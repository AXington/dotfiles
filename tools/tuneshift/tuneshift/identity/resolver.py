"""Track identity resolver: the core resolution pipeline."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Protocol

from tuneshift.identity.confidence import compute_confidence
from tuneshift.identity.models import (
    ConfidenceTier,
    Evidence,
    RecordingCandidate,
    ResolutionResult,
    ResolutionStatus,
    TrackInput,
)

if TYPE_CHECKING:
    from tuneshift.identity.sources.discogs import DiscogsSource
    from tuneshift.identity.sources.musicbrainz import MusicBrainzSource
    from tuneshift.platforms.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CACHE_FRESHNESS_DAYS = 90
CONFIRMED_THRESHOLD = 0.80
VERIFIED_THRESHOLD = 0.95

_TIER_ORDER = {
    "VERIFIED": 4,
    "CONFIRMED": 3,
    "PROBABLE": 2,
    "UNCERTAIN": 1,
}


class IdentityStore(Protocol):
    """Adapter between the resolver and TuneShift's database."""

    def get_resolution_state(
        self, track_id: int
    ) -> tuple[str | None, float | None, str | None]: ...
    def store_resolution(
        self,
        track_id: int,
        mb_recording_id: str | None,
        mb_release_group_id: str | None,
        confidence_tier: str,
        confidence_score: float,
        evidence: list[dict],
        isrc: str | None = None,
    ) -> None: ...
    def store_failed_evidence(self, track_id: int, evidence: list[dict]) -> None: ...
    def get_isrc(self, track_id: int) -> str | None: ...


@dataclass
class ResolverConfig:
    """Configuration for the resolver pipeline."""

    upgrade_mode: bool = False
    force: bool = False
    max_candidates: int = 10


class TrackResolver:
    """4-step pipeline for resolving track identity."""

    def __init__(
        self,
        store: IdentityStore,
        musicbrainz: MusicBrainzSource | None = None,
        discogs: DiscogsSource | None = None,
        config: ResolverConfig | None = None,
        rate_limiters: dict[str, RateLimiter] | None = None,
    ) -> None:
        self._store = store
        self._mb = musicbrainz
        self._discogs = discogs
        self._config = config or ResolverConfig()
        self._rate_limiters = rate_limiters or {}

    def resolve(self, track_id: int, track: TrackInput) -> ResolutionResult:
        """Resolve a single track through the 4-step pipeline."""
        # Step 1: Cache check
        skip_result = self._check_cache(track_id)
        if skip_result is not None:
            return skip_result

        all_evidence: list[Evidence] = []
        best_candidates: list[RecordingCandidate] = []

        # Step 2: ISRC lookup
        if track.isrc and self._mb:
            self._wait_for_rate_limit("musicbrainz")
            result = self._mb.lookup_isrc(track.isrc, duration_ms=track.duration_ms)
            if result:
                best_candidates.extend(result.recordings)
                if result.evidence:
                    all_evidence.append(result.evidence)
                score, _ = compute_confidence(all_evidence)
                if score >= VERIFIED_THRESHOLD and len(result.recordings) == 1:
                    return self._finalize(track_id, result.recordings[0], all_evidence)

        # Step 3: Text search
        if self._mb:
            self._wait_for_rate_limit("musicbrainz")
            result = self._mb.search(
                track.artist, track.title, duration_ms=track.duration_ms
            )
            if result.recordings:
                best_candidates.extend(result.recordings)
                if result.evidence:
                    all_evidence.append(result.evidence)
                score, _ = compute_confidence(all_evidence)
                if score >= CONFIRMED_THRESHOLD:
                    top = sorted(
                        result.recordings, key=lambda c: c.score, reverse=True
                    )[0]
                    return self._finalize(track_id, top, all_evidence)

        # Step 4: Discogs confirmation
        if self._discogs:
            self._wait_for_rate_limit("discogs")
            result = self._discogs.search(track.artist, track.title)
            if result.evidence:
                all_evidence.append(result.evidence)
                score, _ = compute_confidence(all_evidence)
                if score >= CONFIRMED_THRESHOLD and best_candidates:
                    top = sorted(best_candidates, key=lambda c: c.score, reverse=True)[
                        0
                    ]
                    return self._finalize(track_id, top, all_evidence)

        # Final evaluation with all collected evidence
        if all_evidence and best_candidates:
            score, tier = compute_confidence(all_evidence)
            top = sorted(best_candidates, key=lambda c: c.score, reverse=True)[0]
            return self._finalize(track_id, top, all_evidence)

        # Failed to find a confident match this attempt.
        # Check if there's an existing resolution we should preserve.
        existing_tier, existing_score, _ = self._store.get_resolution_state(track_id)

        if all_evidence:
            self._store.store_failed_evidence(
                track_id=track_id,
                evidence=[
                    {
                        "source": e.source,
                        "evidence_type": e.evidence_type,
                        "confidence": e.confidence,
                    }
                    for e in all_evidence
                ],
            )
        else:
            self._store.store_failed_evidence(track_id=track_id, evidence=[])

        if existing_tier is not None:
            # Prior resolution still valid; report unchanged rather than failed
            return ResolutionResult(
                track_id=track_id,
                status=ResolutionStatus.UNCHANGED,
                confidence_tier=ConfidenceTier(existing_tier),
            )

        return ResolutionResult(
            track_id=track_id,
            status=ResolutionStatus.FAILED,
            error="All sources exhausted without confident match",
        )

    def _check_cache(self, track_id: int) -> ResolutionResult | None:
        """Check if track already has a fresh, confident resolution."""
        tier, score, resolved_at = self._store.get_resolution_state(track_id)

        if tier is None:
            return None

        if self._config.force:
            return None

        if resolved_at:
            resolved_dt = datetime.fromisoformat(resolved_at)
            if resolved_dt.tzinfo is None:
                resolved_dt = resolved_dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - resolved_dt
            if age > timedelta(days=CACHE_FRESHNESS_DAYS):
                return None

        required_tier = "VERIFIED" if self._config.upgrade_mode else "CONFIRMED"
        required_score = (
            VERIFIED_THRESHOLD if self._config.upgrade_mode else CONFIRMED_THRESHOLD
        )

        if score is not None:
            if score < required_score:
                return None
        elif _TIER_ORDER.get(tier, 0) < _TIER_ORDER[required_tier]:
            return None

        return ResolutionResult(track_id=track_id, status=ResolutionStatus.SKIPPED)

    def _finalize(
        self,
        track_id: int,
        candidate: RecordingCandidate,
        evidence: list[Evidence],
    ) -> ResolutionResult:
        """Store resolution and return result."""
        score, tier = compute_confidence(evidence)

        old_tier, old_score, _ = self._store.get_resolution_state(track_id)
        if old_tier is not None:
            old_order = _TIER_ORDER.get(old_tier, 0)
            new_order = _TIER_ORDER.get(tier.value, 0)
            status = (
                ResolutionStatus.UPGRADED
                if new_order > old_order
                else ResolutionStatus.UNCHANGED
            )
        else:
            status = ResolutionStatus.RESOLVED

        rg_id = None
        if candidate.release_groups:
            rg_id = candidate.release_groups[0].get("id")

        evidence_dicts = [
            {
                "source": e.source,
                "evidence_type": e.evidence_type,
                "confidence": e.confidence,
                "raw_data": e.raw_data,
            }
            for e in evidence
        ]

        self._store.store_resolution(
            track_id=track_id,
            mb_recording_id=candidate.mb_recording_id,
            mb_release_group_id=rg_id,
            confidence_tier=tier.value,
            confidence_score=score,
            evidence=evidence_dicts,
        )

        return ResolutionResult(
            track_id=track_id,
            status=status,
            mb_recording_id=candidate.mb_recording_id,
            mb_release_group_id=rg_id,
            confidence_score=score,
            confidence_tier=tier,
            evidence=evidence,
        )

    def _wait_for_rate_limit(self, source: str) -> None:
        """Wait if rate-limited for a source."""
        limiter = self._rate_limiters.get(source)
        if limiter and not limiter.acquire():
            wait_time = limiter.wait_time()
            if wait_time > 0:
                time.sleep(wait_time)
