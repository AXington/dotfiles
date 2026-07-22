"""Schema migration to version 2 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    track_identity_columns = {
        "mb_recording_id": "TEXT",
        "mb_release_group_id": "TEXT",
        "confidence_tier": "TEXT",
        "confidence_score": "REAL",
        "resolved_at": "TEXT",
    }
    for column_name, column_type in track_identity_columns.items():
        if column_name not in cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {column_name} {column_type}")

    conn.execute(
        """
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                raw_data TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                superseded_by INTEGER REFERENCES evidence(id),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_track ON evidence(track_id, is_current)"
    )
