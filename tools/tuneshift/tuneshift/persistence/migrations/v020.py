"""Schema migration to version 20 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

import json

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # FL3: unify the preference model. playlist_track_prefs gains a
    # surrogate id + a NULL-safe unique index on
    # (playlist_id, track_id, criterion, target) so multiple targets
    # on one axis coexist (Alice's bug: could not avoid karaoke AND
    # instrumental), and playlist_id becomes NULLable so a NULL row
    # is a playlist-agnostic per-track preference. The orphan
    # tracks.preferences blob is folded in and dropped.
    conn.execute("ALTER TABLE playlist_track_prefs RENAME TO _ptp_old_v20")
    conn.execute("""
            CREATE TABLE playlist_track_prefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                criterion TEXT NOT NULL,
                strength TEXT NOT NULL,
                target TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_track_prefs_scope
                ON playlist_track_prefs(
                    COALESCE(playlist_id, -1), track_id, criterion,
                    COALESCE(target, '')
                )
        """)
    conn.execute("""
            INSERT INTO playlist_track_prefs
                (playlist_id, track_id, criterion, strength, target,
                 created_at, updated_at)
            SELECT playlist_id, track_id, criterion, strength, target,
                   created_at, updated_at
            FROM _ptp_old_v20
        """)
    conn.execute("DROP TABLE _ptp_old_v20")

    # Fold any typed per-track criteria out of tracks.preferences
    # into the NULL-playlist (playlist-agnostic) scope, then drop the
    # orphan column. Legacy prefer/avoid keyword blobs (never wired to
    # the typed engine) are not carried over.
    track_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "preferences" in track_cols:
        rows = conn.execute(
            "SELECT id, preferences FROM tracks "
            "WHERE preferences IS NOT NULL AND preferences != ''"
        ).fetchall()
        for row in rows:
            try:
                blob = json.loads(row[1])
            except (ValueError, TypeError):
                continue
            for crit in (blob or {}).get("criteria") or ():
                criterion = crit.get("criterion")
                strength = crit.get("strength")
                if not criterion or not strength:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO playlist_track_prefs
                               (playlist_id, track_id, criterion,
                                strength, target)
                           VALUES (NULL, ?, ?, ?, ?)""",
                    (row[0], criterion, strength, crit.get("target")),
                )
        conn.execute("ALTER TABLE tracks DROP COLUMN preferences")
