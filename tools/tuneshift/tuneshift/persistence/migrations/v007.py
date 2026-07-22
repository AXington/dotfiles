"""Schema migration to version 7 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    playlist_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(playlists)").fetchall()
    }
    if "collection" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN collection TEXT")
    if "goal" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN goal TEXT")
    if "playlist_type" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN playlist_type TEXT")
    if "weights" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN weights TEXT")
    if "mood_profile" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN mood_profile TEXT")
    if "curation_constraints" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN curation_constraints TEXT")
    if "preferences" not in playlist_cols:
        conn.execute("ALTER TABLE playlists ADD COLUMN preferences TEXT")

    playlist_track_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(playlist_tracks)").fetchall()
    }
    if "version_override" not in playlist_track_cols:
        conn.execute("ALTER TABLE playlist_tracks ADD COLUMN version_override TEXT")
