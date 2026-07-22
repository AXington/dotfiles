"""Schema migration to version 13 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Chunk 6: durable self-healing locks + per-track precedence.
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(platform_tracks)").fetchall()
    }
    if "fingerprint" not in cols:
        conn.execute("ALTER TABLE platform_tracks ADD COLUMN fingerprint TEXT")
    track_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()
    }
    if "preferences" not in track_cols:
        conn.execute("ALTER TABLE tracks ADD COLUMN preferences TEXT")
