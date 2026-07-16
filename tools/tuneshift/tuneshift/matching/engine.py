"""The Distance accumulator and match recommendation.

``Distance`` is the beets-style accumulator: it collects
:class:`~tuneshift.matching.penalties.SignalPenalty` objects and exposes two
projections of the same evidence.

* ``total``, normalized weighted distance in [0.0, 1.0]; 0.0 = perfect match,
  higher = worse. ``total = sum(clamp(penalty)*weight) / sum(weight)``.
* ``points``, the raw signed legacy point sum, used by the byte-parity track
  scorer (which applies the historical staged clamping itself).

``recommend`` turns a candidate's distance (optionally with the runner-up's
distance for a gap criterion) into an action, honoring *max-recommendation
caps*: a hard version rejection (karaoke/instrumental) or an out-of-band
duration can cap the result to a non-auto action no matter how close the
strings match.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from tuneshift.matching.penalties import SignalPenalty


class Recommendation(str, Enum):
    """What to do with a candidate, by decreasing confidence."""

    AUTO = "auto"  # accept without asking
    SUGGEST = "suggest"  # propose, lightly confirm
    ASK = "ask"  # surface for explicit review
    REJECT = "reject"  # do not offer


@dataclass(frozen=True)
class RecommendationThresholds:
    """Distance cut-points for the recommendation ladder (defaults tunable)."""

    auto_max: float = 0.15  # distance <= auto_max -> AUTO
    suggest_max: float = 0.35  # <= suggest_max -> SUGGEST
    ask_max: float = 0.60  # <= ask_max -> ASK; else REJECT
    gap_min: float = 0.10  # runner-up must be at least this much worse for AUTO


DEFAULT_THRESHOLDS = RecommendationThresholds()

# Signal names (or prefixes) that cap the recommendation to non-auto.
# ``version:reject`` (source-aware wrong-recording / censored) forces REJECT;
# the legacy candidate-only karaoke/instrumental caps are retained for callers
# still building distances from ``version_signals``.
_HARD_VERSION_CAPS = frozenset(
    {"version:reject", "version:karaoke", "version:instrumental"}
)
# A source-aware substitute (fallback recording) caps to SUGGEST, never AUTO.
_SUBSTITUTE_CAP = "version:substitute"
_DURATION_CAP = "duration"


@dataclass(frozen=True)
class SignalBreakdown:
    """One signal's contribution, in both projections."""

    name: str
    penalty: float
    weight: float
    points: int
    contribution: float  # clamped_penalty * weight (share of the numerator)


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


class Distance:
    """Accumulates penalty signals for a single candidate match."""

    def __init__(self, signals: Iterable[SignalPenalty] = ()) -> None:
        self._signals: list[SignalPenalty] = list(signals)

    def add(self, signal: SignalPenalty) -> None:
        self._signals.append(signal)

    def extend(self, signals: Iterable[SignalPenalty]) -> None:
        self._signals.extend(signals)

    @property
    def signals(self) -> tuple[SignalPenalty, ...]:
        return tuple(self._signals)

    @property
    def total(self) -> float:
        """Normalized weighted distance in [0.0, 1.0]. 0.0 when no weight."""
        total_weight = sum(s.weight for s in self._signals)
        if total_weight <= 0:
            return 0.0
        numerator = sum(_clamp01(s.penalty) * s.weight for s in self._signals)
        return numerator / total_weight

    @property
    def points(self) -> int:
        """Raw signed sum of legacy point contributions (unclamped)."""
        return sum(s.points for s in self._signals)

    @property
    def breakdown(self) -> list[SignalBreakdown]:
        """Per-signal breakdown, sorted by distance contribution (worst first)."""
        rows = [
            SignalBreakdown(
                name=s.name,
                penalty=s.penalty,
                weight=s.weight,
                points=s.points,
                contribution=_clamp01(s.penalty) * s.weight,
            )
            for s in self._signals
        ]
        rows.sort(key=lambda r: r.contribution, reverse=True)
        return rows

    def has_signal(self, name: str) -> bool:
        return any(s.name == name for s in self._signals)

    def capped_recommendation(self) -> Recommendation | None:
        """Return the strongest recommendation allowed by max-rec caps, or None.

        A hard version rejection forces REJECT; an active duration penalty caps
        to SUGGEST (never AUTO). None means no cap applies.
        """
        for s in self._signals:
            if s.name in _HARD_VERSION_CAPS and s.points != 0:
                return Recommendation.REJECT
        for s in self._signals:
            if s.name == _SUBSTITUTE_CAP and s.points != 0:
                return Recommendation.SUGGEST
        for s in self._signals:
            if s.name == _DURATION_CAP and s.points != 0:
                return Recommendation.SUGGEST
        return None


def recommend(
    distance: Distance,
    *,
    runner_up: float | None = None,
    thresholds: RecommendationThresholds = DEFAULT_THRESHOLDS,
) -> Recommendation:
    """Map a candidate's distance to an action.

    - Threshold ladder on ``distance.total``.
    - Gap criterion: an AUTO requires the runner-up to be at least
      ``gap_min`` worse (larger distance); otherwise downgrade to SUGGEST.
    - Max-rec caps: hard version rejects / duration penalties cap the result.
    """
    total = distance.total
    if total <= thresholds.auto_max:
        base = Recommendation.AUTO
    elif total <= thresholds.suggest_max:
        base = Recommendation.SUGGEST
    elif total <= thresholds.ask_max:
        base = Recommendation.ASK
    else:
        base = Recommendation.REJECT

    if base is Recommendation.AUTO and runner_up is not None:
        if runner_up - total < thresholds.gap_min:
            base = Recommendation.SUGGEST

    cap = distance.capped_recommendation()
    if cap is not None and _rank(cap) > _rank(base):
        return cap
    return base


_RECOMMENDATION_ORDER = {
    Recommendation.AUTO: 0,
    Recommendation.SUGGEST: 1,
    Recommendation.ASK: 2,
    Recommendation.REJECT: 3,
}


def _rank(rec: Recommendation) -> int:
    return _RECOMMENDATION_ORDER[rec]
