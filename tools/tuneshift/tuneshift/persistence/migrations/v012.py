"""Schema migration to version 12 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute("""
            CREATE TABLE IF NOT EXISTS match_audits (
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                availability TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                audit_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (track_id, platform)
            )
        """)
