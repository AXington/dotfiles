"""Persistence mixin: resolution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from tuneshift.models import (
    Track,
)
from tuneshift.persistence.base import (
    PersistenceBase,
)

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


# Metadata keys whose values contribute to the keyword-search haystack.
_SEARCH_KEYWORD_KEYS = (
    "vibes",
    "era_mood",
    "lastfm_tags",
    "lyrical_subject",
    "narrator_stance",
    "sonic_texture",
    "space",
    "groove_feel",
    "opens_with",
    "closes_with",
    "energy_arc_within",
)


def _search_intensity_ok(
    metadata: dict, track: Track, intensity_range: tuple[float, float] | None
) -> bool:
    """Return True when the track's emotional intensity falls in range.

    Uses ``emotional_intensity`` metadata, falling back to ``track.energy``.
    A missing or non-numeric value fails a bounded search (returns False).
    """
    if intensity_range is None:
        return True
    raw = metadata.get("emotional_intensity", track.energy)
    if raw is None:
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    minimum, maximum = intensity_range
    return minimum <= value <= maximum


def _search_stance_ok(metadata: dict, normalized_stance: str | None) -> bool:
    """Return True when the track's narrator stance matches (case-insensitive)."""
    if normalized_stance is None:
        return True
    stance = metadata.get("narrator_stance")
    return isinstance(stance, str) and stance.casefold() == normalized_stance


def _search_haystack(metadata: dict, track: Track) -> str:
    """Build the casefolded text blob a keyword search matches against."""
    terms: list[str] = [track.title, track.artist]
    if track.album:
        terms.append(track.album)
    terms.extend(track.themes)
    for key in _SEARCH_KEYWORD_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            terms.extend(str(item) for item in value if item)
        elif value:
            terms.append(str(value))
    return " ".join(terms).casefold()


class ResolutionMixin(PersistenceBase):
    """Resolution-queue and candidate persistence for the Database facade."""

    def get_resolution_state(
        self,
        track_id: int,
    ) -> tuple[str | None, float | None, str | None]:
        """Get the current resolution state of a track."""
        row = self.conn.execute(
            "SELECT confidence_tier, confidence_score, resolved_at FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            return None, None, None
        return row["confidence_tier"], row["confidence_score"], row["resolved_at"]

    def store_resolution(
        self,
        track_id: int,
        mb_recording_id: str | None,
        mb_release_group_id: str | None,
        confidence_tier: str,
        confidence_score: float,
        evidence: list[dict],
        isrc: str | None = None,
    ) -> None:
        """Store a successful resolution result."""

        now = datetime.now(timezone.utc).isoformat()

        with self.conn:
            new_evidence_ids = []
            for evidence_row in evidence:
                cursor = self.conn.execute(
                    """INSERT INTO evidence (track_id, source, evidence_type, confidence, raw_data, is_current)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        track_id,
                        evidence_row["source"],
                        evidence_row["evidence_type"],
                        evidence_row["confidence"],
                        evidence_row.get("raw_data"),
                    ),
                )
                new_evidence_ids.append(cursor.lastrowid)

            anchor_id = new_evidence_ids[0] if new_evidence_ids else None
            if anchor_id is not None:
                placeholders = ",".join("?" for _ in new_evidence_ids)
                self.conn.execute(
                    f"UPDATE evidence SET is_current = 0, superseded_by = ? WHERE track_id = ? AND is_current = 1 AND id NOT IN ({placeholders})",  # noqa: S608 - placeholders are bound '?' params; values parameterized  # nosec B608
                    (anchor_id, track_id, *new_evidence_ids),
                )

            update_sql = """UPDATE tracks SET
                mb_recording_id = ?,
                mb_release_group_id = ?,
                confidence_tier = ?,
                confidence_score = ?,
                resolved_at = ?"""
            params: list[object] = [
                mb_recording_id,
                mb_release_group_id,
                confidence_tier,
                confidence_score,
                now,
            ]
            if isrc is not None:
                update_sql += ", isrc = ?"
                params.append(isrc)
            update_sql += " WHERE id = ?"
            params.append(track_id)
            self.conn.execute(update_sql, params)

    def store_failed_evidence(self, track_id: int, evidence: list[dict]) -> None:
        """Store evidence from a failed resolution attempt."""
        with self.conn:
            for evidence_row in evidence:
                self.conn.execute(
                    """INSERT INTO evidence (track_id, source, evidence_type, confidence, raw_data, is_current)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        track_id,
                        evidence_row["source"],
                        evidence_row["evidence_type"],
                        evidence_row["confidence"],
                        evidence_row.get("raw_data"),
                    ),
                )

    def enqueue_resolution(
        self, track_id: int, next_attempt_at: str | None = None
    ) -> None:
        """Enqueue a track for resolution/enrichment.

        Idempotent per track. Re-enqueuing a track that previously QUARANTINED
        reopens it for another attempt (a re-add/re-import is a user signal to
        retry) and resets its counters; a ``pending`` or ``resolved`` row is left
        untouched so already-resolved work is never needlessly redone.
        """
        with self.conn:
            self.conn.execute(
                """INSERT INTO resolution_queue (track_id, state, next_attempt_at)
                   VALUES (?, 'pending', ?)
                   ON CONFLICT(track_id) DO UPDATE SET
                       state = 'pending',
                       attempts = 0,
                       transient_attempts = 0,
                       last_error = NULL,
                       next_attempt_at = excluded.next_attempt_at,
                       updated_at = datetime('now')
                   WHERE resolution_queue.state = 'quarantined'""",
                (track_id, next_attempt_at),
            )

    def approve_resolution(self, track_id: int) -> None:
        """Manually approve/release a quarantined track (AC-D6).

        Clears the quarantine on BOTH sources of truth in one transaction: the
        track's ``quarantine_state`` (which drives selectability) and the
        ``resolution_queue`` row (which drives coverage). Marking the queue row
        ``resolved`` keeps ``coverage_report`` consistent with
        ``get_quarantined_tracks``, an approved track counts as resolved, never
        lingering as quarantined.
        """
        with self.conn:
            self.conn.execute(
                """UPDATE resolution_queue
                   SET state = 'resolved', last_error = NULL,
                       next_attempt_at = NULL, updated_at = datetime('now')
                   WHERE track_id = ?""",
                (track_id,),
            )
            self.conn.execute(
                "UPDATE tracks SET quarantine_state = NULL, "
                "quarantine_reason = NULL WHERE id = ?",
                (track_id,),
            )

    def get_resolution_queue_state(self, track_id: int) -> str | None:
        """Return the resolution_queue state for a track, or None if unqueued.

        Distinct from :meth:`get_resolution_state` (which reads the track's
        identity confidence): this reflects the worker's queue lifecycle
        (pending/resolved/quarantined) that drives the drain loop and coverage.
        """
        row = self.conn.execute(
            "SELECT state FROM resolution_queue WHERE track_id = ?", (track_id,)
        ).fetchone()
        return row["state"] if row else None

    def next_pending_resolution(self) -> int | None:
        """Return the next track_id whose resolution work is due, or None.

        Due means state='pending' and (no backoff set, or backoff has elapsed).
        Ordered by enqueue time so the queue drains FIFO.
        """
        row = self.conn.execute(
            """SELECT track_id FROM resolution_queue
               WHERE state = 'pending'
                 AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now'))
               ORDER BY enqueued_at
               LIMIT 1"""
        ).fetchone()
        return int(row["track_id"]) if row else None

    def set_resolution_state(
        self,
        track_id: int,
        state: str,
        *,
        last_error: str | None = None,
        next_attempt_at: str | None = None,
        increment_attempts: bool = False,
        increment_transient: bool = False,
    ) -> None:
        """Update a queued track's resolution state (and optional backoff/error).

        ``increment_attempts`` bumps the hard-failure counter that drives the
        quarantine ceiling. ``increment_transient`` bumps a SEPARATE counter used
        only for rate-limit backoff, transient throttling must never consume the
        quarantine budget (AC-D7).
        """
        set_clauses = ["state = ?", "last_error = ?", "updated_at = datetime('now')"]
        params: list[Any] = [state, last_error]
        if next_attempt_at is not None:
            set_clauses.append("next_attempt_at = ?")
            params.append(next_attempt_at)
        if increment_attempts:
            set_clauses.append("attempts = attempts + 1")
        if increment_transient:
            set_clauses.append("transient_attempts = transient_attempts + 1")
        with self.conn:
            self.conn.execute(
                f"UPDATE resolution_queue SET {', '.join(set_clauses)} WHERE track_id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized  # nosec B608
                (*params, track_id),
            )

    def upsert_track_candidate(
        self,
        track_id: int,
        platform: str,
        platform_track_id: str,
        captured_metadata: dict[str, Any] | None,
        discovery_rank: int = 0,
    ) -> None:
        """Insert or update a hydrated platform candidate for a track.

        ``discovery_rank`` records the candidate's position in the discovery
        order so :meth:`get_track_candidates` can return the set in the same
        order the live gather produced, selection keeps input order for default
        band-ties, so preserving it is what guarantees winner parity (AC-P4).
        """
        payload = (
            json.dumps(captured_metadata) if captured_metadata is not None else None
        )
        with self.conn:
            self.conn.execute(
                """INSERT INTO track_candidates
                       (track_id, platform, platform_track_id, captured_metadata,
                        discovery_rank, fetched_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(track_id, platform, platform_track_id)
                   DO UPDATE SET captured_metadata = excluded.captured_metadata,
                                 discovery_rank = excluded.discovery_rank,
                                 fetched_at = excluded.fetched_at""",
                (track_id, platform, platform_track_id, payload, discovery_rank),
            )

    def clear_track_candidates(
        self, track_id: int, platform: str | None = None
    ) -> None:
        """Remove persisted candidates for a track (optionally one platform).

        Called before persisting a fresh candidate set so a refresh REPLACES the
        prior set rather than leaving stale rows (and stale ranks) behind.
        """
        with self.conn:
            if platform is None:
                self.conn.execute(
                    "DELETE FROM track_candidates WHERE track_id = ?", (track_id,)
                )
            else:
                self.conn.execute(
                    "DELETE FROM track_candidates WHERE track_id = ? AND platform = ?",
                    (track_id, platform),
                )

    def get_track_candidates(
        self, track_id: int, platform: str | None = None
    ) -> list[dict[str, Any]]:
        """Return hydrated candidates for a track, optionally filtered by platform.

        Ordered by ``discovery_rank`` (then a stable id tiebreak) so callers see
        the persisted set in the original discovery order, the ordering
        selection relies on for default band-tie parity (AC-P4).
        """
        query = "SELECT * FROM track_candidates WHERE track_id = ?"
        params: list[Any] = [track_id]
        if platform is not None:
            query += " AND platform = ?"
            params.append(platform)
        query += " ORDER BY discovery_rank, platform, platform_track_id"
        rows = self.conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "track_id": row["track_id"],
                    "platform": row["platform"],
                    "platform_track_id": row["platform_track_id"],
                    "captured_metadata": (
                        json.loads(row["captured_metadata"])
                        if row["captured_metadata"]
                        else None
                    ),
                    "discovery_rank": row["discovery_rank"],
                    "fetched_at": row["fetched_at"],
                }
            )
        return result

    def get_resolution_attempts(self, track_id: int, *, transient: bool = False) -> int:
        """Return the stored (hard or transient) attempt count for a queued track.

        Returns 0 when the track has no resolution_queue row. ``transient`` reads
        the rate-limit backoff counter instead of the hard-failure counter.
        """
        counter = "transient_attempts" if transient else "attempts"
        row = self.conn.execute(
            f"SELECT {counter} AS n FROM resolution_queue WHERE track_id = ?",  # noqa: S608 - counter is a code-controlled literal; value parameterized  # nosec B608
            (track_id,),
        ).fetchone()
        return int(row["n"]) if row else 0
