"""Schema migration to version 19 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Persisted candidate ORDER matters for winner parity: selection
    # keeps input order for default band-ties (selection.py stable
    # sort), so the persisted set must be returned in the same
    # discovery order reconcile's live gather produced (spec section 4.1a /
    # AC-X3, AC-P4). Add a rank column; existing rows default to 0.
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(track_candidates)").fetchall()
    }
    if "discovery_rank" not in cols:
        conn.execute(
            "ALTER TABLE track_candidates "
            "ADD COLUMN discovery_rank INTEGER NOT NULL DEFAULT 0"
        )
