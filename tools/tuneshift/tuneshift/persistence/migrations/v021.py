"""Schema migration to version 21 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Concept-rule acceptances: persist (playlist, track, rule) pairs
    # a user has explicitly accepted so a review finding for that pair
    # is suppressed across runs, even when a thematic LLM verdict is
    # non-deterministic. rule_key is a normalized form of the rule.
    conn.execute("""
            CREATE TABLE IF NOT EXISTS concept_rule_acceptances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL
                    REFERENCES playlists(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL
                    REFERENCES tracks(id) ON DELETE CASCADE,
                rule_key TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(playlist_id, track_id, rule_key)
            )
        """)
