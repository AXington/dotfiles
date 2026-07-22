"""Schema migration to version 14 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Artist-alias equivalence: user-curated classes of equivalent
    # artist surface forms (98\u00ba / 98 Degrees, Ke$ha / Kesha).
    conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_aliases (
                class_id INTEGER NOT NULL,
                member TEXT NOT NULL,
                norm_member TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (class_id, member)
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artist_aliases_norm "
        "ON artist_aliases(norm_member)"
    )
