"""Per-playlist criterion precedence and conflict resolution (AC-C4 / AC-C7).

Precedence order is the backbone of per-playlist selection: it is derived from
the preference cascade (``track`` outranks ``playlist`` outranks ``global``;
declared order preserved within a scope) and it decides conflicts between soft
preferences that pull toward different candidates.

Conflict resolution is *lexicographic*, never a weighted average: walking the
precedence order from highest to lowest, the first criterion on which the
surviving contenders disagree eliminates the disfavoured ones. This guarantees
the higher-precedence preference dominates and that the winner is always a
candidate some preference actually wanted — a plain weighted sum could let a
large-weight low-precedence preference win, or let a candidate neither
preference favoured slip through. Every elimination step is recorded in a trace
so ``explain`` (AC-CLI3) can show which preference won and over whom.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field

from tuneshift.matching.criteria import Strength, Verdict

# Scope precedence: lower rank = higher precedence (resolved first).
SCOPE_RANK: dict[str, int] = {"track": 0, "playlist": 1, "global": 2}


@dataclass(frozen=True)
class PreferenceRef:
    """A single active preference: a strength attached to a criterion+target.

    ``scope`` is one of ``track`` / ``playlist`` / ``global`` and determines
    precedence relative to other refs (see :data:`SCOPE_RANK`).
    """

    criterion: str
    strength: Strength
    target: str
    scope: str = "global"


def derive_precedence(
    *,
    global_refs: list[PreferenceRef],
    playlist_refs: list[PreferenceRef],
    track_refs: list[PreferenceRef],
) -> list[PreferenceRef]:
    """Flatten the cascade into a single inspectable precedence order.

    Highest precedence first: all track refs (in declared order), then playlist
    refs, then global refs. This *is* the per-playlist precedence order that
    :func:`resolve_conflict` walks and that ``explain`` surfaces.
    """

    ordered: list[PreferenceRef] = []
    for scope, refs in (
        ("track", track_refs),
        ("playlist", playlist_refs),
        ("global", global_refs),
    ):
        for ref in refs:
            ordered.append(
                ref
                if ref.scope == scope
                else PreferenceRef(
                    criterion=ref.criterion,
                    strength=ref.strength,
                    target=ref.target,
                    scope=scope,
                )
            )
    return ordered


@dataclass(frozen=True)
class ConflictStep:
    """One lexicographic elimination step in a conflict resolution."""

    criterion: str
    favored: list[Hashable]
    eliminated: list[Hashable]


@dataclass
class ConflictDecision:
    """Outcome of resolving a soft-preference conflict.

    ``winner`` is ``None`` when precedence could not distinguish the remaining
    ``contenders`` (the caller then falls back to the deterministic tie-break,
    AC-C6). ``decided_by`` names the criterion that produced a unique winner.
    """

    winner: Hashable | None
    contenders: list[Hashable]
    trace: list[ConflictStep] = field(default_factory=list)

    @property
    def unresolved(self) -> bool:
        return self.winner is None

    @property
    def decided_by(self) -> str | None:
        return (
            self.trace[-1].criterion if self.winner is not None and self.trace else None
        )


def _favor(verdict: Verdict) -> int:
    """Rank a verdict for lexicographic comparison: +1 favour, -1 disfavour."""

    if verdict in (Verdict.SOFT_BONUS, Verdict.HARD_PASS):
        return 1
    if verdict in (Verdict.SOFT_PENALTY, Verdict.HARD_REJECT):
        return -1
    return 0  # NEUTRAL / NO_VERDICT


def resolve_conflict(
    candidate_verdicts: dict[Hashable, dict[PreferenceRef, Verdict]],
    precedence: list[PreferenceRef],
) -> ConflictDecision:
    """Resolve which candidate wins by lexicographic precedence, with a trace.

    ``candidate_verdicts`` maps each candidate to the verdict *each active
    preference* assigned it, keyed by the :class:`PreferenceRef` itself (NOT by
    criterion name — two preferences may reference the same criterion at
    different scopes/targets, e.g. a track override of a global default, and
    those must stay distinct). Walking ``precedence`` highest-first, each
    preference keeps only the contenders it favours most; the first preference
    that yields a unique survivor decides. If the order is exhausted with more
    than one survivor the decision is ``unresolved`` and the caller applies the
    tie-break.
    """

    contenders: list[Hashable] = list(candidate_verdicts)
    trace: list[ConflictStep] = []

    for ref in precedence:
        if len(contenders) <= 1:
            break
        ranks = {
            c: _favor(candidate_verdicts[c].get(ref, Verdict.NO_VERDICT))
            for c in contenders
        }
        best = max(ranks.values())
        favored = [c for c in contenders if ranks[c] == best]
        if 0 < len(favored) < len(contenders):
            eliminated = [c for c in contenders if c not in favored]
            trace.append(
                ConflictStep(
                    criterion=ref.criterion, favored=favored, eliminated=eliminated
                )
            )
            contenders = favored

    winner = contenders[0] if len(contenders) == 1 else None
    return ConflictDecision(winner=winner, contenders=contenders, trace=trace)


__all__ = [
    "SCOPE_RANK",
    "ConflictDecision",
    "ConflictStep",
    "PreferenceRef",
    "derive_precedence",
    "resolve_conflict",
]
