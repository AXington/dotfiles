"""Library-first platform resolver (spec section 4.1a, AC-D7, AC-X3).

The :class:`ResolutionWorker` is deliberately I/O-free: it delegates the actual
candidate lookup to an injected ``resolver`` callable. This module provides the
production resolver, a thin, DRY bridge over the same multi-strategy platform
search that ``reconcile`` uses (:func:`tuneshift.reconcile.gather_candidates`),
converting each :class:`~tuneshift.models.TrackResult` into the worker's
:class:`~tuneshift.library.worker.ResolvedCandidate` contract.

Sharing the discovery pass with reconcile is what lets resolution *persist* the
exact candidate set selection will later score over (AC-X3 / AC-P4), instead of
maintaining a second, drift-prone search path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tuneshift.library.worker import ResolutionRateLimited, ResolvedCandidate
from tuneshift.matching.track import MatchScores, score_match_components
from tuneshift.models import capture_candidate_metadata
from tuneshift.platforms.timeout import PlatformTimeout
from tuneshift.reconcile import build_alias_resolver, gather_candidates

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tuneshift.db import Database
    from tuneshift.models import Track, TrackResult

# Platform errors that mean "temporarily throttled" - surfaced to the worker as
# a transient rate-limit so the track is re-queued with backoff, never lost or
# quarantined (AC-X2).
_RATE_LIMIT_MARKERS = ("rate limit", "429", "too many requests")


class PlatformResolver:
    """Resolve a library track to hydrated platform candidates.

    Reuses reconcile's multi-strategy search and returns candidates in DISCOVERY
    order (the order the gather cascade found them). That order is deliberate:
    selection keeps input order for default band-ties, so persisting discovery
    order is what preserves winner parity (AC-P4). Each candidate carries a
    ``match_score`` so the worker can hydrate the track from the strongest
    identity match; the full per-playlist version selection remains
    reconcile/selection's job.
    """

    def __init__(
        self, db: Database, client: object, *, max_candidates: int = 10
    ) -> None:
        self._db = db
        self._client = client
        self._max_candidates = max_candidates
        # Built once per resolver so aliased artists are retrieved + scored under
        # every equivalent surface form (mirrors reconcile).
        self._alias_resolver = build_alias_resolver(db)

    @property
    def platform_name(self) -> str:
        return self._client.platform_name

    def __call__(self, track: Track) -> Sequence[ResolvedCandidate]:
        try:
            candidates, _strategies = gather_candidates(
                track,
                self._client,
                self._alias_resolver,
            )
        except PlatformTimeout as exc:
            # A stalled platform call is transient, not a hard failure: re-queue
            # with backoff so it never erodes the quarantine budget (BUG-4).
            raise ResolutionRateLimited(str(exc)) from exc
        except Exception as exc:
            if _is_rate_limit(exc):
                raise ResolutionRateLimited(str(exc)) from exc
            raise

        if not candidates:
            return []

        all_durations = [c.duration_seconds for c in candidates if c.duration_seconds]

        def _score(candidate: TrackResult) -> MatchScores:
            return score_match_components(
                track.title,
                track.artist,
                track.album,
                candidate.title,
                candidate.artist,
                candidate.album,
                result_duration=candidate.duration_seconds,
                reference_duration=track.duration_seconds,
                all_durations=all_durations,
                cand_explicit=getattr(candidate, "explicit", None),
                alias_resolver=self._alias_resolver,
            )

        platform = self._client.platform_name
        # Preserve DISCOVERY order (do NOT re-sort): the persisted set is fed to
        # selection, which keeps input order for default band-ties, so discovery
        # order is what guarantees winner parity (AC-P4). The per-candidate match
        # score is attached only so the worker can hydrate the track from the
        # single best identity match and derive its confidence tier.
        resolved: list[ResolvedCandidate] = []
        for candidate in candidates[: self._max_candidates]:
            metadata = capture_candidate_metadata(candidate)
            scores = _score(candidate)
            metadata["match_score"] = scores.match_score
            # The gating projection, persisted alongside so the quarantine floor
            # never has to re-derive it from stale stored fields (BUG-13).
            metadata["quality_score"] = scores.quality_score
            resolved.append(
                ResolvedCandidate(
                    platform=platform,
                    platform_track_id=candidate.platform_id,
                    metadata=metadata,
                )
            )
        return resolved


def _is_rate_limit(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)
