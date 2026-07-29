"""Resumable resolution/enrichment worker (spec sections 4.1a, 4.3; AC-D7, AC-X2).

Library-first add lands tracks immediately and defers the (rate-limited,
network-bound) work of resolving a track to platform candidates. This worker is
the resumable engine that drains that backlog. It is deliberately I/O-free: the
actual candidate lookup is an injected ``resolver`` callable, so the *same* code
path serves the background drain and a foreground ``resolve`` command, and unit
tests never touch the network.

Failure handling (AC-X2 "rate-limited work is never lost"):

* ``ResolutionRateLimited`` -> the track stays ``pending`` with exponential
  backoff and an incremented attempt count. Rate limits are transient and never
  cause quarantine.
* any other exception -> retried with backoff up to ``max_attempts``, after
  which the track is quarantined with a machine-readable reason.
* resolver returns no candidates -> the track is quarantined immediately
  (nothing to resolve to).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from tuneshift.db import Database
from tuneshift.identity.models import ConfidenceTier
from tuneshift.models import Track

logger = logging.getLogger(__name__)

# SQLite's datetime('now') format; next_attempt_at must be string-comparable
# with it (see Database.next_pending_resolution).
_SQLITE_TS_FMT = "%Y-%m-%d %H:%M:%S"

# BUG-3: a best-candidate match_score below this floor is not a confident match
# (the resolver found only wrong candidates). Such tracks are quarantined for
# review rather than hydrated with a misleading low tier. Locked tracks are
# exempt. 50/100 maps to confidence_score 0.50 (mid-UNCERTAIN).
RESOLVE_ACCEPT_FLOOR = 50.0


class ResolutionRateLimited(Exception):
    """Raised by a resolver when an upstream platform rate-limits the request.

    Distinct from a hard failure: the work is re-queued with backoff and is
    never counted toward the quarantine attempt ceiling.
    """


@dataclass(frozen=True)
class ResolvedCandidate:
    """A hydrated platform candidate produced by a resolver."""

    platform: str
    platform_track_id: str
    metadata: dict[str, Any] | None = field(default=None)


# A resolver takes a library Track and returns the platform candidates it found.
Resolver = Callable[[Track], Sequence[ResolvedCandidate]]


def cand_platform(candidates: Sequence[ResolvedCandidate]) -> str | None:
    """Platform of a candidate set (all share one platform), or None if empty."""
    return candidates[0].platform if candidates else None


def _best_candidate(candidates: Sequence[ResolvedCandidate]) -> ResolvedCandidate:
    """The highest ``match_score`` candidate, the strongest identity match.

    Candidates are persisted in discovery order (for selection parity), so the
    best hydration source is chosen by score here rather than by position.
    """

    def _score(candidate: ResolvedCandidate) -> float:
        value = (candidate.metadata or {}).get("match_score")
        return float(value) if isinstance(value, (int, float)) else -1.0

    return max(candidates, key=_score)


def _floor_score(candidate: ResolvedCandidate) -> float | None:
    """The score the acceptance floor judges: quality, else the legacy ranking.

    Candidate rows persisted before BUG-13 carry only ``match_score``, which
    still has preference penalties baked in. Falling back to it keeps the floor
    firing on the existing library; treating a missing ``quality_score`` as "no
    quality concern" would silently disable the quarantine gate for every
    already-stored candidate, which is worse than the bug being fixed. Those
    rows recover their headroom when the track is next re-resolved.
    """
    metadata = candidate.metadata or {}
    for key in ("quality_score", "match_score"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


class ResolutionWorker:
    """Drains the resolution queue by resolving tracks to platform candidates."""

    def __init__(
        self,
        db: Database,
        resolver: Resolver,
        *,
        enricher: Callable[[Database, Track], None] | None = None,
        rate_limiter: Any | None = None,
        max_attempts: int = 5,
        base_backoff_seconds: float = 60.0,
    ) -> None:
        self._db = db
        self._resolver = resolver
        self._enricher = enricher
        self._rate_limiter = rate_limiter
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds

    def enqueue(self, track_id: int) -> None:
        """Queue a track for resolution. Idempotent per track."""
        self._db.enqueue_resolution(track_id)

    def drain(self, limit: int | None = None) -> int:
        """Resolve due tracks until the queue is drained or ``limit`` is hit.

        Returns the number of tracks *successfully* resolved. Backed-off tracks
        (rate-limited or awaiting retry) are skipped this pass and remain queued,
        which is what makes a subsequent drain resumable.
        """
        resolved = 0
        processed = 0
        while limit is None or processed < limit:
            track_id = self._db.next_pending_resolution()
            if track_id is None:
                break
            processed += 1
            if self._resolve_one(track_id):
                resolved += 1
        return resolved

    def resolve_tracks(self, track_ids: Sequence[int], *, force: bool = False) -> int:
        """Resolve a specific set of tracks now, in order.

        Unlike :meth:`drain` (which processes the whole due backlog FIFO), this
        resolves exactly the requested tracks so a scoped ``resolve <playlist>``
        or ``resolve --track`` never touches unrelated queued work. Each track is
        (re-)enqueued first so a prior quarantine is reopened (a user asking to
        resolve is a retry signal), then resolved immediately regardless of
        backoff, the backoff timer only governs the unattended drain loop.

        Already-``resolved`` tracks are skipped unless ``force`` is set, so a
        routine re-run is cheap and never needlessly re-hits the network.
        Returns the number of tracks successfully resolved this call.
        """
        resolved = 0
        for track_id in track_ids:
            state = self._db.get_resolution_queue_state(track_id)
            if state == "resolved" and not force:
                continue
            self.enqueue(track_id)
            if force:
                # enqueue only reopens quarantined rows; force a resolved row
                # back to pending so it is actually re-resolved.
                self._db.set_resolution_state(track_id, "pending", last_error=None)
            if self._resolve_one(track_id):
                resolved += 1
        return resolved

    def _resolve_one(self, track_id: int) -> bool:
        """Resolve a single track. Returns True only on success."""
        track = self._db.get_track(track_id)
        if track is None:
            # Track vanished (deleted) after enqueue; drop it from the queue.
            self._db.set_resolution_state(
                track_id, "quarantined", last_error="track_missing"
            )
            return False

        if self._rate_limiter is not None:
            self._rate_limiter.wait()

        try:
            candidates = list(self._resolver(track))
        except ResolutionRateLimited as exc:
            # Transient: re-queue with backoff, never quarantine. Uses a SEPARATE
            # transient counter so throttling can never erode the hard-failure
            # quarantine budget (AC-D7).
            self._db.set_resolution_state(
                track_id,
                "pending",
                last_error=str(exc),
                next_attempt_at=self._backoff_at(track_id, transient=True),
                increment_transient=True,
            )
            logger.info("resolution rate-limited, backing off: track=%s", track_id)
            return False
        except Exception as exc:  # noqa: BLE001
            return self._handle_failure(track_id, exc)

        if not candidates:
            self._quarantine(track_id, "no_candidate: no platform match found")
            return False

        candidates = list(candidates)
        best = _best_candidate(candidates)
        platform = cand_platform(candidates)
        # BUG-5: a user-approved lock owns the track's identity; it is exempt from
        # the tier recompute and the acceptance floor below.
        locked = (
            platform is not None
            and self._db.get_effective_lock(track_id, platform) is not None
        )
        # Persist the discovered candidate set BEFORE any floor decision (BUG-8):
        # a below-floor quarantine used to discard what the search found, leaving
        # zero rows in track_candidates so triage/explain could not show why the
        # track scored low or whether the result was a transient bad match.
        # Persisting first makes the quarantine diagnosable and a re-resolve still
        # replaces the set cleanly. Replace any prior set so re-resolve never
        # leaves stale rows/ranks behind, then persist in discovery order.
        self._db.clear_track_candidates(track_id, platform)
        for rank, cand in enumerate(candidates):
            self._db.upsert_track_candidate(
                track_id,
                cand.platform,
                cand.platform_track_id,
                cand.metadata,
                discovery_rank=rank,
            )
        # BUG-3: if the strongest candidate scores below the acceptance floor, the
        # resolver found nothing confident (only wrong candidates, e.g. other
        # songs by the same artist, or a same-title different-artist hard-reject).
        # Quarantine for review instead of hydrating a misleading low tier. The
        # persisted candidates above remain so the quarantine can be inspected.
        # BUG-13: gate on the QUALITY projection. This floor asks "did the
        # resolver find the recording at all?", which a listener preference
        # cannot answer: a song that only ever had a clean release must not be
        # quarantined for lacking an explicit alternative it could never have
        # had. Ranking below still uses match_score, so the preference keeps
        # deciding which candidate wins.
        best_score = _floor_score(best)
        if (
            not locked
            and isinstance(best_score, (int, float))
            and best_score < RESOLVE_ACCEPT_FLOOR
        ):
            self._quarantine(
                track_id,
                f"no_confident_match: best score {best_score:.0f} "
                f"< {RESOLVE_ACCEPT_FLOOR:.0f}",
            )
            return False
        # Hydrate the track's core identity metadata from the BEST-scoring
        # candidate (discovery order != score order). Fill-NULL semantics live in
        # the DB method, so a prior user edit is never clobbered and re-resolving
        # is idempotent. This is what makes "resolved" mean populated, not just
        # tagged (spec AC-D2: tier=CONFIRMED but isrc/duration/album all NULL).
        # BUG-5: never overwrite a locked track's confidence tier on re-resolve.
        self._hydrate_track(track_id, best, preserve_tier=locked)
        self._db.set_resolution_state(track_id, "resolved", last_error=None)
        # Clear any prior quarantine now that the track resolved.
        if track.quarantine_state:
            self._db.set_track_fields(
                track_id,
                {"quarantine_state": None, "quarantine_reason": None},
                source="resolver",
            )
        # Async enrichment (AC-D7): classification + artist genres happen here,
        # out of the interactive add path. Failures are non-fatal.
        if self._enricher is not None:
            try:
                self._enricher(self._db, track)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "enrichment failed after resolve: track=%s",
                    track_id,
                    exc_info=True,
                )
        return True

    def _hydrate_track(
        self,
        track_id: int,
        candidate: ResolvedCandidate,
        *,
        preserve_tier: bool = False,
    ) -> None:
        """Promote the best candidate's core metadata onto the track.

        Delegates the fill-NULL / provenance bookkeeping to
        :meth:`Database.hydrate_identity_metadata`; here we just translate the
        candidate's captured metadata into that call and derive a confidence
        tier from the resolver's match score. When ``preserve_tier`` is set (the
        track carries a user-approved lock, BUG-5), the tier/score are left
        untouched: passing ``None`` makes ``hydrate_identity_metadata`` skip the
        confidence columns, so the locked track keeps the tier it already had.
        """
        meta = candidate.metadata or {}
        confidence_tier: str | None = None
        confidence_score: float | None = None
        if not preserve_tier:
            match_score = meta.get("match_score")
            if isinstance(match_score, (int, float)):
                confidence_score = max(0.0, min(1.0, match_score / 100.0))
                confidence_tier = ConfidenceTier.from_score(confidence_score).value

        self._db.hydrate_identity_metadata(
            track_id,
            isrc=meta.get("isrc"),
            duration_seconds=meta.get("duration_seconds"),
            album=meta.get("album"),
            confidence_tier=confidence_tier,
            confidence_score=confidence_score,
            source="resolver",
        )

    def _handle_failure(self, track_id: int, exc: Exception) -> bool:
        """Retry a hard failure with backoff; quarantine once retries exhaust."""
        attempts = self._db.get_resolution_attempts(track_id) + 1
        if attempts >= self._max_attempts:
            self._quarantine(
                track_id,
                f"max_attempts_exceeded: {type(exc).__name__}: {exc}",
                increment_attempts=True,
            )
            return False
        self._db.set_resolution_state(
            track_id,
            "pending",
            last_error=f"{type(exc).__name__}: {exc}",
            next_attempt_at=self._backoff_at(track_id, attempts=attempts),
            increment_attempts=True,
        )
        logger.warning(
            "resolution failed (attempt %s/%s): track=%s error=%s",
            attempts,
            self._max_attempts,
            track_id,
            exc,
        )
        return False

    def _quarantine(
        self, track_id: int, reason: str, *, increment_attempts: bool = False
    ) -> None:
        self._db.set_resolution_state(
            track_id,
            "quarantined",
            last_error=reason,
            increment_attempts=increment_attempts,
        )
        self._db.set_track_fields(
            track_id,
            {"quarantine_state": "unresolved", "quarantine_reason": reason},
            source="resolver",
        )
        logger.warning("track quarantined: track=%s reason=%s", track_id, reason)

    def _backoff_at(
        self, track_id: int, attempts: int | None = None, *, transient: bool = False
    ) -> str:
        """Compute an exponential-backoff timestamp string (SQLite format).

        For a rate-limit backoff (``transient=True``) the escalation is driven by
        the separate ``transient_attempts`` counter, keeping transient throttling
        independent of the hard-failure quarantine budget.
        """
        if attempts is None:
            attempts = (
                self._db.get_resolution_attempts(track_id, transient=transient) + 1
            )
        delay = self._base_backoff_seconds * (2 ** max(0, attempts - 1))
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime(
            _SQLITE_TS_FMT
        )
