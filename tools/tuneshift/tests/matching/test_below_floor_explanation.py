"""A below-floor verdict must be explained by the evidence the floor used.

BUG-13 commit 3b. The accept floor scores on ``quality_score``, which excludes
preference-grade (``pref:``) signals. If the audit still names the worst signal
across ALL signals, a quarantine can be blamed on a preference that provably did
not cause it: the scoring would be right and the explanation would be lying.

These tests cover ``_build_audit``'s reason-code DERIVATION, which had no
coverage before this commit. ``tests/test_explain_cmd.py`` constructs a
``MatchAudit`` directly, so it exercises display only and would not have caught
a derivation change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tuneshift.matching import Availability, ReasonCode
from tuneshift.reconcile import _build_audit, _decisive_signal


@dataclass
class T:
    title: str = ""
    artist: str = ""
    album: str | None = None
    isrc: str | None = None
    duration_seconds: int | None = None


@dataclass
class Cand:
    title: str = ""
    artist: str = ""
    album: str = ""
    platform_id: str = "p1"
    isrc: str | None = None
    duration_seconds: int | None = None
    explicit: bool | None = None
    available: bool = True
    tags: list[str] = field(default_factory=list)


ALBUM = "Like A Prayer"
SOURCE = T("Vogue", "Madonna", ALBUM, None, 319)


def _audit(candidate: Cand, *, score: int, confidence: str = "not_found"):
    return _build_audit(
        track=SOURCE,
        platform_name="tidal",
        scored=[(score, 0, candidate)],
        confidence=confidence,
        prefer=frozenset(),
        avoid=frozenset(),
    )


class TestDecisiveSignalQualityOnly:
    def test_quality_only_drops_preference_signals(self):
        # A radio edit the source lacks is preference-grade after 3b.
        cand = Cand("Vogue (Radio Edit)", "Madonna", ALBUM, duration_seconds=319)
        full = _decisive_signal(SOURCE, cand, frozenset(), frozenset())
        floor = _decisive_signal(
            SOURCE, cand, frozenset(), frozenset(), quality_only=True
        )
        assert full is not None and full.startswith("pref:")
        assert floor is None or not floor.startswith("pref:")

    def test_quality_only_keeps_identity_signals(self):
        cand = Cand(
            "Vogue (Karaoke Version)", "Madonna", ALBUM, duration_seconds=319
        )
        floor = _decisive_signal(
            SOURCE, cand, frozenset(), frozenset(), quality_only=True
        )
        assert floor == "version:reject"


class TestBelowFloorReasonCodeDerivation:
    def test_preference_is_never_named_as_the_cause_of_a_quarantine(self):
        """The regression this commit exists to prevent.

        The candidate's worst signal overall is preference-grade, but the floor
        never scored it. Naming it would tell an operator to change a
        preference that had nothing to do with the outcome.
        """
        cand = Cand(
            "Vogue (Radio Edit)", "Madonna", ALBUM, duration_seconds=319
        )
        audit = _audit(cand, score=40)
        assert audit.availability == Availability.NOT_FOUND
        assert audit.decisive_signal is None or not audit.decisive_signal.startswith(
            "pref:"
        )
        assert audit.reason_code == ReasonCode.ALL_BELOW_THRESHOLD

    def test_a_real_version_rejection_still_reports_version_rejected(self):
        # The narrowing must not cost a genuine wrong-recording explanation.
        cand = Cand(
            "Vogue (Karaoke Version)", "Madonna", ALBUM, duration_seconds=319
        )
        audit = _audit(cand, score=0)
        assert audit.availability == Availability.NOT_FOUND
        assert audit.reason_code == ReasonCode.VERSION_REJECTED
        assert audit.decisive_signal == "version:reject"

    def test_ranking_verdicts_keep_the_preference_inclusive_signal(self):
        """Only the floor branch is quality-only.

        A confident pick is a RANKING outcome, and a preference is exactly the
        signal that should explain why one candidate beat another.
        """
        cand = Cand(
            "Vogue (Radio Edit)", "Madonna", ALBUM, duration_seconds=319
        )
        audit = _audit(cand, score=80, confidence="high")
        assert audit.availability != Availability.NOT_FOUND
        assert audit.decisive_signal is not None
        assert audit.decisive_signal.startswith("pref:")
