"""Schema migration to version 6 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    playlist_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()
    }
    if "narrative" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN narrative TEXT")
