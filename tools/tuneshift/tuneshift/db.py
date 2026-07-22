"""Public Database facade composed from persistence mixins.

The Database class is a thin composition of the persistence.* mixins;
all schema, migrations, and queries live under tuneshift/persistence/.
db.py remains the public import surface (``from tuneshift.db import
Database``) and re-exports the stable module-level helpers.
"""

from __future__ import annotations

from pathlib import Path

from tuneshift.persistence.artists import ArtistsMixin
from tuneshift.persistence.base import (
    _SCHEMA_VERSION,
    get_default_db_path,
    normalize_artist,
    normalize_ban_name,
    normalize_title,
)
from tuneshift.persistence.collections import CollectionsMixin
from tuneshift.persistence.meta import MetaMixin
from tuneshift.persistence.platform import PlatformMixin
from tuneshift.persistence.playlists import PlaylistsMixin
from tuneshift.persistence.schema import SchemaMixin
from tuneshift.persistence.tracks import TracksMixin
from tuneshift.types import JournalEntry, ReviewItem

__all__ = [
    "_SCHEMA_VERSION",
    "Database",
    "JournalEntry",
    "ReviewItem",
    "get_default_db_path",
    "normalize_artist",
    "normalize_ban_name",
    "normalize_title",
]


class Database(
    SchemaMixin,
    TracksMixin,
    PlaylistsMixin,
    PlatformMixin,
    MetaMixin,
    ArtistsMixin,
    CollectionsMixin,
):
    """SQLite database wrapper composed from persistence mixins."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or get_default_db_path()
        if self.path.exists() and self.path.is_symlink():
            raise ValueError(f"Refusing to open symlinked database: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._ensure_schema()
