"""Schema migration to version 11 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute("""
            CREATE TABLE IF NOT EXISTS track_platform_metadata (
                id INTEGER PRIMARY KEY,
                track_id INTEGER NOT NULL REFERENCES tracks(id),
                platform TEXT NOT NULL,
                platform_track_id TEXT NOT NULL,
                release_year INTEGER,
                release_date TEXT,
                genres TEXT,
                audio_qualities TEXT,
                album_name TEXT,
                album_type TEXT,
                explicit INTEGER,
                duration_ms INTEGER,
                popularity INTEGER,
                raw_metadata TEXT,
                fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(track_id, platform)
            )
        """)
    conn.execute("""
            CREATE TABLE IF NOT EXISTS track_tags (
                track_id INTEGER NOT NULL REFERENCES tracks(id),
                tag TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (track_id, tag)
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_track_tags_tag ON track_tags(tag)")
