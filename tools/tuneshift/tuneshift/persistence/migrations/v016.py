"""Schema migration to version 16 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Playlist-scope match_audits (spec section 4.1a item 5, AC-CLI3/CLI5):
    # selection is now playlist-dependent, so an audit is keyed by
    # (playlist_id, track_id, platform). Rebuild the table (SQLite
    # cannot alter a PK in place); existing rows land at the global
    # sentinel playlist_id=0. Idempotent via the column-presence guard.
    audit_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(match_audits)").fetchall()
    }
    if audit_cols and "playlist_id" not in audit_cols:
        conn.execute("""
                CREATE TABLE match_audits_new (
                    playlist_id INTEGER NOT NULL DEFAULT 0,
                    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    audit_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (playlist_id, track_id, platform)
                )
            """)
        conn.execute("""
                INSERT INTO match_audits_new
                    (playlist_id, track_id, platform, availability,
                     reason_code, audit_json, updated_at)
                SELECT 0, track_id, platform, availability,
                       reason_code, audit_json, updated_at
                FROM match_audits
            """)
        conn.execute("DROP TABLE match_audits")
        conn.execute("ALTER TABLE match_audits_new RENAME TO match_audits")
