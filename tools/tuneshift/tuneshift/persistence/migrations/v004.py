"""Schema migration to version 4 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute(
        """
            CREATE TABLE IF NOT EXISTS playlist_pins (
                id INTEGER PRIMARY KEY,
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                pin_type TEXT NOT NULL,
                group_id TEXT,
                group_order INTEGER,
                UNIQUE(playlist_id, track_id)
            )
            """
    )
