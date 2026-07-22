"""Ordered schema migration chain, extracted from db.py:_migrate_schema.

Each historical `if current_version < N` block is now one module (vNNN.apply).
db.py delegates to run_migrations, staying the public persistence entry point.
"""

from __future__ import annotations

from collections.abc import Callable

from tuneshift.persistence import _Conn
from tuneshift.persistence.migrations import (
    v002,
    v003,
    v004,
    v005,
    v006,
    v007,
    v008,
    v009,
    v010,
    v011,
    v012,
    v013,
    v014,
    v015,
    v016,
    v017,
    v018,
    v019,
    v020,
    v021,
    v022,
)

# (target schema version, apply function). Applied in ascending order inside a
# single transaction, matching the original monolithic method exactly.
MIGRATIONS: list[tuple[int, Callable[[_Conn], None]]] = [
    (2, v002.apply),
    (3, v003.apply),
    (4, v004.apply),
    (5, v005.apply),
    (6, v006.apply),
    (7, v007.apply),
    (8, v008.apply),
    (9, v009.apply),
    (10, v010.apply),
    (11, v011.apply),
    (12, v012.apply),
    (13, v013.apply),
    (14, v014.apply),
    (15, v015.apply),
    (16, v016.apply),
    (17, v017.apply),
    (18, v018.apply),
    (19, v019.apply),
    (20, v020.apply),
    (21, v021.apply),
    (22, v022.apply),
]


def run_migrations(conn: _Conn, current_version: int, target_version: int) -> None:
    """Apply every migration newer than current_version, then record the head.

    Runs inside one transaction (via the connection context manager) so a
    failure leaves the schema untouched, preserving the original all-or-nothing
    behavior.
    """
    if current_version >= target_version:
        return
    with conn:
        for version, apply_step in MIGRATIONS:
            if current_version < version:
                apply_step(conn)
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'version'",
            (str(target_version),),
        )
