"""Schema migration to version 15 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # First-class version-selection metadata columns (spec section 4.1,
    # AC-D3/D4). These lift audio/version/release fields out of the
    # opaque metadata JSON so the matching path can read them, plus a
    # field_provenance JSON column recording (source, timestamp) per
    # enrichable field. Idempotent ALTERs guarded by PRAGMA.
    track_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    first_class_columns = {
        "album_artist": "TEXT",
        "album_type": "TEXT",
        "label": "TEXT",
        "recording_date": "TEXT",
        "release_date": "TEXT",
        "remaster_year": "INTEGER",
        "audio_modes": "TEXT",
        "audio_quality": "TEXT",
        "tidal_version": "TEXT",
        "language": "TEXT",
        "composer": "TEXT",
        "availability": "TEXT",
        "quarantine_state": "TEXT",
        "quarantine_reason": "TEXT",
        "field_provenance": "TEXT",
    }
    for column_name, column_type in first_class_columns.items():
        if column_name not in track_cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {column_name} {column_type}")
