"""Persistence mixin: playlists."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tuneshift.models import (
    Playlist,
    PlaylistPin,
    Track,
)
from tuneshift.persistence.base import (
    PersistenceBase,
)

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class PlaylistsMixin(PersistenceBase):
    """Playlists persistence methods for the Database facade."""

    def create_playlist(self, name: str, description: str | None = None) -> int:
        """Create a playlist and return its ID."""
        cursor = self.conn.execute(
            "INSERT INTO playlists (name, description) VALUES (?, ?)",
            (name, description),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_playlists(self) -> list[Playlist]:
        """List all playlists."""
        rows = self.conn.execute("SELECT * FROM playlists ORDER BY name").fetchall()
        return [self._row_to_playlist(row) for row in rows]

    def set_auto_reorder(
        self, playlist_id: int, enabled: bool, arc: str = "wave"
    ) -> None:
        """Enable or disable auto-reorder for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET auto_reorder = ?, reorder_arc = ?, updated_at = datetime('now') WHERE id = ?",
            (int(enabled), arc, playlist_id),
        )
        self.conn.commit()

    def set_narrative(self, playlist_id: int, narrative: str | None) -> None:
        """Set the intended narrative arc description for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET narrative = ?, updated_at = datetime('now') WHERE id = ?",
            (narrative, playlist_id),
        )
        self.conn.commit()

    def get_narrative(self, playlist_id: int) -> str | None:
        """Get the intended narrative arc description for a playlist."""
        row = self.conn.execute(
            "SELECT narrative FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_pin(
        self,
        playlist_id: int,
        track_id: int,
        pin_type: str,
        group_id: str | None = None,
        group_order: int | None = None,
    ) -> None:
        """Pin a track in a playlist (opener, closer, or anchor group)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO playlist_pins
               (playlist_id, track_id, pin_type, group_id, group_order)
               VALUES (?, ?, ?, ?, ?)""",
            (playlist_id, track_id, pin_type, group_id, group_order),
        )
        self.conn.commit()

    def remove_pin(self, playlist_id: int, track_id: int) -> None:
        """Remove a pin from a playlist."""
        self.conn.execute(
            "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        self.conn.commit()

    def get_pins(self, playlist_id: int) -> list[PlaylistPin]:
        """Get all pins for a playlist."""
        rows = self.conn.execute(
            "SELECT playlist_id, track_id, pin_type, group_id, group_order "
            "FROM playlist_pins WHERE playlist_id = ? ORDER BY pin_type, group_id, group_order",
            (playlist_id,),
        ).fetchall()
        return [
            PlaylistPin(
                playlist_id=row[0],
                track_id=row[1],
                pin_type=row[2],
                group_id=row[3],
                group_order=row[4],
            )
            for row in rows
        ]

    def transfer_pins(
        self, playlist_id: int, from_track_id: int, to_track_id: int
    ) -> None:
        """Transfer all pins from one track to another within a playlist."""
        with self.conn:
            self.conn.execute(
                """UPDATE playlist_pins SET track_id = ?
                   WHERE playlist_id = ? AND track_id = ?""",
                (to_track_id, playlist_id, from_track_id),
            )

    def set_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> None:
        """Set the track order for a playlist, replacing existing rows."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        for position, track_id in enumerate(track_ids):
            self.conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                (playlist_id, track_id, position),
            )
        self.conn.commit()

    def clear_playlist_tracks(self, playlist_id: int) -> None:
        """Remove all tracks from a playlist without deleting the playlist."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        self.conn.commit()

    def get_playlist_track_ids(self, playlist_id: int) -> list[int]:
        """Return ordered track IDs for a playlist."""
        rows = self.conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def get_playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Get ordered tracks for a playlist."""
        rows = self.conn.execute(
            """SELECT t.* FROM tracks t
               JOIN playlist_tracks pt ON t.id = pt.track_id
               WHERE pt.playlist_id = ?
               ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def get_release_years_for_playlist(self, playlist_id: int) -> dict[int, int | None]:
        """Map each track in a playlist to a best-known release year.

        Reads ``track_platform_metadata.release_year`` (populated per platform).
        When a track has release years from multiple platforms, the earliest
        non-null year is used (the original release, not a later reissue).
        Every track in the playlist is present in the result; a track with no
        recorded year maps to ``None`` so callers can report it as unverifiable.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id AS track_id,
                      MIN(tpm.release_year) AS year
               FROM playlist_tracks pt
               LEFT JOIN track_platform_metadata tpm
                    ON tpm.track_id = pt.track_id
                    AND tpm.release_year IS NOT NULL
               WHERE pt.playlist_id = ?
               GROUP BY pt.track_id""",
            (playlist_id,),
        ).fetchall()
        return {row["track_id"]: row["year"] for row in rows}

    def add_concept_acceptance(
        self, playlist_id: int, track_id: int, rule: str
    ) -> None:
        """Record that a review finding for (track, rule) is accepted.

        Idempotent: re-accepting the same pair is a no-op. The normalized
        ``rule_key`` is what future reviews match against; the raw rule text is
        stored alongside for display.
        """
        from tuneshift.composer.rules import normalize_rule_key

        self.conn.execute(
            """INSERT OR IGNORE INTO concept_rule_acceptances
                   (playlist_id, track_id, rule_key, rule_text)
               VALUES (?, ?, ?, ?)""",
            (playlist_id, track_id, normalize_rule_key(rule), rule),
        )
        self.conn.commit()

    def get_concept_acceptances(self, playlist_id: int) -> set[tuple[int, str]]:
        """Return accepted ``(track_id, rule_key)`` pairs for a playlist."""
        rows = self.conn.execute(
            "SELECT track_id, rule_key FROM concept_rule_acceptances "
            "WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
        return {(row["track_id"], row["rule_key"]) for row in rows}

    def list_concept_acceptances(self, playlist_id: int) -> list[tuple[int, str]]:
        """Return accepted ``(track_id, rule_text)`` pairs for display."""
        rows = self.conn.execute(
            "SELECT track_id, rule_text FROM concept_rule_acceptances "
            "WHERE playlist_id = ? ORDER BY track_id, rule_text",
            (playlist_id,),
        ).fetchall()
        return [(row["track_id"], row["rule_text"]) for row in rows]

    def clear_concept_acceptance(
        self, playlist_id: int, track_id: int, rule: str
    ) -> None:
        """Remove a previously recorded acceptance for (track, rule)."""
        from tuneshift.composer.rules import normalize_rule_key

        self.conn.execute(
            "DELETE FROM concept_rule_acceptances "
            "WHERE playlist_id = ? AND track_id = ? AND rule_key = ?",
            (playlist_id, track_id, normalize_rule_key(rule)),
        )
        self.conn.commit()

    def remove_playlist_track_by_position(
        self, playlist_id: int, position: int
    ) -> None:
        """Remove the track at a specific position and reindex later rows.

        Position-scoped (BUG-7): only the row at ``position`` is removed, so a
        track that legitimately appears at multiple positions keeps its other
        copies. Pins for the track are cleared only when no copy of it remains in
        the playlist (a track pinned while still present elsewhere keeps its pin).
        """
        with self.conn:
            row = self.conn.execute(
                "SELECT track_id FROM playlist_tracks "
                "WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            ).fetchone()
            if row is None:
                return
            track_id = row["track_id"]
            self.conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            )
            self.conn.execute(
                """UPDATE playlist_tracks SET position = position - 1
                   WHERE playlist_id = ? AND position > ?""",
                (playlist_id, position),
            )
            still_present = self.conn.execute(
                "SELECT 1 FROM playlist_tracks "
                "WHERE playlist_id = ? AND track_id = ? LIMIT 1",
                (playlist_id, track_id),
            ).fetchone()
            if still_present is None:
                self.conn.execute(
                    "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
                    (playlist_id, track_id),
                )

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> None:
        """Remove track from playlist with cascade cleanup of pins and positions."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            )
            self.conn.execute(
                "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            )
            rows = self.conn.execute(
                "SELECT rowid FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
            for idx, (rowid,) in enumerate(rows):
                self.conn.execute(
                    "UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                    (idx, rowid),
                )

    def per_playlist_coverage(self) -> list[dict[str, Any]]:
        """Per-playlist resolution coverage, lowest playable-fraction first.

        Each row partitions the playlist's distinct tracks into ``playable`` /
        ``quarantined`` / ``unresolved`` (same rule as the headline). ``pct`` is
        ``playable / total``. Playlists whose only gap is quarantined-unavailable
        tracks (``unresolved == 0`` and ``quarantined > 0``) are "done as they can
        be"; a nonzero ``unresolved`` is the call to run ``resolve``.
        """
        rows = self.conn.execute(
            """SELECT p.name AS name,
                      COUNT(DISTINCT pt.track_id) AS total,
                      COUNT(DISTINCT CASE
                          WHEN t.confidence_tier IS NOT NULL AND t.quarantine_state IS NULL
                          THEN t.id END) AS playable,
                      COUNT(DISTINCT CASE
                          WHEN t.quarantine_state IS NOT NULL
                          THEN t.id END) AS quarantined,
                      COUNT(DISTINCT CASE
                          WHEN t.confidence_tier IS NULL AND t.quarantine_state IS NULL
                          THEN t.id END) AS unresolved
               FROM playlists p
               JOIN playlist_tracks pt ON pt.playlist_id = p.id
               JOIN tracks t ON t.id = pt.track_id
               GROUP BY p.id, p.name"""
        ).fetchall()
        result = [
            {
                "name": row["name"],
                "total": row["total"],
                "playable": row["playable"],
                "quarantined": row["quarantined"],
                "unresolved": row["unresolved"],
                "pct": (row["playable"] / row["total"]) if row["total"] else 0.0,
            }
            for row in rows
        ]
        result.sort(key=lambda r: (r["pct"], r["name"]))
        return result

    def add_track_to_playlist(
        self, playlist_id: int, track_id: int, position: int
    ) -> None:
        """Add a track at a specific position (upsert, no error on conflict)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO playlist_tracks (playlist_id, track_id, position)
               VALUES (?, ?, ?)""",
            (playlist_id, track_id, position),
        )
        self.conn.commit()

    def get_track_position(self, playlist_id: int, track_id: int) -> int | None:
        """Return a track's position in a playlist, or None if it is not present."""
        row = self.conn.execute(
            "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        ).fetchone()
        return int(row["position"]) if row else None

    def get_playlist_track_positions(self, playlist_id: int) -> list[int]:
        """Return the ordered positions of every row in a playlist."""
        return [
            row["position"]
            for row in self.conn.execute(
                "SELECT position FROM playlist_tracks WHERE playlist_id = ? "
                "ORDER BY position",
                (playlist_id,),
            )
        ]

    def get_max_playlist_position(self, playlist_id: int) -> int:
        """Return the highest position in a playlist, or 0 if the playlist is empty."""
        row = self.conn.execute(
            "SELECT MAX(position) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        return (row[0] if row else 0) or 0

    def get_playlist_reorder_config(
        self, playlist_id: int
    ) -> tuple[int, str | None] | None:
        """Return ``(auto_reorder, reorder_arc)`` for a playlist, or None if absent."""
        row = self.conn.execute(
            "SELECT auto_reorder, reorder_arc FROM playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if row is None:
            return None
        return row["auto_reorder"], row["reorder_arc"]

    def get_playlist_name(self, playlist_id: int) -> str | None:
        """Return a playlist's name, or None if no such playlist exists."""
        row = self.conn.execute(
            "SELECT name FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row["name"] if row else None

    def get_playlist_name_description(
        self, playlist_id: int
    ) -> tuple[str, str | None] | None:
        """Return ``(name, description)`` for a playlist, or None if absent."""
        row = self.conn.execute(
            "SELECT name, description FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        if row is None:
            return None
        return row["name"], row["description"]

    def append_playlist_track(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """Insert a playlist row at ``position`` (plain INSERT, errors on conflict).

        Unlike :meth:`add_track_to_playlist` (INSERT OR REPLACE), this refuses to
        silently overwrite an occupied position, so callers that pre-check for a
        free slot surface a genuine conflict instead of clobbering a row.
        """
        self.conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
            "VALUES (?, ?, ?)",
            (playlist_id, track_id, position),
        )
        if commit:
            self.conn.commit()

    def insert_playlist_track_if_absent(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """INSERT OR IGNORE a playlist row (no-op if the position is taken)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) "
            "VALUES (?, ?, ?)",
            (playlist_id, track_id, position),
        )
        if commit:
            self.conn.commit()

    def shift_playlist_position(
        self,
        playlist_id: int,
        from_position: int,
        to_position: int,
        *,
        commit: bool = False,
    ) -> None:
        """Move the row currently at ``from_position`` to ``to_position``."""
        self.conn.execute(
            "UPDATE playlist_tracks SET position = ? "
            "WHERE playlist_id = ? AND position = ?",
            (to_position, playlist_id, from_position),
        )
        if commit:
            self.conn.commit()

    def set_playlist_track_position(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """Set a specific track's position within a playlist."""
        self.conn.execute(
            "UPDATE playlist_tracks SET position = ? "
            "WHERE playlist_id = ? AND track_id = ?",
            (position, playlist_id, track_id),
        )
        if commit:
            self.conn.commit()

    def remove_playlist_track(
        self, playlist_id: int, track_id: int, *, commit: bool = False
    ) -> None:
        """Delete every row for a track_id within a playlist."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        if commit:
            self.conn.commit()

    def delete_playlist(self, playlist_id: int, *, commit: bool = False) -> None:
        """Delete a playlist row by id."""
        self.conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        if commit:
            self.conn.commit()

    def find_playlist_by_name(self, name: str) -> Playlist | None:
        """Find a playlist by exact name."""
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_playlist(row)

    def set_goal(self, playlist_id: int, goal: str | None) -> None:
        """Set the goal for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET goal = ? WHERE id = ?", (goal, playlist_id)
        )
        self.conn.commit()

    def get_goal(self, playlist_id: int) -> str | None:
        """Get the goal for a playlist."""
        row = self.conn.execute(
            "SELECT goal FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_weights(self, playlist_id: int, weights: dict | None) -> None:
        """Set the weights for a playlist."""
        val = json.dumps(weights) if weights else None
        self.conn.execute(
            "UPDATE playlists SET weights = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_weights(self, playlist_id: int) -> dict | None:
        """Get the weights for a playlist."""
        row = self.conn.execute(
            "SELECT weights FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_constraints(self, playlist_id: int, constraints: dict | None) -> None:
        """Set the curation constraints for a playlist."""
        val = json.dumps(constraints) if constraints else None
        self.conn.execute(
            "UPDATE playlists SET curation_constraints = ? WHERE id = ?",
            (val, playlist_id),
        )
        self.conn.commit()

    def get_constraints(self, playlist_id: int) -> dict | None:
        """Get the curation constraints for a playlist."""
        row = self.conn.execute(
            "SELECT curation_constraints FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_preferences(self, playlist_id: int, prefs: dict | None) -> None:
        """Set the preferences for a playlist."""
        val = json.dumps(prefs) if prefs else None
        self.conn.execute(
            "UPDATE playlists SET preferences = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_preferences(self, playlist_id: int) -> dict | None:
        """Get the preferences for a playlist."""
        row = self.conn.execute(
            "SELECT preferences FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_playlist_type(self, playlist_id: int, playlist_type: str | None) -> None:
        """Set the playlist type."""
        self.conn.execute(
            "UPDATE playlists SET playlist_type = ? WHERE id = ?",
            (playlist_type, playlist_id),
        )
        self.conn.commit()

    def get_playlist_type(self, playlist_id: int) -> str | None:
        """Get the playlist type."""
        row = self.conn.execute(
            "SELECT playlist_type FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_mood_profile(self, playlist_id: int, mood_profile: dict | None) -> None:
        """Set the mood profile for a playlist."""
        val = json.dumps(mood_profile) if mood_profile else None
        self.conn.execute(
            "UPDATE playlists SET mood_profile = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_mood_profile(self, playlist_id: int) -> dict | None:
        """Get the mood profile for a playlist."""
        row = self.conn.execute(
            "SELECT mood_profile FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None
