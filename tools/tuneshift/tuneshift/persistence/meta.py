"""Persistence mixin: meta."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tuneshift.models import (
    EffectiveLock,
)
from tuneshift.persistence.base import (
    PersistenceBase,
)
from tuneshift.types import JournalEntry

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class MetaMixin(PersistenceBase):
    """Meta persistence methods for the Database facade."""

    def record_journal_entry(
        self,
        *,
        plan_id: str,
        table_name: str,
        row_key: str,
        op: str,
        prior_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
    ) -> None:
        """Record one applied write so the plan can be reversed (AC-P4).

        ``prior_value``/``new_value`` are JSON-serialized row snapshots; either
        may be ``None`` (insert has no prior, delete has no new).
        """
        with self.conn:
            self.conn.execute(
                """INSERT INTO apply_journal
                       (plan_id, table_name, row_key, op, prior_value, new_value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    table_name,
                    row_key,
                    op,
                    json.dumps(prior_value) if prior_value is not None else None,
                    json.dumps(new_value) if new_value is not None else None,
                ),
            )

    def get_journal_entries(self, plan_id: str) -> list[JournalEntry]:
        """Return a plan's journal entries newest-first (reverse-replay order)."""
        rows = self.conn.execute(
            """SELECT id, plan_id, table_name, row_key, op,
                      prior_value, new_value, applied_at
               FROM apply_journal WHERE plan_id = ? ORDER BY id DESC""",
            (plan_id,),
        ).fetchall()
        return [
            JournalEntry(
                id=row["id"],
                plan_id=row["plan_id"],
                table_name=row["table_name"],
                row_key=row["row_key"],
                op=row["op"],
                prior_value=json.loads(row["prior_value"])
                if row["prior_value"]
                else None,
                new_value=json.loads(row["new_value"]) if row["new_value"] else None,
                applied_at=row["applied_at"],
            )
            for row in rows
        ]

    def has_journal(self, plan_id: str) -> bool:
        """Whether any journal entries exist for ``plan_id``."""
        row = self.conn.execute(
            "SELECT 1 FROM apply_journal WHERE plan_id = ? LIMIT 1", (plan_id,)
        ).fetchone()
        return row is not None

    def clear_journal(self, plan_id: str) -> None:
        """Remove a plan's journal entries (after a successful rollback)."""
        with self.conn:
            self.conn.execute("DELETE FROM apply_journal WHERE plan_id = ?", (plan_id,))

    _COVERAGE_FIELDS = (
        "isrc",
        "duration_seconds",
        "album_artist",
        "album_type",
        "label",
        "release_date",
        "audio_modes",
    )

    def coverage_report(self) -> dict[str, Any]:
        """Return backfill coverage and per-field fill rates.

        Coverage uses the AC-D1 denominator ``resolved / (resolved +
        quarantined)``, ``pending`` tracks are excluded so an in-progress
        backfill does not depress the number, and quarantined tracks stay in the
        denominator so quarantine cannot game the floor.
        """
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS c FROM resolution_queue GROUP BY state"
        ).fetchall()
        counts = {row["state"]: row["c"] for row in rows}
        resolved = counts.get("resolved", 0)
        quarantined = counts.get("quarantined", 0)
        pending = counts.get("pending", 0)
        denom = resolved + quarantined
        coverage = (resolved / denom) if denom else 0.0

        total = self.conn.execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]
        fill: dict[str, float] = {}
        for column in self._COVERAGE_FIELDS:
            if not total:
                fill[column] = 0.0
                continue
            filled = self.conn.execute(
                f"SELECT COUNT(*) AS c FROM tracks "  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                f"WHERE {column} IS NOT NULL AND {column} != ''"
            ).fetchone()["c"]
            fill[column] = filled / total

        return {
            "resolved": resolved,
            "quarantined": quarantined,
            "pending": pending,
            "coverage": coverage,
            "total_tracks": total,
            "field_fill_rates": fill,
        }

    def resolution_status_summary(self) -> dict[str, Any]:
        """Partition the library for the ``resolve --status`` headline.

        Categories are mutually exclusive and sum to ``total`` (quarantine wins
        over a stale tier): ``playable`` (a usable, available mapping),
        ``quarantined`` (unavailable on platform), and ``unresolved`` (no tier
        yet), the last split into tracks that are in a playlist (actionable) vs
        orphaned/no-playlist (library cleanup). Also returns the resolved-tier
        breakdown and a quarantine-reason histogram bucketed by reason prefix.
        """
        total = self.conn.execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]
        quarantined = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks WHERE quarantine_state IS NOT NULL"
        ).fetchone()["c"]
        playable = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks "
            "WHERE confidence_tier IS NOT NULL AND quarantine_state IS NULL"
        ).fetchone()["c"]
        unresolved_in_playlist = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks t "
            "WHERE t.confidence_tier IS NULL AND t.quarantine_state IS NULL "
            "AND EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id)"
        ).fetchone()["c"]
        unresolved_orphaned = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks t "
            "WHERE t.confidence_tier IS NULL AND t.quarantine_state IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id)"
        ).fetchone()["c"]

        tier_rows = self.conn.execute(
            "SELECT confidence_tier AS tier, COUNT(*) AS c FROM tracks "
            "WHERE confidence_tier IS NOT NULL AND quarantine_state IS NULL "
            "GROUP BY confidence_tier"
        ).fetchall()
        tiers = {row["tier"]: row["c"] for row in tier_rows}

        reason_rows = self.conn.execute(
            """SELECT CASE
                        WHEN instr(quarantine_reason, ':') > 0
                          THEN substr(quarantine_reason, 1, instr(quarantine_reason, ':') - 1)
                        ELSE COALESCE(quarantine_reason, 'unknown')
                      END AS bucket,
                      COUNT(*) AS c
               FROM tracks WHERE quarantine_state IS NOT NULL
               GROUP BY bucket ORDER BY c DESC, bucket"""
        ).fetchall()
        quarantine_reasons = [(row["bucket"], row["c"]) for row in reason_rows]

        return {
            "total": total,
            "playable": playable,
            "quarantined": quarantined,
            "unresolved_in_playlist": unresolved_in_playlist,
            "unresolved_orphaned": unresolved_orphaned,
            "playable_pct": (playable / total) if total else 0.0,
            "tiers": tiers,
            "quarantine_reasons": quarantine_reasons,
        }

    def set_playlist_track_mapping(
        self,
        playlist_id: int,
        track_id: int,
        platform: str,
        platform_track_id: str,
        *,
        source: str | None = None,
        user_approved: bool = False,
    ) -> None:
        """Set the per-playlist platform release for a track (upsert on PK)."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO playlist_track_mappings
                       (playlist_id, track_id, platform, platform_track_id,
                        source, user_approved, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(playlist_id, track_id, platform)
                   DO UPDATE SET platform_track_id = excluded.platform_track_id,
                                 source = excluded.source,
                                 user_approved = excluded.user_approved,
                                 updated_at = excluded.updated_at""",
                (
                    playlist_id,
                    track_id,
                    platform,
                    platform_track_id,
                    source,
                    1 if user_approved else 0,
                ),
            )

    def get_playlist_track_mapping(
        self, playlist_id: int, track_id: int, platform: str
    ) -> dict[str, Any] | None:
        """Return the per-playlist release override for a track, or None."""
        row = self.conn.execute(
            """SELECT * FROM playlist_track_mappings
               WHERE playlist_id = ? AND track_id = ? AND platform = ?""",
            (playlist_id, track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return {
            "playlist_id": row["playlist_id"],
            "track_id": row["track_id"],
            "platform": row["platform"],
            "platform_track_id": row["platform_track_id"],
            "source": row["source"],
            "user_approved": bool(row["user_approved"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_effective_lock(
        self, track_id: int, platform: str, playlist_id: int | None = None
    ) -> EffectiveLock | None:
        """Resolve the effective identity lock for a track on a platform (AC-L1/L4).

        A lock is two-level: a per-playlist override (``playlist_track_mappings``
        with ``user_approved=1``) takes precedence over the library-wide default
        lock (``platform_tracks`` with ``user_approved=1``). Returns ``None`` when
        neither level is locked. The result carries the composite identity
        (platform-id + ISRC + fingerprint) so selection can honour the lock even
        after a platform re-ID.

        Only an ``user_approved`` mapping is a lock; an auto-matched (unapproved)
        per-playlist row does NOT shadow a global lock, it falls through to the
        global default.
        """
        track = self.get_track(track_id)
        isrc = track.isrc if track is not None else None
        global_mapping = self.get_platform_mapping(track_id, platform)

        if playlist_id is not None:
            pl = self.get_playlist_track_mapping(playlist_id, track_id, platform)
            if pl is not None and pl["user_approved"] and pl["platform_track_id"]:
                # A per-playlist override reuses the global mapping's fingerprint
                # only when it points at the SAME release; otherwise the override
                # is a distinct release and carries no cached fingerprint yet.
                fingerprint = None
                if (
                    global_mapping is not None
                    and global_mapping.platform_track_id == pl["platform_track_id"]
                ):
                    fingerprint = global_mapping.fingerprint
                return EffectiveLock(
                    platform_track_id=pl["platform_track_id"],
                    scope="playlist",
                    isrc=isrc,
                    fingerprint=fingerprint,
                    status="matched",
                )

        if (
            global_mapping is not None
            and global_mapping.user_approved
            and global_mapping.platform_track_id
        ):
            return EffectiveLock(
                platform_track_id=global_mapping.platform_track_id,
                scope="global",
                isrc=isrc,
                fingerprint=global_mapping.fingerprint,
                status=global_mapping.status or "matched",
                is_divergent=global_mapping.is_divergent,
                divergence_note=global_mapping.divergence_note,
                match_score=global_mapping.match_score,
            )
        return None

    def get_global_locks(self) -> list[dict]:
        """All library-wide default locks (``platform_tracks`` ``user_approved=1``).

        Returns one dict per (track, platform) lock with the track title/artist
        for display, ordered by track then platform. Drives ``locks list``
        (AC-CLI4) at global scope.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id, pt.platform, pt.platform_track_id, pt.status,
                      t.title, t.artist
               FROM platform_tracks pt
               JOIN tracks t ON t.id = pt.track_id
               WHERE pt.user_approved = 1 AND pt.platform_track_id != ''
               ORDER BY t.title, pt.platform"""
        ).fetchall()
        return [
            {
                "track_id": r["track_id"],
                "platform": r["platform"],
                "platform_track_id": r["platform_track_id"],
                "status": r["status"],
                "title": r["title"],
                "artist": r["artist"],
            }
            for r in rows
        ]

    def get_playlist_locks(self, playlist_id: int) -> list[dict]:
        """All per-playlist override locks for a playlist (``user_approved=1``).

        Returns one dict per (track, platform) override lock. These win over the
        global default for the playlist; ``locks list --playlist`` renders both
        layers with precedence (AC-CLI4).
        """
        rows = self.conn.execute(
            """SELECT m.track_id, m.platform, m.platform_track_id,
                      t.title, t.artist
               FROM playlist_track_mappings m
               JOIN tracks t ON t.id = m.track_id
               WHERE m.playlist_id = ? AND m.user_approved = 1
                     AND m.platform_track_id != ''
               ORDER BY t.title, m.platform""",
            (playlist_id,),
        ).fetchall()
        return [
            {
                "track_id": r["track_id"],
                "platform": r["platform"],
                "platform_track_id": r["platform_track_id"],
                "title": r["title"],
                "artist": r["artist"],
            }
            for r in rows
        ]

    def set_playlist_track_pref(
        self,
        playlist_id: int | None,
        track_id: int,
        criterion: str,
        strength: str,
        target: str | None = None,
    ) -> None:
        """Upsert one preference at the ``(playlist_id, track_id, criterion,
        target)`` scope.

        A ``None`` ``playlist_id`` denotes a playlist-agnostic per-track
        preference (it applies to the track on every playlist). Keying on
        ``target`` (not just ``criterion``) is what lets multiple targets coexist
        on one axis, e.g. ``content avoid karaoke`` and ``content avoid
        instrumental``, instead of the second overwriting the first. Re-setting
        the same ``(scope, criterion, target)`` replaces its strength in place.

        Uses a NULL-safe delete-then-insert (``IS`` matches NULL) rather than
        ``ON CONFLICT`` so the ``COALESCE`` unique index and the NULLable
        ``playlist_id`` are both honoured.
        """
        with self.conn:
            self.conn.execute(
                """DELETE FROM playlist_track_prefs
                   WHERE playlist_id IS ? AND track_id = ?
                     AND criterion = ? AND target IS ?""",
                (playlist_id, track_id, criterion, target),
            )
            self.conn.execute(
                """INSERT INTO playlist_track_prefs
                       (playlist_id, track_id, criterion, strength, target)
                   VALUES (?, ?, ?, ?, ?)""",
                (playlist_id, track_id, criterion, strength, target),
            )

    def get_playlist_track_prefs(
        self, playlist_id: int | None, track_id: int
    ) -> list[dict[str, Any]]:
        """Return the preferences stored at exactly this scope for the track.

        ``playlist_id=None`` returns the playlist-agnostic per-track rows (see
        :meth:`get_track_global_prefs`); a concrete id returns only that
        playlist's own rows (NULL rows are a distinct scope and never leak in).
        """
        rows = self.conn.execute(
            """SELECT criterion, strength, target FROM playlist_track_prefs
               WHERE playlist_id IS ? AND track_id = ?
               ORDER BY criterion, target""",
            (playlist_id, track_id),
        ).fetchall()
        return [
            {
                "criterion": r["criterion"],
                "strength": r["strength"],
                "target": r["target"],
            }
            for r in rows
        ]

    def get_track_global_prefs(self, track_id: int) -> list[dict[str, Any]]:
        """Return the playlist-agnostic per-track preferences (NULL playlist).

        These apply to the track on every playlist and form the folded successor
        to the retired ``tracks.preferences`` blob (FL3 decision #4).
        """
        return self.get_playlist_track_prefs(None, track_id)

    def remove_playlist_track_pref(
        self,
        playlist_id: int | None,
        track_id: int,
        criterion: str,
        target: str | None = None,
    ) -> bool:
        """Delete preference(s) for a criterion at the given scope.

        With ``target`` omitted, every target on the criterion is removed; with a
        ``target`` given, only that exact ``(criterion, target)`` row. Returns
        True if at least one row was deleted. NULL-safe on ``playlist_id``.
        """
        with self.conn:
            if target is None:
                cur = self.conn.execute(
                    """DELETE FROM playlist_track_prefs
                       WHERE playlist_id IS ? AND track_id = ? AND criterion = ?""",
                    (playlist_id, track_id, criterion),
                )
            else:
                cur = self.conn.execute(
                    """DELETE FROM playlist_track_prefs
                       WHERE playlist_id IS ? AND track_id = ?
                         AND criterion = ? AND target IS ?""",
                    (playlist_id, track_id, criterion, target),
                )
        return cur.rowcount > 0

    def set_global_preferences(self, prefs: dict | None) -> None:
        """Set the account-wide default preferences (schema_meta key/value)."""
        if prefs:
            self.conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('global_preferences', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(prefs),),
            )
        else:
            self.conn.execute(
                "DELETE FROM schema_meta WHERE key = 'global_preferences'"
            )
        self.conn.commit()

    def get_global_preferences(self) -> dict | None:
        """Get the account-wide default preferences, or None if unset."""
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'global_preferences'"
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None
