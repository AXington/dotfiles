"""Schema-migration equivalence guard (ARCH-H1, Task 3.3 safety net).

The migration chain (schema v2..v22) is being extracted from the monolithic
``db.py:_migrate_schema`` into ``persistence/migrations/``. That extraction must
be behavior-preserving: migrating the oldest supported database (a v1 seed) up
to head must yield exactly the same schema objects, byte-for-byte, as before.

This test pins that invariant. It seeds a v1 database, opens it through
``Database`` (which runs the full migration chain inside one transaction), then
snapshots the normalized ``sqlite_master`` and compares it to a golden fixture
captured from the pre-extraction code. Any DDL drift -- a reordered ALTER, a
dropped index, a changed CREATE -- fails here.

The golden lives in ``tests/fixtures/schema_v1_to_head.txt``. Regenerate it
deliberately (and review the diff) only when a *new* migration is intentionally
added.
"""

import sqlite3
from pathlib import Path

from tuneshift.db import Database

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema_v1_to_head.txt"

# The oldest supported on-disk shape (schema_meta version 1). Opening this with
# Database runs migrations 2..22. Kept minimal: the migration DDL is what shapes
# sqlite_master, so an empty-ish seed fully exercises the schema invariant.
_V1_SEED = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO schema_meta VALUES ('version', '1');
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    isrc TEXT,
    duration_seconds INTEGER,
    norm_title TEXT,
    norm_artist TEXT,
    norm_album TEXT
);
INSERT INTO tracks (title, artist, album) VALUES ('Heroes', 'David Bowie', 'Heroes');
"""


def _snapshot(db_path: Path) -> str:
    """Return a normalized, order-stable dump of user-defined schema objects."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type, name, tbl_name"
        ).fetchall()
    finally:
        conn.close()
    lines = [f"{t}\t{n}\t{tbl}\t{' '.join(sql.split())}" for t, n, tbl, sql in rows]
    return "\n".join(lines) + "\n"


def test_v1_to_head_schema_matches_golden(tmp_path: Path) -> None:
    """Migrating a v1 seed to head reproduces the pinned schema exactly."""
    db_path = tmp_path / "v1.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(_V1_SEED)
    seed.close()

    db = Database(db_path)
    _ = db.conn  # force open + run migrations
    db.close()

    got = _snapshot(db_path)
    expected = _FIXTURE.read_text()
    assert got == expected, (
        "Migrated schema drifted from the golden fixture. If a new migration "
        "was intentionally added, regenerate tests/fixtures/schema_v1_to_head.txt "
        "and review the diff.\n\n--- got ---\n" + got
    )
