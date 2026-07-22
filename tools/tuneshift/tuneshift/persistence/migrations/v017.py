"""Schema migration to version 17 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Separate transient (rate-limit) retries from hard-failure
    # attempts so a burst of 429s can never erode the quarantine
    # budget (AC-D7 worker semantics). PRAGMA-guarded ALTER.
    rq_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(resolution_queue)").fetchall()
    }
    if rq_cols and "transient_attempts" not in rq_cols:
        conn.execute(
            "ALTER TABLE resolution_queue "
            "ADD COLUMN transient_attempts INTEGER NOT NULL DEFAULT 0"
        )
