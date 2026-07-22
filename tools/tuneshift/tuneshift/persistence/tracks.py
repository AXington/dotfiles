"""Persistence mixin: tracks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from tuneshift.models import (
    Track,
)
from tuneshift.persistence.base import (
    _TRACK_EDITABLE_COLUMNS,
    _TRACK_FIRST_CLASS_COLUMNS,
    _TRACK_JSON_FIELD_COLUMNS,
    PersistenceBase,
    normalize_artist,
    normalize_title,
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


class TracksMixin(PersistenceBase):
    """Tracks persistence methods for the Database facade."""

    def insert_track(self, track: Track) -> int:
        """Insert a track and return its ID.

        Links the track to the normalized ``artists``/``albums`` tables at insert
        time (get-or-create), so every runtime-added track carries ``artist_id`` and
        (when an album is present) ``album_id``, not only tracks touched by the
        one-time migration backfill. Gate on AC-D1/AC-D3.
        """
        artist_id: int | None = None
        album_id: int | None = None
        if track.artist:
            artist_id = self._get_or_create_artist(track.artist)
            if track.album:
                album_id = self._get_or_create_album(track.album, artist_id)
        cursor = self.conn.execute(
            """INSERT INTO tracks (
                   title, artist, album, norm_title, norm_artist, norm_album,
                   duration_seconds, isrc, energy, valence, tempo, key, themes, metadata,
                   artist_id, album_id
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track.title,
                track.artist,
                track.album,
                normalize_title(track.title),
                normalize_artist(track.artist),
                normalize_title(track.album) if track.album else None,
                track.duration_seconds,
                track.isrc,
                track.energy,
                track.valence,
                track.tempo,
                track.key,
                json.dumps(track.themes) if track.themes else None,
                json.dumps(track.metadata) if track.metadata else None,
                artist_id,
                album_id,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_track(self, track: Track) -> int:
        """Insert a track and return its ID."""
        return self.insert_track(track)

    def update_track(self, track_id: int, **fields: str | None) -> int:
        """Update editable identity fields, recomputing normalized columns.

        Only ``title``, ``artist`` and ``album`` may be edited. Normalized
        lookup columns are recomputed for every changed field so identity
        matching stays consistent, and each change is recorded in
        ``track_edits`` for an audit trail. Returns the number of fields
        that actually changed.
        """
        invalid = set(fields) - _TRACK_EDITABLE_COLUMNS
        if invalid:
            raise ValueError(f"Cannot edit track fields: {sorted(invalid)}")

        track = self.get_track(track_id)
        if track is None:
            raise ValueError(f"Track id not found: {track_id}")

        current = {"title": track.title, "artist": track.artist, "album": track.album}
        changes = {k: v for k, v in fields.items() if v != current.get(k)}
        if not changes:
            return 0

        set_clauses: list[str] = []
        params: list[str | None] = []
        for field, value in changes.items():
            # field is constrained to the allowlist above, so interpolation is safe.
            set_clauses.append(f"{field} = ?")
            params.append(value)
            if field == "title":
                set_clauses.append("norm_title = ?")
                params.append(normalize_title(value))
            elif field == "artist":
                set_clauses.append("norm_artist = ?")
                params.append(normalize_artist(value) if value else None)
            elif field == "album":
                set_clauses.append("norm_album = ?")
                params.append(normalize_title(value) if value else None)
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )
            for field, value in changes.items():
                self.conn.execute(
                    "INSERT INTO track_edits (track_id, field, old_value, new_value) "
                    "VALUES (?, ?, ?, ?)",
                    (track_id, field, current.get(field), value),
                )
        return len(changes)

    def get_track_edits(self, track_id: int) -> list[dict]:
        """Return the recorded edit history for a track, newest first."""
        rows = self.conn.execute(
            "SELECT field, old_value, new_value, edited_at FROM track_edits "
            "WHERE track_id = ? ORDER BY id DESC",
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_track(self, track_id: int) -> Track | None:
        """Fetch a track by ID."""
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_track(row)

    def find_track(self, title: str, artist: str, album: str | None) -> Track | None:
        """Find a track by identity using indexed normalized columns."""
        norm_title = normalize_title(title)
        norm_artist = normalize_artist(artist)
        norm_album = normalize_title(album) if album else None

        if norm_title is None:
            return None

        if norm_album:
            row = self.conn.execute(
                "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ? AND norm_album = ?",
                (norm_title, norm_artist, norm_album),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ? AND norm_album IS NULL",
                (norm_title, norm_artist),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_track(row)

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

    def get_isrc(self, track_id: int) -> str | None:
        """Get the ISRC for a track."""
        row = self.conn.execute(
            "SELECT isrc FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return row["isrc"] if row else None

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
        from datetime import datetime, timezone

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
                    f"""UPDATE evidence
                       SET is_current = 0, superseded_by = ?
                       WHERE track_id = ? AND is_current = 1 AND id NOT IN ({placeholders})""",  # noqa: S608 - placeholders are bound '?' params; values parameterized
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

    def find_unresolved(self, below_tier: str | None = None) -> list[Track]:
        """Find tracks that still need identity resolution."""
        tier_order = {"VERIFIED": 4, "CONFIRMED": 3, "PROBABLE": 2, "UNCERTAIN": 1}

        if below_tier is None:
            rows = self.conn.execute(
                "SELECT * FROM tracks WHERE confidence_tier IS NULL"
            ).fetchall()
        else:
            threshold = tier_order.get(below_tier, 0)
            tiers_below = [
                tier for tier, order in tier_order.items() if order < threshold
            ]
            if not tiers_below:
                rows = self.conn.execute(
                    "SELECT * FROM tracks WHERE confidence_tier IS NULL"
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in tiers_below)
                rows = self.conn.execute(
                    f"SELECT * FROM tracks WHERE confidence_tier IS NULL OR confidence_tier IN ({placeholders})",  # noqa: S608 - placeholders are bound '?' params; values parameterized
                    tiers_below,
                ).fetchall()

        return [self._row_to_track(row) for row in rows]

    def find_orphaned_tracks(self) -> list[Track]:
        """Tracks invisible to every review surface (BUG-3 / FEAT-3).

        Orphaned == no confidence_tier, no quarantine_state, no resolution_queue
        entry, AND no platform_tracks row at all. "No platform mapping" here means
        the strict case (zero rows): a track reconciled at least once (any
        platform_tracks row, even unavailable/substitute) is NOT orphaned; its
        availability is a separate triage/quarantine concern. This strict
        definition matches the real orphans (tracks added but never resolved),
        which are invisible to both ``triage`` (quarantine-only) and a user who
        never runs ``resolve --all``.
        """
        rows = self.conn.execute(
            """
            SELECT t.* FROM tracks t
            WHERE t.confidence_tier IS NULL
              AND t.quarantine_state IS NULL
              AND NOT EXISTS (SELECT 1 FROM resolution_queue rq WHERE rq.track_id = t.id)
              AND NOT EXISTS (SELECT 1 FROM platform_tracks pt WHERE pt.track_id = t.id)
            ORDER BY t.id
            """
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def find_tracks_by_playlist(self, playlist_id: int) -> list[Track]:
        """Find all tracks in a playlist."""
        return self.get_playlist_tracks(playlist_id)

    def find_tracks_by_title_artist(self, title: str, artist: str) -> list[Track]:
        """Find tracks by title and artist using normalized columns."""
        norm_title = normalize_title(title)
        norm_artist = normalize_artist(artist)
        if norm_title is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ?",
            (norm_title, norm_artist),
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def search_tracks_by_metadata(
        self,
        intensity_range: tuple[float, float] | None = None,
        stance: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
    ) -> list[Track]:
        """Search tracks using metadata-backed narrative attributes."""
        rows = self.conn.execute(
            "SELECT * FROM tracks ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        normalized_stance = stance.casefold() if stance else None
        normalized_keywords = {
            keyword.casefold().strip()
            for keyword in (keywords or [])
            if keyword and keyword.strip()
        }
        matches: list[tuple[int, Track]] = []

        for row in rows:
            track = self._row_to_track(row)
            metadata = track.metadata or {}

            if not _search_intensity_ok(metadata, track, intensity_range):
                continue
            if not _search_stance_ok(metadata, normalized_stance):
                continue

            overlap_count = 0
            if normalized_keywords:
                haystack = _search_haystack(metadata, track)
                overlap_count = sum(
                    1 for keyword in normalized_keywords if keyword in haystack
                )
                if overlap_count == 0:
                    continue

            matches.append((overlap_count, track))

        matches.sort(
            key=lambda item: (
                item[0],
                item[1].metadata.get("classification_confidence", 0.0),
                item[1].id or 0,
            ),
            reverse=True,
        )
        return [track for _, track in matches[:limit]]

    def merge_tracks(self, keep_id: int, merge_ids: list[int]) -> None:
        """Merge duplicate track rows into a canonical row.

        For each id in ``merge_ids``: reassign its playlist memberships and pins
        to ``keep_id`` (deduplicating within a playlist), delete its auxiliary
        rows (metadata, tags), then delete the track row itself. Runs in a
        single transaction so a failure leaves the database unchanged.

        Playlist positions are rewritten contiguously; the offset technique
        avoids transient UNIQUE(playlist_id, position) collisions during
        reassignment.
        """
        conn = self.conn
        with conn:
            for mid in merge_ids:
                if mid == keep_id:
                    continue
                playlists = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT playlist_id FROM playlist_tracks WHERE track_id = ?",
                        (mid,),
                    ).fetchall()
                ]
                for pid in playlists:
                    # Transfer pins where possible; UNIQUE conflicts (keep already
                    # pinned) are ignored and cleaned up by the cascade below.
                    conn.execute(
                        "UPDATE OR IGNORE playlist_pins SET track_id = ? "
                        "WHERE playlist_id = ? AND track_id = ?",
                        (keep_id, pid, mid),
                    )
                    keep_present = conn.execute(
                        "SELECT 1 FROM playlist_tracks WHERE playlist_id = ? "
                        "AND track_id = ? LIMIT 1",
                        (pid, keep_id),
                    ).fetchone()
                    if keep_present:
                        # Avoid a duplicate membership: drop the merge rows.
                        conn.execute(
                            "DELETE FROM playlist_tracks WHERE playlist_id = ? "
                            "AND track_id = ?",
                            (pid, mid),
                        )
                    else:
                        conn.execute(
                            "UPDATE playlist_tracks SET track_id = ? "
                            "WHERE playlist_id = ? AND track_id = ?",
                            (keep_id, pid, mid),
                        )
                    # Reindex positions contiguously without PK collisions.
                    conn.execute(
                        "UPDATE playlist_tracks SET position = position + 1000000 "
                        "WHERE playlist_id = ?",
                        (pid,),
                    )
                    rows = conn.execute(
                        "SELECT rowid FROM playlist_tracks WHERE playlist_id = ? "
                        "ORDER BY position",
                        (pid,),
                    ).fetchall()
                    for idx, (rowid,) in enumerate(rows):
                        conn.execute(
                            "UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                            (idx, rowid),
                        )
                # Remove auxiliary rows that lack ON DELETE CASCADE.
                conn.execute(
                    "DELETE FROM track_platform_metadata WHERE track_id = ?", (mid,)
                )
                conn.execute("DELETE FROM track_tags WHERE track_id = ?", (mid,))
                # Delete the track; cascade removes remaining platform_tracks,
                # playlist_tracks, playlist_pins, and evidence rows.
                conn.execute("DELETE FROM tracks WHERE id = ?", (mid,))

    def set_track_fields(
        self, track_id: int, fields: dict[str, Any], source: str
    ) -> None:
        """Set first-class metadata columns on a track and record provenance.

        ``fields`` keys must be in ``_TRACK_FIRST_CLASS_COLUMNS``. Each updated
        field records ``{"source": source, "at": <utc iso>}`` in the
        ``field_provenance`` JSON column (AC-D4), merged with any existing
        provenance so successive enrichment passes accumulate rather than clobber.
        List-valued columns (audio_modes) are JSON-serialized.
        """
        invalid = set(fields) - _TRACK_FIRST_CLASS_COLUMNS
        if invalid:
            raise ValueError(f"Cannot set track fields: {sorted(invalid)}")
        if not fields:
            return

        row = self.conn.execute(
            "SELECT field_provenance FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Track id not found: {track_id}")
        provenance = (
            json.loads(row["field_provenance"]) if row["field_provenance"] else {}
        )

        now = datetime.now(timezone.utc).isoformat()
        set_clauses: list[str] = []
        params: list[Any] = []
        for column, value in fields.items():
            # column is constrained to the allowlist above - safe to interpolate.
            set_clauses.append(f"{column} = ?")
            if column in _TRACK_JSON_FIELD_COLUMNS:
                params.append(json.dumps(value) if value is not None else None)
            else:
                params.append(value)
            provenance[column] = {"source": source, "at": now}
        set_clauses.append("field_provenance = ?")
        params.append(json.dumps(provenance))
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
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
                f"UPDATE resolution_queue SET {', '.join(set_clauses)} WHERE track_id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
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

    def hydrate_identity_metadata(
        self,
        track_id: int,
        *,
        isrc: str | None = None,
        duration_seconds: int | None = None,
        album: str | None = None,
        confidence_tier: str | None = None,
        confidence_score: float | None = None,
        mb_recording_id: str | None = None,
        mb_release_group_id: str | None = None,
        source: str = "resolver",
    ) -> dict[str, Any]:
        """Promote a resolved candidate's core identity metadata onto the track.

        This is the single source of truth for turning a "resolved" verdict into
        populated ``tracks`` columns (spec AC-D2). It is deliberately conservative:

        * ``isrc``/``duration_seconds``/``album`` use **fill-NULL** semantics,
          a field is written only when the track's current value is NULL/empty,
          so a prior user edit or an earlier higher-signal hydration is never
          clobbered. Idempotent: re-running promotes nothing new.
        * ``confidence_tier``/``confidence_score`` and the MusicBrainz ids are the
          resolver's to own, so they are updated whenever provided, and
          ``resolved_at`` is stamped so the track drops out of ``find_unresolved``.

        Provenance for each *newly filled* column is recorded in
        ``field_provenance`` (AC-D4) so downstream passes can see the source.
        Returns the map of columns actually written (empty when a no-op).
        """
        row = self.conn.execute(
            "SELECT isrc, duration_seconds, album, field_provenance "
            "FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Track id not found: {track_id}")

        written: dict[str, Any] = {}
        # Fill-NULL only: never overwrite a value the track already holds.
        if isrc and not row["isrc"]:
            written["isrc"] = isrc
        if duration_seconds and not row["duration_seconds"]:
            written["duration_seconds"] = duration_seconds
        if album and not row["album"]:
            written["album"] = album
            written["norm_album"] = normalize_title(album)

        provenance = (
            json.loads(row["field_provenance"]) if row["field_provenance"] else {}
        )
        now = datetime.now(timezone.utc).isoformat()
        for column in written:
            if column == "norm_album":
                continue
            provenance[column] = {"source": source, "at": now}

        set_clauses: list[str] = [f"{col} = ?" for col in written]
        params: list[Any] = list(written.values())

        # Resolution owns identity confidence + MB linkage; refresh when provided.
        if confidence_tier is not None:
            set_clauses.append("confidence_tier = ?")
            params.append(confidence_tier)
        if confidence_score is not None:
            set_clauses.append("confidence_score = ?")
            params.append(confidence_score)
        if mb_recording_id is not None:
            set_clauses.append("mb_recording_id = ?")
            params.append(mb_recording_id)
        if mb_release_group_id is not None:
            set_clauses.append("mb_release_group_id = ?")
            params.append(mb_release_group_id)

        if not set_clauses:
            return {}

        if confidence_tier is not None or confidence_score is not None:
            set_clauses.append("resolved_at = ?")
            params.append(now)
        set_clauses.append("field_provenance = ?")
        params.append(json.dumps(provenance))
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )
        return written

    def get_quarantined_tracks(self) -> list[dict[str, Any]]:
        """List quarantined tracks with machine-readable reasons (AC-D6)."""
        rows = self.conn.execute(
            """SELECT t.id, t.title, t.artist, t.quarantine_reason,
                      rq.last_error
               FROM tracks t
               LEFT JOIN resolution_queue rq ON rq.track_id = t.id
               WHERE t.quarantine_state IS NOT NULL
               ORDER BY t.artist, t.title"""
        ).fetchall()
        return [
            {
                "track_id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "reason": row["quarantine_reason"] or row["last_error"] or "",
            }
            for row in rows
        ]

    def get_selectable_track_ids(self, playlist_id: int) -> list[int]:
        """Return a playlist's track ids that are eligible for selection.

        Quarantined tracks (``quarantine_state`` set) are excluded until they
        are resolved or manually approved (AC-D6). Order is preserved.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id
               FROM playlist_tracks pt
               JOIN tracks t ON t.id = pt.track_id
               WHERE pt.playlist_id = ?
                 AND t.quarantine_state IS NULL
               ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def update_track_metadata(self, track_id: int, meta: dict) -> None:
        """Update a track's audio metadata fields from enrichment data."""
        # Remap LLM field name to internal field name
        if "confidence" in meta and "classification_confidence" not in meta:
            meta["classification_confidence"] = meta.pop("confidence")
        updates = []
        params = []
        if "tempo" in meta:
            updates.append("tempo = ?")
            params.append(meta["tempo"])
        if "key" in meta:
            updates.append("key = ?")
            params.append(meta["key"])
        # The explicit "key in meta and meta[key]" form is kept over dict.get():
        # callers (and tests) may pass mapping-like objects whose __contains__ is
        # authoritative, so collapsing to .get() would change truthiness semantics.
        if "duration_seconds" in meta and meta["duration_seconds"]:  # noqa: RUF019
            updates.append("duration_seconds = ?")
            params.append(meta["duration_seconds"])
        if "isrc" in meta and meta["isrc"]:  # noqa: RUF019
            updates.append("isrc = ?")
            params.append(meta["isrc"])
        # Store extra fields in metadata JSON
        _METADATA_KEYS = (
            "key_scale",
            "energy",
            "valence",
            "themes",
            "vibes",
            "instruments",
            "density",
            "era_mood",
            "emotional_intensity",
            "lyrical_subject",
            "narrator_stance",
            "sonic_texture",
            "space",
            "groove_feel",
            "opens_with",
            "closes_with",
            "energy_arc_within",
            "classification_confidence",
        )
        track = self.get_track(track_id)
        if track:
            existing_meta = dict(track.metadata) if track.metadata else {}
            for k in _METADATA_KEYS:
                if k in meta:
                    existing_meta[k] = meta[k]
            if existing_meta != (track.metadata or {}):
                updates.append("metadata = ?")
                import json as _json

                params.append(_json.dumps(existing_meta))
        if updates:
            params.append(track_id)
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(updates)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                params,
            )
            self.conn.commit()

    def all_track_ids(self) -> list[int]:
        """Return every track id in the library."""
        return [row[0] for row in self.conn.execute("SELECT id FROM tracks")]

    def get_resolution_attempts(self, track_id: int, *, transient: bool = False) -> int:
        """Return the stored (hard or transient) attempt count for a queued track.

        Returns 0 when the track has no resolution_queue row. ``transient`` reads
        the rate-limit backoff counter instead of the hard-failure counter.
        """
        counter = "transient_attempts" if transient else "attempts"
        row = self.conn.execute(
            f"SELECT {counter} AS n FROM resolution_queue WHERE track_id = ?",  # noqa: S608 - counter is a code-controlled literal; value parameterized
            (track_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_track_title(self, track_id: int) -> str | None:
        """Return a track's title, or None if no such track exists."""
        row = self.conn.execute(
            "SELECT title FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return row["title"] if row else None

    def set_track_metadata(
        self, track_id: int, metadata: dict, *, commit: bool = False
    ) -> None:
        """Overwrite a track's full metadata JSON blob.

        Distinct from :meth:`update_track_metadata`, which remaps individual
        audio fields; this writes the entire ``metadata`` column verbatim.
        """
        self.conn.execute(
            "UPDATE tracks SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), track_id),
        )
        if commit:
            self.conn.commit()

    def add_track_tag(self, track_id: int, tag: str, source: str = "manual") -> None:
        """Add a tag to a track."""
        self.conn.execute(
            "INSERT OR IGNORE INTO track_tags (track_id, tag, source) VALUES (?, ?, ?)",
            (track_id, tag, source),
        )
        self.conn.commit()

    def remove_track_tag(self, track_id: int, tag: str) -> bool:
        """Remove a tag from a track."""
        cursor = self.conn.execute(
            "DELETE FROM track_tags WHERE track_id = ? AND tag = ?",
            (track_id, tag),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_track_tags(self, track_id: int) -> list[str]:
        """Get all tags for a track."""
        rows = self.conn.execute(
            "SELECT tag FROM track_tags WHERE track_id = ? ORDER BY tag",
            (track_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def find_tracks_by_tag(self, *tags: str) -> list:
        """Find tracks matching ALL given tags."""
        if not tags:
            return []
        placeholders = ", ".join("?" * len(tags))
        rows = self.conn.execute(
            f"SELECT t.* FROM tracks t "  # noqa: S608 - placeholders are bound '?' params; values parameterized
            f"WHERE (SELECT COUNT(*) FROM track_tags tt WHERE tt.track_id = t.id AND tt.tag IN ({placeholders})) = ?",
            [*tags, len(tags)],
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def list_all_track_tags(self) -> list[tuple[str, int]]:
        """List all tags with usage counts."""
        rows = self.conn.execute(
            "SELECT tag, COUNT(*) FROM track_tags GROUP BY tag ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
