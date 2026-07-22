"""Schema migration to version 18 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Plan/apply journal (section 7, AC-P4): records every applied write so
    # a LOCAL apply is reversible in one step by reverse-replay.
    conn.execute("""
            CREATE TABLE IF NOT EXISTS apply_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                op TEXT NOT NULL,
                prior_value TEXT,
                new_value TEXT,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_apply_journal_plan "
        "ON apply_journal(plan_id, id)"
    )
