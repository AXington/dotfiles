"""Persistence mixin: schema."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tuneshift.persistence.base import (
    _SCHEMA_SQL,
    _SCHEMA_VERSION,
    PersistenceBase,
)
from tuneshift.persistence.migrations import run_migrations

if TYPE_CHECKING:
    from tuneshift.types import JournalEntry as _JournalEntry  # noqa: F401


class SchemaMixin(PersistenceBase):
    """Schema persistence methods for the Database facade."""

    def _ensure_schema(self) -> None:
        """Create tables if they do not exist."""
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(_SCHEMA_VERSION)),
        )
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Migrate the database schema to the latest version.

        Delegates to the extracted, versioned migration chain in
        tuneshift.persistence.migrations. db.py remains the public persistence
        entry point, so the documented contract still holds at the API level.
        """
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        if row is None:
            return
        run_migrations(self.conn, int(row[0]), _SCHEMA_VERSION)
