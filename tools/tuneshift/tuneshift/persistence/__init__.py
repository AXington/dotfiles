"""Persistence implementation package for :class:`tuneshift.db.Database`.

``db.py`` remains the single public persistence entry point (the documented
"db.py owns persistence" contract). Its implementation is extracted, one
responsibility at a time, into modules here (schema, migrations, tracks,
playlists, platform, meta) which ``Database`` composes as mixins. Keeping the
extractions in a *sibling* package avoids the impossible ``db.py``-file vs
``db/``-package name collision while letting each area shrink to a reviewable
size (ARCH-H1).

:class:`_Conn` is the structural type of the shared ``sqlite3`` connection
handle. Extracted mixins annotate the ``conn`` attribute they rely on against
this protocol so they never need to import ``db.py`` (which would recreate a
cycle).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

# Parameters accepted by sqlite3's execute family: a positional sequence or a
# named mapping (for ":name" style placeholders).
_SqlParams = Sequence[Any] | Mapping[str, Any]


@runtime_checkable
class _Conn(Protocol):
    """Structural subset of :class:`sqlite3.Connection` used by persistence code.

    A real ``sqlite3.Connection`` satisfies this protocol; typing against it
    keeps the persistence mixins decoupled from ``db.py``.
    """

    row_factory: Any

    def execute(self, sql: str, parameters: _SqlParams = ..., /) -> sqlite3.Cursor: ...

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor: ...

    def commit(self) -> None: ...
