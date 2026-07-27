"""Persistence mixin: platform."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tuneshift.models import (
    PlatformMapping,
)
from tuneshift.persistence.base import (
    PersistenceBase,
)
from tuneshift.persistence.specs import TableSpec
from tuneshift.types import ReviewItem

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class PlatformMixin(PersistenceBase):
    """Platform persistence methods for the Database facade."""

    def upsert_platform_mapping(self, mapping: PlatformMapping) -> None:
        """Insert or update a platform mapping."""
        self.conn.execute(
            """INSERT INTO platform_tracks
               (track_id, platform, platform_track_id, platform_title,
                platform_artist, platform_album, match_score,
                is_divergent, divergence_note, status, user_approved, fingerprint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(track_id, platform) DO UPDATE SET
                 platform_track_id = excluded.platform_track_id,
                 platform_title = excluded.platform_title,
                 platform_artist = excluded.platform_artist,
                 platform_album = excluded.platform_album,
                 match_score = excluded.match_score,
                 is_divergent = excluded.is_divergent,
                 divergence_note = excluded.divergence_note,
                 status = excluded.status,
                 user_approved = excluded.user_approved,
                 fingerprint = COALESCE(excluded.fingerprint, platform_tracks.fingerprint)""",
            (
                mapping.track_id,
                mapping.platform,
                mapping.platform_track_id,
                mapping.platform_title,
                mapping.platform_artist,
                mapping.platform_album,
                mapping.match_score,
                int(mapping.is_divergent),
                mapping.divergence_note,
                mapping.status,
                int(mapping.user_approved),
                mapping.fingerprint,
            ),
        )
        self.conn.commit()

    def save_match_audit(
        self, track_id: int, platform: str, audit, playlist_id: int = 0
    ) -> None:
        """Persist the explainable MatchAudit for a (playlist, track, platform).

        Stored for every reconcile outcome, including misses, so ``tuneshift
        explain`` can explain a decision without re-running a live search. The
        audit is serialized to JSON via ``MatchAudit.to_json``; availability and
        reason_code are also stored as plain columns for cheap filtering.

        ``playlist_id`` scopes the audit: selection is playlist-dependent, so the
        same (track, platform) can carry a distinct audit per playlist. It
        defaults to the global sentinel ``0`` for legacy/non-playlist call sites.
        """
        if audit is None:
            return
        self.conn.execute(
            """INSERT INTO match_audits
               (playlist_id, track_id, platform, availability, reason_code, audit_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(playlist_id, track_id, platform) DO UPDATE SET
                 availability = excluded.availability,
                 reason_code = excluded.reason_code,
                 audit_json = excluded.audit_json,
                 updated_at = excluded.updated_at""",
            (
                playlist_id,
                track_id,
                platform,
                audit.availability,
                audit.reason_code,
                audit.to_json(),
            ),
        )
        self.conn.commit()

    def get_match_audit(self, track_id: int, platform: str, playlist_id: int = 0):
        """Return the persisted ``MatchAudit`` for a (playlist, track, platform).

        ``playlist_id`` defaults to the global sentinel ``0``.
        """
        from tuneshift.matching import MatchAudit

        row = self.conn.execute(
            "SELECT audit_json FROM match_audits "
            "WHERE playlist_id = ? AND track_id = ? AND platform = ?",
            (playlist_id, track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return MatchAudit.from_json(row["audit_json"])

    def get_match_audits_for_track(
        self, track_id: int, playlist_id: int = 0
    ) -> dict[str, object]:
        """Return all persisted audits for a (playlist, track), keyed by platform.

        ``playlist_id`` defaults to the global sentinel ``0``.
        """
        from tuneshift.matching import MatchAudit

        rows = self.conn.execute(
            "SELECT platform, audit_json FROM match_audits "
            "WHERE track_id = ? AND playlist_id = ?",
            (track_id, playlist_id),
        ).fetchall()
        return {
            row["platform"]: MatchAudit.from_json(row["audit_json"]) for row in rows
        }

    def get_review_items(
        self,
        playlist_id: int | None = None,
        platform: str | None = None,
    ) -> list[ReviewItem]:
        """Return per-(playlist, track, platform) review items from stored audits.

        Joins persisted ``match_audits`` to the tracks and the playlists they
        appear in. One item per distinct (playlist, track, platform) so a track
        pinned twice in a playlist is not double-counted, while the same track in
        two playlists is surfaced under each. Callers filter/cluster with
        :func:`tuneshift.matching.cluster_reviews` and
        :func:`tuneshift.matching.compute_burden`.
        """
        sql = """
            SELECT DISTINCT p.id AS playlist_id, p.name AS playlist_name,
                   t.id AS track_id, t.title, t.artist, t.album,
                   ma.platform, ma.availability, ma.reason_code
            FROM match_audits ma
            JOIN tracks t ON t.id = ma.track_id
            JOIN playlist_tracks pt ON pt.track_id = ma.track_id
            JOIN playlists p ON p.id = pt.playlist_id
            WHERE ma.playlist_id IN (pt.playlist_id, 0)
              AND ma.playlist_id = (
                    SELECT MAX(ma2.playlist_id)
                    FROM match_audits ma2
                    WHERE ma2.track_id = ma.track_id
                      AND ma2.platform = ma.platform
                      AND ma2.playlist_id IN (pt.playlist_id, 0)
                  )
        """
        conditions: list[str] = []
        params: list[object] = []
        if playlist_id is not None:
            conditions.append("p.id = ?")
            params.append(playlist_id)
        if platform is not None:
            conditions.append("ma.platform = ?")
            params.append(platform)
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += " ORDER BY p.name, t.artist, t.title"

        rows = self.conn.execute(sql, params).fetchall()
        return [
            ReviewItem(
                track_id=row["track_id"],
                title=row["title"],
                artist=row["artist"],
                album=row["album"],
                platform=row["platform"],
                availability=row["availability"],
                reason_code=row["reason_code"],
                playlist_id=row["playlist_id"],
                playlist_name=row["playlist_name"],
            )
            for row in rows
        ]

    def get_unavailable_track_ids(
        self, playlist_id: int, platform: str = "tidal"
    ) -> list[int]:
        """Return playlist track ids that are unavailable on ``platform``.

        A track counts as unavailable when its persisted ``match_audit`` for the
        platform records availability ``not_found`` (genuinely absent) or
        ``exact_unavailable`` (known but blocked). Tidal is the availability
        source of truth, so it is the default platform.

        Tracks with no audit for the platform are treated as available (not
        excluded): a playlist that has never been reconciled is unaffected. The
        sequencer uses this to keep unavailable tracks from distorting the arc.

        Audits are playlist-scoped: a playlist-specific audit
        (``playlist_id = this playlist``) takes precedence over the legacy
        global sentinel (``playlist_id = 0``), so one playlist's verdict never
        leaks into another.
        """
        rows = self.conn.execute(
            """
            SELECT DISTINCT pt.track_id
            FROM playlist_tracks pt
            JOIN match_audits ma
              ON ma.track_id = pt.track_id AND ma.platform = ?
             AND ma.playlist_id IN (pt.playlist_id, 0)
            WHERE pt.playlist_id = ?
              AND ma.availability IN ('not_found', 'exact_unavailable')
              AND ma.playlist_id = (
                    SELECT MAX(ma2.playlist_id)
                    FROM match_audits ma2
                    WHERE ma2.track_id = pt.track_id
                      AND ma2.platform = ma.platform
                      AND ma2.playlist_id IN (pt.playlist_id, 0)
                  )
            """,
            (platform, playlist_id),
        ).fetchall()
        return [row[0] for row in rows]

    def get_platform_mapping(
        self, track_id: int, platform: str
    ) -> PlatformMapping | None:
        """Get a platform mapping for a track."""
        row = self.conn.execute(
            "SELECT * FROM platform_tracks WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return PlatformMapping(
            track_id=row["track_id"],
            platform=row["platform"],
            platform_track_id=row["platform_track_id"],
            platform_title=row["platform_title"],
            platform_artist=row["platform_artist"],
            platform_album=row["platform_album"],
            match_score=row["match_score"],
            is_divergent=bool(row["is_divergent"]),
            divergence_note=row["divergence_note"],
            status=row["status"],
            user_approved=bool(row["user_approved"]),
            fingerprint=row["fingerprint"],
        )

    def get_platform_mappings_for_tracks(
        self,
        track_ids: list[int],
        platform: str,
    ) -> dict[int, PlatformMapping]:
        """Batch-load platform mappings for multiple tracks."""
        if not track_ids:
            return {}
        placeholders = ",".join("?" for _ in track_ids)
        rows = self.conn.execute(
            f"SELECT * FROM platform_tracks WHERE track_id IN ({placeholders}) AND platform = ?",  # noqa: S608 - placeholders are bound '?' params; values parameterized  # nosec B608
            (*track_ids, platform),
        ).fetchall()
        result: dict[int, PlatformMapping] = {}
        for row in rows:
            result[row["track_id"]] = PlatformMapping(
                track_id=row["track_id"],
                platform=row["platform"],
                platform_track_id=row["platform_track_id"],
                platform_title=row["platform_title"],
                platform_artist=row["platform_artist"],
                platform_album=row["platform_album"],
                match_score=row["match_score"],
                is_divergent=bool(row["is_divergent"]),
                divergence_note=row["divergence_note"],
                status=row["status"],
                user_approved=bool(row["user_approved"]),
                fingerprint=row["fingerprint"],
            )
        return result

    def set_platform_mapping(
        self,
        track_id: int,
        platform: str,
        platform_track_id: str,
        user_approved: bool = False,
        platform_title: str | None = None,
        platform_artist: str | None = None,
        platform_album: str | None = None,
        match_score: int | None = None,
    ) -> None:
        """Set or update a platform mapping for a track."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO platform_tracks
                   (track_id, platform, platform_track_id, platform_title, platform_artist,
                    platform_album, match_score, user_approved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, platform) DO UPDATE SET
                   platform_track_id = excluded.platform_track_id,
                   platform_title = excluded.platform_title,
                   platform_artist = excluded.platform_artist,
                   platform_album = excluded.platform_album,
                   match_score = excluded.match_score,
                   user_approved = excluded.user_approved""",
                (
                    track_id,
                    platform,
                    platform_track_id,
                    platform_title,
                    platform_artist,
                    platform_album,
                    match_score,
                    int(user_approved),
                ),
            )

    def delete_platform_mapping(self, track_id: int, platform: str) -> None:
        """Remove a platform mapping for a track."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM platform_tracks WHERE track_id = ? AND platform = ?",
                (track_id, platform),
            )

    def link_platform_playlist(
        self,
        playlist_id: int,
        platform: str,
        platform_playlist_id: str,
        *,
        commit: bool = True,
    ) -> None:
        """Link a canonical playlist to a platform playlist.

        Uses ON CONFLICT DO UPDATE rather than INSERT OR REPLACE so re-linking an
        already-synced playlist preserves ``last_synced_at`` (INSERT OR REPLACE
        deletes the row and resets the timestamp to NULL, making status report
        "never synced" for a genuinely-synced playlist -- BUG-6b).

        ``commit=False`` links within a caller-managed transaction (e.g. the
        plan/apply push) so the link is journaled and reversible with the push.
        """
        self.conn.execute(
            """INSERT INTO platform_playlists (playlist_id, platform, platform_playlist_id)
               VALUES (?, ?, ?)
               ON CONFLICT(playlist_id, platform) DO UPDATE SET
               platform_playlist_id = excluded.platform_playlist_id""",
            (playlist_id, platform, platform_playlist_id),
        )
        if commit:
            self.conn.commit()

    def get_linked_platforms(self, playlist_id: int) -> list[str]:
        """Return platform names linked to this playlist."""
        rows = self.conn.execute(
            "SELECT platform FROM platform_playlists WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
        return [row["platform"] for row in rows]

    def get_platform_playlist_id(self, playlist_id: int, platform: str) -> str | None:
        """Get the platform-specific playlist ID."""
        row = self.conn.execute(
            "SELECT platform_playlist_id FROM platform_playlists WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        ).fetchone()
        return row["platform_playlist_id"] if row else None

    def get_platform_track_ids(
        self, platform: str, *, approved_only: bool = False
    ) -> list[int]:
        """Return track ids mapped on a platform, ordered by track_id.

        ``approved_only`` restricts to rows with ``user_approved = 1``.
        """
        if approved_only:
            rows = self.conn.execute(
                "SELECT track_id FROM platform_tracks "
                "WHERE platform = ? AND user_approved = 1 ORDER BY track_id",
                (platform,),
            )
        else:
            rows = self.conn.execute(
                "SELECT track_id FROM platform_tracks WHERE platform = ? "
                "ORDER BY track_id",
                (platform,),
            )
        return [row["track_id"] for row in rows]

    def get_platform_tracks_columns(self) -> list[str]:
        """Return the platform_tracks column names in schema order."""
        return [
            row[1] for row in self.conn.execute("PRAGMA table_info(platform_tracks)")
        ]

    def get_platform_tracks_for_track(self, track_id: int) -> list[sqlite3.Row]:
        """Return every platform_tracks row for a track (all columns)."""
        return self.conn.execute(
            "SELECT * FROM platform_tracks WHERE track_id = ?", (track_id,)
        ).fetchall()

    def read_spec_columns(
        self,
        spec: TableSpec,
        columns: Sequence[str],
        pk_values: dict[str, Any],
    ) -> sqlite3.Row | None:
        """SELECT ``columns`` from a spec table for one primary-key tuple.

        ``columns`` must be a subset of ``spec.all_columns`` (guarded here as
        defense in depth; callers already derive them from the spec).
        """
        unknown = set(columns) - set(spec.all_columns)
        if unknown:
            raise ValueError(f"Columns {sorted(unknown)} not in spec for {spec.name!r}")
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        col_list = ", ".join(columns)
        return self.conn.execute(
            f"SELECT {col_list} FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized  # nosec B608
            tuple(pk_values[col] for col in spec.pk),
        ).fetchone()

    def insert_spec_row(
        self, spec: TableSpec, proposed: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Insert a spec row, upserting on the primary key.

        Only columns present in ``proposed`` are written; on PK conflict the
        non-PK supplied columns are updated (or DO NOTHING when none remain).
        """
        cols = [c for c in spec.all_columns if c in proposed]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in spec.pk)
        pk_list = ", ".join(spec.pk)
        conflict = (
            f" ON CONFLICT({pk_list}) DO UPDATE SET {updates}"  # nosec B608
            if updates
            else f" ON CONFLICT({pk_list}) DO NOTHING"
        )
        sql = f"INSERT INTO {spec.name} ({col_list}) VALUES ({placeholders}){conflict}"  # noqa: S608 - identifiers from spec allowlist; values parameterized  # nosec B608
        self.conn.execute(sql, tuple(proposed[c] for c in cols))
        if commit:
            self.conn.commit()

    def update_spec_row(
        self, spec: TableSpec, proposed: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Update only the supplied non-PK columns of an existing spec row."""
        set_cols = [c for c in spec.columns if c in proposed]
        if not set_cols:
            return
        assignments = ", ".join(f"{c} = ?" for c in set_cols)
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        params = [proposed[c] for c in set_cols] + [proposed[col] for col in spec.pk]
        self.conn.execute(
            f"UPDATE {spec.name} SET {assignments} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized  # nosec B608
            tuple(params),
        )
        if commit:
            self.conn.commit()

    def delete_spec_row(
        self, spec: TableSpec, pk_values: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Delete a spec row identified by its primary-key tuple."""
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        self.conn.execute(
            f"DELETE FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized  # nosec B608
            tuple(pk_values[col] for col in spec.pk),
        )
        if commit:
            self.conn.commit()

    def mark_playlist_synced(self, playlist_id: int, platform: str) -> None:
        """Record that a playlist was successfully pushed to a platform.

        Call this ONLY after the platform push has succeeded, so the stored
        sync timestamp never claims a playlist is mirrored when the push failed.
        """
        self.conn.execute(
            "UPDATE platform_playlists SET last_synced_at = datetime('now') "
            "WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        )
        self.conn.commit()

    def get_last_synced(self, playlist_id: int, platform: str) -> str | None:
        """Return the last successful push timestamp, or None if never synced."""
        row = self.conn.execute(
            "SELECT last_synced_at FROM platform_playlists WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        ).fetchone()
        return row["last_synced_at"] if row else None

    def upsert_track_platform_metadata(
        self, track_id: int, platform: str, platform_track_id: str, **fields
    ) -> None:
        """Insert or update platform metadata for a track."""
        import json as _json

        # Serialize JSON fields
        for key in ("genres", "audio_qualities", "raw_metadata"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = _json.dumps(fields[key])

        existing = self.conn.execute(
            "SELECT id FROM track_platform_metadata WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()

        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = [*fields.values(), track_id, platform]
            self.conn.execute(
                f"UPDATE track_platform_metadata SET {sets}, fetched_at = datetime('now') "  # noqa: S608 - columns from code-controlled allowlist; values parameterized  # nosec B608
                f"WHERE track_id = ? AND platform = ?",
                vals,
            )
        else:
            cols = ["track_id", "platform", "platform_track_id", *fields.keys()]
            placeholders = ", ".join("?" * len(cols))
            vals = [track_id, platform, platform_track_id, *fields.values()]
            self.conn.execute(
                f"INSERT INTO track_platform_metadata ({', '.join(cols)}) VALUES ({placeholders})",  # noqa: S608 - columns from code-controlled allowlist; values parameterized  # nosec B608
                vals,
            )
        self.conn.commit()

    def get_track_platform_metadata(self, track_id: int, platform: str) -> dict | None:
        """Get platform metadata for a track."""
        import json as _json

        row = self.conn.execute(
            "SELECT * FROM track_platform_metadata WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()
        if not row:
            return None
        cols = [
            d[1]
            for d in self.conn.execute(
                "PRAGMA table_info(track_platform_metadata)"
            ).fetchall()
        ]
        result = dict(zip(cols, row, strict=True))
        for key in ("genres", "audio_qualities", "raw_metadata"):
            if result.get(key) and isinstance(result[key], str):
                try:
                    result[key] = _json.loads(result[key])
                except _json.JSONDecodeError:
                    pass
        return result
