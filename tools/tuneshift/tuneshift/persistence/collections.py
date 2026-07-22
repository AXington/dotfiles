"""Persistence mixin: collections."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tuneshift.persistence.base import (
    PersistenceBase,
)

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class CollectionsMixin(PersistenceBase):
    """Collections persistence methods for the Database facade."""

    def get_latest_batch_history(self) -> sqlite3.Row | None:
        """Return the most recent un-reverted batch_history row, or None."""
        return self.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history "
            "WHERE reverted_at IS NULL ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()

    def get_batch_history_entry(self, history_id: int) -> sqlite3.Row | None:
        """Return a batch_history row by id, or None if it does not exist."""
        return self.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history WHERE id = ?",
            (history_id,),
        ).fetchone()

    def list_batch_history_entries(
        self, *, playlist_id: int | None = None, limit: int = 20
    ) -> list[sqlite3.Row]:
        """Return recent batch_history rows, newest first.

        Scoped to ``playlist_id`` when given (returning all of that playlist's
        history); otherwise the ``limit`` most recent rows across all playlists.
        """
        if playlist_id is not None:
            return self.conn.execute(
                "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
                "FROM batch_history WHERE playlist_id = ? ORDER BY applied_at DESC",
                (playlist_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
            "FROM batch_history ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def clear_all_tidal_folder_assignments(self, *, commit: bool = False) -> None:
        """Clear every playlist's cached Tidal folder id (full re-sync prelude)."""
        self.conn.execute("UPDATE playlists SET tidal_folder_id = NULL")
        if commit:
            self.conn.commit()

    def set_collection(self, playlist_id: int, collection: str | None) -> None:
        """Set the collection a playlist belongs to (e.g., Pride, Laurel Canyon)."""
        self.conn.execute(
            "UPDATE playlists SET collection = ? WHERE id = ?",
            (collection, playlist_id),
        )
        self.conn.commit()

    def get_collection(self, playlist_id: int) -> str | None:
        """Get the collection a playlist belongs to."""
        row = self.conn.execute(
            "SELECT collection FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def list_collections(self) -> list[str]:
        """List all distinct collections."""
        rows = self.conn.execute(
            "SELECT DISTINCT collection FROM playlists WHERE collection IS NOT NULL ORDER BY collection"
        ).fetchall()
        return [r[0] for r in rows]

    def get_playlists_in_collection(self, collection: str) -> list:
        """Get all playlists belonging to a collection."""
        return [
            p for p in self.list_playlists() if self.get_collection(p.id) == collection
        ]

    def record_batch(self, playlist_id: int, plan_json: str) -> int:
        """Record an applied batch plan in history. Returns the history ID."""
        cursor = self.conn.execute(
            "INSERT INTO batch_history (playlist_id, plan_json) VALUES (?, ?)",
            (playlist_id, plan_json),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_batch_history(self, playlist_id: int) -> list[dict]:
        """Get batch history for a playlist."""
        rows = self.conn.execute(
            "SELECT id, plan_json, applied_at, reverted_at "
            "FROM batch_history WHERE playlist_id = ? ORDER BY applied_at DESC",
            (playlist_id,),
        ).fetchall()
        return [
            {"id": r[0], "plan_json": r[1], "applied_at": r[2], "reverted_at": r[3]}
            for r in rows
        ]

    def mark_batch_reverted(self, history_id: int) -> None:
        """Mark a batch history entry as reverted."""
        self.conn.execute(
            "UPDATE batch_history SET reverted_at = datetime('now') WHERE id = ?",
            (history_id,),
        )
        self.conn.commit()

    def create_collection(self, name: str, description: str | None = None) -> int:
        """Create a collection. Returns the ID."""
        self.conn.execute(
            "INSERT OR IGNORE INTO collections (name, description) VALUES (?, ?)",
            (name, description),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (name,)
        ).fetchone()
        return row[0]

    def delete_collection(self, name: str) -> bool:
        """Delete a collection and all its playlist associations."""
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "DELETE FROM playlist_collections WHERE collection_id = ?", (row[0],)
        )
        self.conn.execute("DELETE FROM collections WHERE id = ?", (row[0],))
        self.conn.commit()
        return True

    def tag_playlist(self, playlist_id: int, collection_name: str) -> None:
        """Add a collection tag to a playlist."""
        col_id = self.create_collection(collection_name)
        self.conn.execute(
            "INSERT OR IGNORE INTO playlist_collections (playlist_id, collection_id) VALUES (?, ?)",
            (playlist_id, col_id),
        )
        self.conn.commit()

    def untag_playlist(self, playlist_id: int, collection_name: str) -> bool:
        """Remove a collection tag from a playlist."""
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (collection_name,)
        ).fetchone()
        if not row:
            return False
        cursor = self.conn.execute(
            "DELETE FROM playlist_collections WHERE playlist_id = ? AND collection_id = ?",
            (playlist_id, row[0]),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_playlist_collections(self, playlist_id: int) -> list[str]:
        """Get all collection names for a playlist."""
        rows = self.conn.execute(
            "SELECT c.name FROM collections c "
            "JOIN playlist_collections pc ON pc.collection_id = c.id "
            "WHERE pc.playlist_id = ? ORDER BY c.name",
            (playlist_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_collection_playlists(self, collection_name: str) -> list:
        """Get all playlists in a collection."""
        rows = self.conn.execute(
            "SELECT p.* FROM playlists p "
            "JOIN playlist_collections pc ON pc.playlist_id = p.id "
            "JOIN collections c ON c.id = pc.collection_id "
            "WHERE c.name = ? ORDER BY p.name",
            (collection_name,),
        ).fetchall()
        return [self._row_to_playlist(r) for r in rows]

    def list_collections_with_counts(self) -> list[tuple[str, int]]:
        """List all collections with playlist counts."""
        rows = self.conn.execute(
            "SELECT c.name, COUNT(pc.playlist_id) FROM collections c "
            "LEFT JOIN playlist_collections pc ON pc.collection_id = c.id "
            "GROUP BY c.id ORDER BY c.name"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def cache_tidal_folder(
        self, tidal_id: str, name: str, parent_tidal_id: str | None = None
    ) -> None:
        """Cache a Tidal folder's metadata."""
        self.conn.execute(
            "INSERT OR REPLACE INTO tidal_folders (tidal_id, name, parent_tidal_id, last_synced_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (tidal_id, name, parent_tidal_id),
        )
        self.conn.commit()

    def get_tidal_folder_by_name(self, name: str) -> dict | None:
        """Look up a cached Tidal folder by name."""
        row = self.conn.execute(
            "SELECT tidal_id, name, parent_tidal_id FROM tidal_folders WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return None
        return {"tidal_id": row[0], "name": row[1], "parent_tidal_id": row[2]}

    def get_cached_tidal_folders(self) -> list[dict]:
        """Get all cached Tidal folders."""
        rows = self.conn.execute(
            "SELECT tidal_id, name, parent_tidal_id FROM tidal_folders ORDER BY name"
        ).fetchall()
        return [{"tidal_id": r[0], "name": r[1], "parent_tidal_id": r[2]} for r in rows]

    def remove_tidal_folder_cache(self, tidal_id: str) -> None:
        """Remove a folder from the cache."""
        self.conn.execute("DELETE FROM tidal_folders WHERE tidal_id = ?", (tidal_id,))
        self.conn.commit()

    def set_playlist_tidal_folder(
        self, playlist_id: int, tidal_folder_id: str | None
    ) -> None:
        """Set or clear the Tidal folder assignment for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET tidal_folder_id = ? WHERE id = ?",
            (tidal_folder_id, playlist_id),
        )
        self.conn.commit()

    def get_playlists_by_tidal_folder(self, tidal_folder_id: str) -> list:
        """Get all playlists assigned to a Tidal folder."""
        rows = self.conn.execute(
            "SELECT * FROM playlists WHERE tidal_folder_id = ? ORDER BY name",
            (tidal_folder_id,),
        ).fetchall()
        return [self._row_to_playlist(r) for r in rows]

    def clear_tidal_folder_assignments(self, tidal_folder_id: str) -> int:
        """Clear folder assignments for all playlists in a folder. Returns count."""
        cursor = self.conn.execute(
            "UPDATE playlists SET tidal_folder_id = NULL WHERE tidal_folder_id = ?",
            (tidal_folder_id,),
        )
        self.conn.commit()
        return cursor.rowcount
