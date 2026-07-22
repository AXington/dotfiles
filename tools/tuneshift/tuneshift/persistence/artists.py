"""Persistence mixin: artists."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tuneshift.matching.normalize import normalize_artist as _alias_normalize
from tuneshift.models import (
    Album,
    Artist,
)
from tuneshift.persistence.base import (
    PersistenceBase,
    normalize_artist,
    normalize_ban_name,
)

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class ArtistsMixin(PersistenceBase):
    """Artists persistence methods for the Database facade."""

    def add_artist_alias(self, members: Sequence[str]) -> None:
        """Add (or extend) an artist-alias equivalence class.

        ``members`` are raw surface forms (e.g. ``["98 Degrees", "98\u00ba"]``);
        surrounding whitespace is trimmed but case and glyphs are preserved so
        the exact spelling is retained for retrieval query expansion. At least
        two *distinct* raw members are required. If any member's normalized key
        already belongs to an existing class, the new members are merged into it
        (bridging several classes into the lowest ``class_id`` when they
        overlap); duplicate ``(class_id, member)`` rows are ignored. Idempotent.
        """
        trimmed = [m.strip() for m in members if m and m.strip()]
        distinct = set(trimmed)
        if len(distinct) < 2:
            raise ValueError("an alias class needs at least two distinct members")
        norms = {_alias_normalize(m) for m in distinct}
        placeholders = ",".join("?" * len(norms))
        with self.conn:
            rows = self.conn.execute(
                f"SELECT DISTINCT class_id FROM artist_aliases "  # noqa: S608 - placeholders are bound '?' params; values parameterized
                f"WHERE norm_member IN ({placeholders})",
                tuple(norms),
            ).fetchall()
            existing = sorted(r[0] for r in rows)
            if existing:
                target = existing[0]
                for other in existing[1:]:
                    self.conn.execute(
                        "UPDATE OR IGNORE artist_aliases SET class_id = ? "
                        "WHERE class_id = ?",
                        (target, other),
                    )
                    self.conn.execute(
                        "DELETE FROM artist_aliases WHERE class_id = ?", (other,)
                    )
            else:
                target = self.conn.execute(
                    "SELECT COALESCE(MAX(class_id), 0) + 1 FROM artist_aliases"
                ).fetchone()[0]
            for member in distinct:
                self.conn.execute(
                    "INSERT OR IGNORE INTO artist_aliases "
                    "(class_id, member, norm_member) VALUES (?, ?, ?)",
                    (target, member, _alias_normalize(member)),
                )

    def get_artist_alias_classes(self) -> list[frozenset[str]]:
        """Return every user-curated alias class as a frozenset of raw members."""
        rows = self.conn.execute(
            "SELECT class_id, member FROM artist_aliases ORDER BY class_id"
        ).fetchall()
        classes: dict[int, set[str]] = {}
        for class_id, member in rows:
            classes.setdefault(class_id, set()).add(member)
        return [frozenset(members) for members in classes.values()]

    def remove_artist_alias(self, member: str) -> bool:
        """Remove a raw alias member; drop the class if it falls below 2 members.

        Matches ``member`` exactly after trimming surrounding whitespace. Returns
        True if a row was removed, False if the member is absent from the DB
        (e.g. a seed-only member, which is read-only).
        """
        target = (member or "").strip()
        if not target:
            return False
        with self.conn:
            row = self.conn.execute(
                "SELECT class_id FROM artist_aliases WHERE member = ?", (target,)
            ).fetchone()
            if row is None:
                return False
            class_id = row[0]
            self.conn.execute(
                "DELETE FROM artist_aliases WHERE class_id = ? AND member = ?",
                (class_id, target),
            )
            remaining = self.conn.execute(
                "SELECT COUNT(DISTINCT member) FROM artist_aliases WHERE class_id = ?",
                (class_id,),
            ).fetchone()[0]
            if remaining < 2:
                self.conn.execute(
                    "DELETE FROM artist_aliases WHERE class_id = ?", (class_id,)
                )
        return True

    def get_artist(self, artist_id: int) -> Artist | None:
        """Get an artist by ID."""
        row = self.conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_artist(row)

    def get_artist_by_name(self, name: str) -> Artist | None:
        """Get an artist by name (normalized lookup)."""
        norm = normalize_artist(name)
        row = self.conn.execute(
            "SELECT * FROM artists WHERE norm_name = ?", (norm,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_artist(row)

    def get_artists_for_playlist(self, playlist_id: int) -> list[Artist]:
        """Get all unique artists in a playlist."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT a.* FROM artists a
            JOIN tracks t ON t.artist_id = a.id
            JOIN playlist_tracks pt ON pt.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY a.name
        """,
            (playlist_id,),
        ).fetchall()
        return [self._row_to_artist(row) for row in rows]

    _UPDATABLE_ARTIST_COLUMNS = frozenset(
        {
            "name",
            "norm_name",
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
            "spotify_artist_uri",
            "lastfm_url",
            "wikipedia_url",
            "enrichment_sources",
            "verified",
            "enriched_at",
            "verified_at",
        }
    )

    def update_artist(self, artist_id: int, **fields: Any) -> None:
        """Update artist fields by keyword arguments.

        Field names are validated against an allowlist of updatable columns
        before being interpolated as SQL identifiers, preventing SQL-identifier
        injection via caller-supplied keys.
        """
        json_fields = {"identity", "tags", "genres", "enrichment_sources"}
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in self._UPDATABLE_ARTIST_COLUMNS:
                raise ValueError(f"Not an updatable artist column: {key!r}")
            sets.append(f"{key} = ?")
            if key in json_fields and not isinstance(value, str):
                values.append(json.dumps(value))
            else:
                values.append(value)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        values.append(artist_id)
        self.conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id = ?", values)  # noqa: S608 - columns from code-controlled allowlist; values parameterized
        self.conn.commit()

    def get_album(self, album_id: int) -> Album | None:
        """Get an album by ID."""
        row = self.conn.execute(
            "SELECT * FROM albums WHERE id = ?", (album_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_album(row)

    def get_albums_by_artist(self, artist_id: int) -> list[Album]:
        """Get all albums by an artist."""
        rows = self.conn.execute(
            "SELECT * FROM albums WHERE artist_id = ? ORDER BY release_date",
            (artist_id,),
        ).fetchall()
        return [self._row_to_album(row) for row in rows]

    def ban_artist(self, name: str, reason: str | None = None) -> int:
        """Add an artist to the global ban list. Returns the ban ID."""
        norm = normalize_ban_name(name)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO banned_artists (name, norm_name, reason) VALUES (?, ?, ?)",
            (name, norm, reason),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def unban_artist(self, name: str) -> bool:
        """Remove an artist from the ban list. Returns True if removed."""
        norm = normalize_ban_name(name)
        cursor = self.conn.execute(
            "DELETE FROM banned_artists WHERE norm_name = ?", (norm,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_banned_artists(self) -> list[tuple[str, str | None]]:
        """Get all banned artists as (name, reason) tuples."""
        rows = self.conn.execute(
            "SELECT name, reason FROM banned_artists ORDER BY name"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def is_artist_banned(self, name: str) -> bool:
        """Check if an artist name (or segment) is on the ban list."""
        norm = normalize_ban_name(name)
        row = self.conn.execute(
            "SELECT 1 FROM banned_artists WHERE norm_name = ?", (norm,)
        ).fetchone()
        return row is not None
