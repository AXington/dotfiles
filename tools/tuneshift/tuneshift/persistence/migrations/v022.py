"""Schema migration to version 22 (extracted from db.py:_migrate_schema)."""

from __future__ import annotations

from tuneshift.persistence import _Conn


def apply(conn: _Conn) -> None:
    # BUG-9: idx_artists_norm was meant to be UNIQUE, but the earlier
    # migration used "CREATE UNIQUE INDEX IF NOT EXISTS" while a plain
    # (non-unique) index of that name already existed, so the statement
    # silently no-opped and never upgraded the index. Without a real
    # UNIQUE constraint, _get_or_create_artist's INSERT OR IGNORE had
    # nothing to violate, so repeated calls inserted duplicate artist
    # rows. Coalesce each duplicate norm_name group into its lowest-id
    # keeper, repoint FK references to the keeper, then DROP the stale
    # index and recreate it as genuinely UNIQUE.
    #
    # General lesson: CREATE UNIQUE INDEX IF NOT EXISTS cannot tighten
    # an existing non-unique index; a migration must DROP it first.
    artist_cols = {r[1] for r in conn.execute("PRAGMA table_info(artists)").fetchall()}
    merge_cols = [
        c
        for c in (
            "sort_name",
            "bio",
            "identity",
            "tags",
            "identity_confidence",
            "genres",
            "origin",
            "active_start",
            "active_end",
            "mb_artist_id",
            "tidal_artist_id",
            "qobuz_artist_id",
            "spotify_artist_uri",
            "lastfm_url",
            "wikipedia_url",
            "enrichment_sources",
            "verified",
            "enriched_at",
            "verified_at",
        )
        if c in artist_cols
    ]
    dupe_groups = conn.execute(
        "SELECT norm_name, MIN(id) AS keeper FROM artists "
        "GROUP BY norm_name HAVING COUNT(*) > 1"
    ).fetchall()
    for group in dupe_groups:
        norm = group["norm_name"]
        keeper = group["keeper"]
        dupes = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM artists WHERE norm_name = ? AND id != ? ORDER BY id",
                (norm, keeper),
            ).fetchall()
        ]
        # Fill any NULL keeper column from the earliest dupe that has
        # a value (keeper is the richest in practice, so this is
        # belt-and-suspenders, but keeps the merge correct generally).
        for col in merge_cols:
            conn.execute(
                f"UPDATE artists SET {col} = ("  # noqa: S608 - columns from code-controlled allowlist; values parameterized  # nosec B608
                f"    SELECT d.{col} FROM artists d "
                f"    WHERE d.norm_name = ? AND d.id != ? "
                f"      AND d.{col} IS NOT NULL ORDER BY d.id LIMIT 1"
                f") WHERE id = ? AND {col} IS NULL",
                (norm, keeper, keeper),
            )
        placeholders = ",".join("?" * len(dupes))
        conn.execute(
            f"UPDATE tracks SET artist_id = ? "  # noqa: S608 - placeholders are bound '?' params; values parameterized  # nosec B608
            f"WHERE artist_id IN ({placeholders})",
            (keeper, *dupes),
        )
        conn.execute(
            f"UPDATE albums SET artist_id = ? "  # noqa: S608 - placeholders are bound '?' params; values parameterized  # nosec B608
            f"WHERE artist_id IN ({placeholders})",
            (keeper, *dupes),
        )
        conn.execute(
            f"DELETE FROM artists WHERE id IN ({placeholders})",  # noqa: S608 - placeholders are bound '?' params; values parameterized  # nosec B608
            tuple(dupes),
        )
    conn.execute("DROP INDEX IF EXISTS idx_artists_norm")
    conn.execute("CREATE UNIQUE INDEX idx_artists_norm ON artists(norm_name)")
