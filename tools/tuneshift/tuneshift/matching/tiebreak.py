"""Deterministic tie-break for equal-scoring candidates (AC-C6).

Ties that survive filtering and conflict resolution are broken by a fixed,
documented order so the winner is never a silent arbitrary pick:

1. ``release-year``, earliest original release-year wins (a missing year is
   treated as newest, i.e. sorts last), preferring the canonical original over
   later reissues/remasters.
2. ``availability``, higher availability rank wins (a playable release beats a
   less-available one).
3. ``stable-id``, lexicographically smallest stable id wins; a total order
   that guarantees the same winner on every run regardless of input order.

Each tier only decides ties the previous tier left. :func:`tie_break` reports
which tier produced the winner so ``explain`` can justify a tie outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

# A missing original release-year is treated as "newest" so dated releases win.
_UNKNOWN_YEAR = 10_000

# Ordered (label, key-extractor) tiers. Each key is "smaller is better".
_TIERS: tuple[tuple[str, str], ...] = (
    ("release-year", "_year_key"),
    ("availability", "_availability_key"),
    ("stable-id", "_id_key"),
)


@dataclass(frozen=True)
class TieCandidate:
    """A candidate release in a tie, carrying the fields the tiers compare."""

    id: str
    release_year: int | None = None
    availability_rank: int = 0

    @property
    def _year_key(self) -> int:
        return self.release_year if self.release_year is not None else _UNKNOWN_YEAR

    @property
    def _availability_key(self) -> int:
        return -self.availability_rank  # higher rank -> smaller key -> wins

    @property
    def _id_key(self) -> str:
        return self.id


@dataclass(frozen=True)
class TieBreakResult:
    """The tie-break winner and the tier that decided it."""

    winner: str
    decided_by: str


def _sort_key(candidate: TieCandidate) -> tuple:
    return tuple(getattr(candidate, attr) for _, attr in _TIERS)


def tie_break(candidates: list[TieCandidate]) -> TieBreakResult:
    """Return the deterministic winner among tied ``candidates`` and why.

    Raises :class:`ValueError` on an empty list, a tie-break with no candidates
    is a caller bug, not a silently-swallowed no-op.
    """

    if not candidates:
        raise ValueError("tie_break requires at least one candidate")

    ordered = sorted(candidates, key=_sort_key)
    winner = ordered[0]

    if len(ordered) == 1:
        return TieBreakResult(winner=winner.id, decided_by="sole-candidate")

    # The deciding tier is the first on which the winner strictly beats the
    # closest contender (the next candidate in sorted order).
    runner_up = ordered[1]
    decided_by = "stable-id"
    for label, attr in _TIERS:
        if getattr(winner, attr) != getattr(runner_up, attr):
            decided_by = label
            break

    return TieBreakResult(winner=winner.id, decided_by=decided_by)


def all_tie_on_meaningful_tiers(candidates: list[TieCandidate]) -> bool:
    """True when every candidate shares the same release-year AND availability rank.

    In that case only the arbitrary ``stable-id`` tier could separate them, so
    :func:`tie_break`'s winner is a purely lexicographic pick. Callers use this to
    preserve INSERTION ORDER (winner-parity) instead of imposing a lexicographic
    winner on an otherwise-indistinguishable band. An empty band is vacuously
    "all tied".
    """
    if not candidates:
        return True
    keys = {(c._year_key, c._availability_key) for c in candidates}
    return len(keys) == 1


__all__ = ["TieBreakResult", "TieCandidate", "all_tie_on_meaningful_tiers", "tie_break"]
