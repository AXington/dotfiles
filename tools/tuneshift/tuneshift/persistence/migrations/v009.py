"""Schema migration to version 9 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_artists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                norm_name TEXT NOT NULL UNIQUE,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_history (
                id INTEGER PRIMARY KEY,
                playlist_id INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                reverted_at TEXT
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_batch_history_playlist "
        "ON batch_history(playlist_id)"
    )
