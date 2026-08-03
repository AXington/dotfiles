"""The edition axis is a ranking axis, so it must not gate the accept floor.

The residual edition buckets (radio/single edit, compilation, deluxe) describe
packaging or an edit of the *same* recording. They answer "which copy do you
want", not "is this the track". The identity axis (``version:reject``,
``version:karaoke``, ``version:instrumental``, ``version:substitute``) answers
the second question and is untouched here.

These tests pin two things:

1. every regime of the edition axis is preference-grade, so a listener's
   configuration cannot decide whether a track resolves at all (BUG-13);
2. the identity axis still gates the floor in full, so this is a narrowing of
   what counts as a defect, not an amnesty.
"""

from __future__ import annotations

import pytest

from tuneshift.matching import penalties as pen
from tuneshift.matching.penalties import is_preference_signal
from tuneshift.matching.track import score_match_components

SRC_TITLE = "Vogue"
SRC_ARTIST = "Madonna"
SRC_ALBUM = "The Immaculate Collection"
SRC_DURATION = 319


def _residual(cand_title: str, cand_album: str = "The Immaculate Collection", **kw):
    return pen._residual_version_signals(
        SRC_TITLE, SRC_ALBUM, cand_title, cand_album, **kw
    )


def _names(signals) -> list[str]:
    return [s.name for s in signals]


class TestEveryEditionRegimeIsPreferenceGrade:
    """All three branches, not just the two the user configured explicitly."""

    def test_default_regime_is_preference_grade(self):
        # The user said nothing. A radio edit of Vogue is still Vogue, so the
        # asymmetric down-rank is a ranking opinion, not evidence of a miss.
        signals = _residual("Vogue (Radio Edit)")
        assert _names(signals) == ["pref:radio_edit"]
        assert all(is_preference_signal(s.name) for s in signals)

    def test_avoided_edition_is_preference_grade(self):
        signals = _residual("Vogue (Radio Edit)", avoid=frozenset({"radio_edit"}))
        assert _names(signals) == ["pref:radio_edit"]
        assert all(is_preference_signal(s.name) for s in signals)

    def test_preferred_edition_absent_is_preference_grade(self):
        # Candidate lacks the preferred edition, so it is down-ranked as a
        # substitute for the intent. Still a ranking call.
        signals = _residual("Vogue", prefer=frozenset({"deluxe"}))
        assert _names(signals) == ["pref:deluxe"]
        assert all(is_preference_signal(s.name) for s in signals)

    def test_compilation_bucket_is_preference_grade(self):
        # Source album must NOT itself read as a compilation, or the default
        # regime's asymmetric rule correctly emits nothing.
        signals = pen._residual_version_signals(
            SRC_TITLE, "Like A Prayer", "Vogue", "Greatest Hits"
        )
        assert _names(signals) == ["pref:compilation"]


class TestQualityScoreIsPreferenceIndependent:
    """The floor must reach the same verdict whatever the user configured.

    This is the property curation's narrower fix would have left broken: with
    only ``avoid``/``prefer`` reclassified, expressing a preference could lift a
    candidate over the floor that a silent user would have seen quarantined.
    """

    def _quality(self, **kw) -> int:
        return score_match_components(
            SRC_TITLE,
            SRC_ARTIST,
            SRC_ALBUM,
            "Vogue (Radio Edit)",
            SRC_ARTIST,
            "Greatest Hits",
            150,
            SRC_DURATION,
            **kw,
        ).quality_score

    def test_silent_and_avoiding_users_get_the_same_quality_score(self):
        silent = self._quality()
        avoiding = self._quality(avoid=frozenset({"radio_edit"}))
        assert silent == avoiding

    def test_preference_config_cannot_flip_the_floor_verdict(self):
        floor = 50
        silent = self._quality()
        avoiding = self._quality(avoid=frozenset({"radio_edit"}))
        preferring = self._quality(prefer=frozenset({"deluxe"}))
        verdicts = {s >= floor for s in (silent, avoiding, preferring)}
        assert len(verdicts) == 1


class TestRankingIsUnchanged:
    """Match score keeps every edition penalty, so preferences still decide."""

    def _match(self, cand_title: str, cand_album: str, **kw) -> int:
        return score_match_components(
            SRC_TITLE,
            SRC_ARTIST,
            SRC_ALBUM,
            cand_title,
            SRC_ARTIST,
            cand_album,
            SRC_DURATION,
            SRC_DURATION,
            **kw,
        ).match_score

    def test_avoided_edition_still_loses_to_the_clean_alternative(self):
        avoid = frozenset({"radio_edit"})
        radio = self._match("Vogue (Radio Edit)", SRC_ALBUM, avoid=avoid)
        album_cut = self._match("Vogue", SRC_ALBUM, avoid=avoid)
        assert album_cut > radio

    def test_preferred_edition_still_wins(self):
        prefer = frozenset({"deluxe"})
        standard = self._match("Vogue", SRC_ALBUM, prefer=prefer)
        expanded = self._match("Vogue", f"{SRC_ALBUM} (Expanded Edition)", prefer=prefer)
        assert expanded > standard

    def test_avoiding_costs_more_than_staying_silent(self):
        # The escalation from default to substitute grade is preserved: it is a
        # ranking signal, and reclassifying it must not flatten it.
        silent = self._match("Vogue (Radio Edit)", SRC_ALBUM)
        avoiding = self._match(
            "Vogue (Radio Edit)", SRC_ALBUM, avoid=frozenset({"radio_edit"})
        )
        assert avoiding < silent


class TestIdentityAxisStillGatesTheFloor:
    """The narrowing must not become an amnesty."""

    def _quality(self, cand_title: str, cand_duration: int = SRC_DURATION) -> int:
        return score_match_components(
            SRC_TITLE,
            SRC_ARTIST,
            SRC_ALBUM,
            cand_title,
            SRC_ARTIST,
            SRC_ALBUM,
            cand_duration,
            SRC_DURATION,
        ).quality_score

    def test_karaoke_is_still_rejected_at_the_floor(self):
        assert self._quality("Vogue (Karaoke Version)") < 50

    def test_wrong_artist_is_still_rejected_at_the_floor(self):
        score = score_match_components(
            SRC_TITLE,
            SRC_ARTIST,
            SRC_ALBUM,
            "Vogue",
            "Some Tribute Band",
            SRC_ALBUM,
            SRC_DURATION,
            SRC_DURATION,
        ).quality_score
        assert score < 50

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-18: the duration signal saturates at -20 and cannot sink any "
            "candidate below the accept floor. A 1 second clip scores the same "
            "as a 150 second one. Pre-existing and independent of the edition "
            "axis: before this change the same candidate scored 60 and still "
            "resolved, so no floor verdict changed here. Remove this marker "
            "when BUG-18 is fixed."
        ),
    )
    def test_a_wildly_wrong_duration_still_sinks_a_radio_edit(self):
        # Edition forgiven, length not: a 40 second clip is not the recording.
        assert self._quality("Vogue (Radio Edit)", 40) < 50
