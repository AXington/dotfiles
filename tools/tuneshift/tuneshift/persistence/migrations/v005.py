"""Schema migration to version 5 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    conn.execute("""
            DELETE FROM playlist_pins
            WHERE NOT EXISTS (
                SELECT 1 FROM playlist_tracks
                WHERE playlist_tracks.playlist_id = playlist_pins.playlist_id
                AND playlist_tracks.track_id = playlist_pins.track_id
            )
        """)
