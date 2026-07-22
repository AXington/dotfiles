"""Schema migration to version 10 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_collections (
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                PRIMARY KEY (playlist_id, collection_id)
            )
        """)
    conn.execute("""
            CREATE TABLE IF NOT EXISTS tidal_folders (
                id INTEGER PRIMARY KEY,
                tidal_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                parent_tidal_id TEXT,
                last_synced_at TEXT DEFAULT (datetime('now'))
            )
        """)
    playlist_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()
    }
    if "tidal_folder_id" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN tidal_folder_id TEXT")
