"""Schema migration to version 8 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # Create artists table
    conn.execute("""
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                norm_name TEXT NOT NULL,
                sort_name TEXT,
                bio TEXT,
                identity JSON,
                tags JSON DEFAULT '[]',
                identity_confidence TEXT DEFAULT 'unconfirmed',
                genres JSON DEFAULT '[]',
                origin TEXT,
                active_start INTEGER,
                active_end INTEGER,
                mb_artist_id TEXT,
                tidal_artist_id INTEGER,
                spotify_artist_uri TEXT,
                lastfm_url TEXT,
                wikipedia_url TEXT,
                enrichment_sources JSON DEFAULT '[]',
                verified INTEGER DEFAULT 0,
                enriched_at TEXT,
                verified_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_artists_norm ON artists(norm_name)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artists_mb ON artists(mb_artist_id)")

    # Create albums table
    conn.execute("""
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                norm_title TEXT NOT NULL,
                artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
                release_date TEXT,
                release_type TEXT DEFAULT 'album',
                edition TEXT DEFAULT 'original',
                genres JSON DEFAULT '[]',
                mb_release_group_id TEXT,
                tidal_album_id INTEGER,
                spotify_album_uri TEXT,
                enriched_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(norm_title, artist_id, edition)
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id)")

    # Add FK columns to tracks
    track_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "artist_id" not in track_cols:
        conn.execute(
            "ALTER TABLE tracks ADD COLUMN artist_id INTEGER REFERENCES artists(id)"
        )
    if "album_id" not in track_cols:
        conn.execute(
            "ALTER TABLE tracks ADD COLUMN album_id INTEGER REFERENCES albums(id)"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id)")

    # Populate artists from existing track data
    # Use the most common casing for each norm_artist as the canonical name
    conn.execute("""
            INSERT OR IGNORE INTO artists (name, norm_name)
            SELECT artist, norm_artist FROM (
                SELECT artist, norm_artist, COUNT(*) as cnt,
                       ROW_NUMBER() OVER (PARTITION BY norm_artist ORDER BY COUNT(*) DESC) as rn
                FROM tracks
                GROUP BY artist, norm_artist
            ) WHERE rn = 1
        """)

    # Link tracks to artists
    conn.execute("""
            UPDATE tracks SET artist_id = (
                SELECT id FROM artists WHERE artists.norm_name = tracks.norm_artist
            )
        """)

    # Populate albums from existing track data
    conn.execute("""
            INSERT OR IGNORE INTO albums (title, norm_title, artist_id)
            SELECT t.album, t.norm_album, t.artist_id
            FROM (
                SELECT album, norm_album, artist_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY norm_album, artist_id ORDER BY COUNT(*) DESC
                       ) as rn
                FROM tracks
                WHERE album IS NOT NULL AND artist_id IS NOT NULL
                GROUP BY album, norm_album, artist_id
            ) t WHERE t.rn = 1
        """)

    # Link tracks to albums
    conn.execute("""
            UPDATE tracks SET album_id = (
                SELECT a.id FROM albums a
                WHERE a.norm_title = tracks.norm_album
                AND a.artist_id = tracks.artist_id
            )
            WHERE tracks.album IS NOT NULL AND tracks.artist_id IS NOT NULL
        """)
