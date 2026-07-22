"""Shared cross-package data-transfer objects.

This module holds pure data records that more than one package needs but that
would otherwise force an import cycle. ``db.py`` returns :class:`ReviewItem`
(owned conceptually by ``matching``) and :class:`JournalEntry` (owned by
``planapply``); at the same time ``matching`` and ``planapply`` both depend on
``db.Database``. Housing these records here -- with NO ``tuneshift`` imports of
its own -- lets every package depend on ``tuneshift.types`` instead of on each
other, so ``db.py`` can import them at module scope rather than lazily inside
methods to dodge the cycle (ARCH-M2).

Invariant: this module must never import from any other ``tuneshift`` module.
``tests/test_no_import_cycles.py`` enforces that; breaking it reintroduces the
cycle it exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewItem:
    """One track outcome that may require human review."""

    track_id: int
    title: str
    artist: str
    album: str | None
    platform: str
    availability: str
    reason_code: str
    playlist_id: int | None = None
    playlist_name: str | None = None


@dataclass
class JournalEntry:
    """One recorded write, used to reverse an applied plan (AC-P4)."""

    id: int
    plan_id: str
    table_name: str
    row_key: str
    op: str
    prior_value: dict | None
    new_value: dict | None
    applied_at: str
