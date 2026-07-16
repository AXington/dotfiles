"""Match auditing and availability model for the reconcile pipeline.

This module makes matching *explainable*. Every reconcile decision can be
captured as a :class:`MatchAudit`, what was chosen, what was rejected and why,
which signal was decisive, and whether a durable lock forced the outcome. The
``tuneshift explain`` command surfaces this record so a human can see exactly why
the engine picked (or refused to pick) a platform track.

It also defines the graded availability model. A core Alice failure mode is the
tool saying a track "doesn't exist" when it is merely blocked in her region or
tier. Distinguishing :data:`Availability.EXACT_UNAVAILABLE` (known but blocked)
from :data:`Availability.NOT_FOUND` (genuinely absent) is what fixes that. Where
a platform cannot tell the two apart (YouTube Music, per the availability
spike), the honest verdict is :data:`Availability.AMBIGUOUS`, never a confident
"not found".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


class Availability:
    """The outcome availability states a reconcile can resolve to.

    These are the *result-level* states (distinct from a raw per-candidate
    ``available`` bool). They are plain strings so they serialize cleanly into
    the DB and JSON without an enum-encoding step.
    """

    #: The requested recording is available and was matched.
    EXACT_AVAILABLE = "exact_available"
    #: The recording is known to the platform but blocked here (region/tier).
    #: NOT the same as "does not exist" - it is held, never silently dropped.
    EXACT_UNAVAILABLE = "exact_unavailable"
    #: The exact recording was not available, but an acceptable fallback
    #: (e.g. the studio master for a live source) is and was surfaced.
    SUBSTITUTE_AVAILABLE = "substitute_available"
    #: The platform cannot distinguish blocked-vs-absent (YT Music), or the top
    #: candidates are too close to choose confidently - needs human review.
    AMBIGUOUS = "ambiguous"
    #: Genuinely no acceptable candidate on a platform we *can* trust.
    NOT_FOUND = "not_found"

    ALL = frozenset(
        {
            EXACT_AVAILABLE,
            EXACT_UNAVAILABLE,
            SUBSTITUTE_AVAILABLE,
            AMBIGUOUS,
            NOT_FOUND,
        }
    )


class ReasonCode:
    """Machine-stable reason codes attached to every non-trivial outcome.

    A reason code accompanies every non-``matched`` result so downstream tooling
    (doctor, review, ``why``) can explain and cluster outcomes without parsing
    prose.
    """

    #: Chosen with clear confidence.
    MATCHED = "matched"
    #: A durable user lock determined the outcome.
    LOCKED = "locked"
    #: No candidate returned by any search strategy.
    NO_CANDIDATES = "no_candidates"
    #: Candidates existed but all scored below the acceptance threshold.
    ALL_BELOW_THRESHOLD = "all_below_threshold"
    #: The best candidate was the wrong recording class (live/karaoke/cover...).
    VERSION_REJECTED = "version_rejected"
    #: The exact recording exists but is blocked in this market/region.
    BLOCKED_IN_MARKET = "blocked_in_market"
    #: The exact recording exists but requires a higher subscription tier.
    TIER_RESTRICTED = "tier_restricted"
    #: The top candidates are too close together to choose confidently.
    AMBIGUOUS_TOP = "ambiguous_top"
    #: The platform cannot tell blocked-vs-absent, so we refuse a hard verdict.
    PLATFORM_CANNOT_DISTINGUISH = "platform_cannot_distinguish"
    #: An acceptable non-exact recording was chosen as a fallback.
    SUBSTITUTED = "substituted"
    #: A locked recording's platform id went dead; re-bound to an equivalent
    #: live id for the SAME recording (verified by fingerprint + version class).
    LOCK_HEALED = "lock_healed"
    #: A locked recording is gone from the platform; held as unavailable rather
    #: than silently swapped to a different recording.
    LOCK_HELD = "lock_held"


@dataclass(frozen=True)
class RejectedCandidate:
    """A candidate that was not chosen, with the reason it lost.

    ``decisive_signal`` is the name of the signal that most hurt this candidate
    (e.g. ``version:reject``, ``duration``, ``title``), the single most useful
    thing to show a human asking "why not this one?". ``rejection`` is the
    machine-stable *class* of that loss for the failed-match explain surface
    (AC-CLI5): ``unavailable`` (blocked/tier-gated), ``hard_filter`` (failed an
    active require/forbid criterion), ``below_threshold`` (scored too low), or
    ``lost`` (out-ranked by a better identity match). ``rejection_detail``
    carries the specific hard filter (``criterion=target``) when known.
    """

    platform_id: str
    title: str
    artist: str
    album: str
    score: int
    decisive_signal: str | None = None
    rejection: str | None = None
    rejection_detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RejectedCandidate:
        return cls(
            platform_id=data["platform_id"],
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            score=int(data.get("score", 0)),
            decisive_signal=data.get("decisive_signal"),
            rejection=data.get("rejection"),
            rejection_detail=data.get("rejection_detail"),
        )


@dataclass(frozen=True)
class CriterionOutcome:
    """An active user-preference criterion and how it acted in this decision.

    ``kind`` is ``hard`` (require/forbid, a Phase-1 filter) or ``soft``
    (prefer/avoid, a Phase-2 ranking bias). ``fired`` is True when the criterion
    actually affected the outcome: a hard criterion that eliminated >=1 candidate,
    or a soft criterion that broke the winning tie. A hard criterion that
    eliminated nothing (e.g. every candidate already satisfied it, or none could
    be judged) is recorded with ``fired=False`` so the explain surface shows it
    was in force yet inert, the "mono demoted to soft" transparency (AC-CLI3).
    """

    criterion: str
    strength: str
    kind: str
    target: str | None = None
    fired: bool = False

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CriterionOutcome:
        return cls(
            criterion=data["criterion"],
            strength=data["strength"],
            kind=data["kind"],
            target=data.get("target"),
            fired=bool(data.get("fired", False)),
        )


@dataclass(frozen=True)
class SignalContribution:
    """One signal's weighted contribution to the winner's match distance (AC-CLI3).

    Sourced from :pyattr:`~tuneshift.matching.engine.Distance.breakdown`;
    ``contribution`` is the clamped ``penalty * weight`` distance term (higher =
    hurt the match more).
    """

    name: str
    contribution: float
    weight: float

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SignalContribution:
        return cls(
            name=data["name"],
            contribution=float(data.get("contribution", 0.0)),
            weight=float(data.get("weight", 0.0)),
        )


@dataclass(frozen=True)
class MatchAudit:
    """An explainable record of a single reconcile decision.

    Captures the chosen track (if any), the runner-up candidates that lost, the
    decisive signal and distance behind the pick, the availability verdict and
    reason code, and whether a durable lock forced the outcome. Serializes to a
    compact JSON string for persistence alongside the platform mapping.

    ``criteria`` records the active user-preference criteria (hard vs soft, and
    whether each fired), ``signal_breakdown`` the winner's weighted per-signal
    distance breakdown, and ``tie_break`` the preference criterion that resolved
    a within-delta contention by precedence, together the AC-CLI3 match
    explanation. The per-candidate ``rejection`` fields drive the AC-CLI5
    failed-match explanation.
    """

    availability: str
    reason_code: str
    chosen_platform_id: str | None = None
    chosen_score: int = 0
    decisive_signal: str | None = None
    distance: float | None = None
    rejected: list[RejectedCandidate] = field(default_factory=list)
    locked: bool = False
    note: str | None = None
    criteria: list[CriterionOutcome] = field(default_factory=list)
    signal_breakdown: list[SignalContribution] = field(default_factory=list)
    tie_break: str | None = None

    def __post_init__(self) -> None:
        if self.availability not in Availability.ALL:
            raise ValueError(
                f"unknown availability state {self.availability!r}; "
                f"expected one of {sorted(Availability.ALL)}"
            )

    def as_dict(self) -> dict:
        data = asdict(self)
        data["rejected"] = [r.as_dict() for r in self.rejected]
        data["criteria"] = [c.as_dict() for c in self.criteria]
        data["signal_breakdown"] = [s.as_dict() for s in self.signal_breakdown]
        return data

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> MatchAudit:
        return cls(
            availability=data["availability"],
            reason_code=data["reason_code"],
            chosen_platform_id=data.get("chosen_platform_id"),
            chosen_score=int(data.get("chosen_score", 0)),
            decisive_signal=data.get("decisive_signal"),
            distance=data.get("distance"),
            rejected=[RejectedCandidate.from_dict(r) for r in data.get("rejected", [])],
            locked=bool(data.get("locked", False)),
            note=data.get("note"),
            criteria=[CriterionOutcome.from_dict(c) for c in data.get("criteria", [])],
            signal_breakdown=[
                SignalContribution.from_dict(s)
                for s in data.get("signal_breakdown", [])
            ],
            tie_break=data.get("tie_break"),
        )

    @classmethod
    def from_json(cls, raw: str) -> MatchAudit:
        return cls.from_dict(json.loads(raw))


_REASON_TEXT = {
    ReasonCode.MATCHED: "clear match",
    ReasonCode.LOCKED: "a durable lock decided this",
    ReasonCode.NO_CANDIDATES: "no candidate returned by any search strategy",
    ReasonCode.ALL_BELOW_THRESHOLD: "candidates existed but all scored too low",
    ReasonCode.VERSION_REJECTED: "the best candidate was the wrong version (live/cover/karaoke/...)",  # noqa: E501
    ReasonCode.BLOCKED_IN_MARKET: "the exact recording exists but is blocked in this market",  # noqa: E501
    ReasonCode.TIER_RESTRICTED: "the exact recording exists but needs a higher subscription tier",  # noqa: E501
    ReasonCode.AMBIGUOUS_TOP: "the top candidates were too close to choose confidently",
    ReasonCode.PLATFORM_CANNOT_DISTINGUISH: "this platform can't tell blocked from absent",  # noqa: E501
    ReasonCode.SUBSTITUTED: "an acceptable non-exact version was chosen as a fallback",
    ReasonCode.LOCK_HEALED: "the locked track's id changed; re-bound to the same recording",  # noqa: E501
    ReasonCode.LOCK_HELD: "the locked recording is gone; held rather than swapped to a different one",  # noqa: E501
}

_AVAILABILITY_TEXT = {
    Availability.EXACT_AVAILABLE: "exact version available",
    Availability.EXACT_UNAVAILABLE: "exact version found but not playable",
    Availability.SUBSTITUTE_AVAILABLE: "substitute version available",
    Availability.AMBIGUOUS: "ambiguous, needs review",
    Availability.NOT_FOUND: "not found",
}


def describe_reason(reason_code: str) -> str:
    """Human-readable one-liner for a machine-stable reason code."""
    return _REASON_TEXT.get(reason_code, reason_code)


def describe_availability(state: str) -> str:
    """Human-readable one-liner for an availability state."""
    return _AVAILABILITY_TEXT.get(state, state)


__all__ = [
    "Availability",
    "MatchAudit",
    "ReasonCode",
    "RejectedCandidate",
    "describe_availability",
    "describe_reason",
]
