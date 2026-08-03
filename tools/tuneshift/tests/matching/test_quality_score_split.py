"""A preference may pick the winner; it must never decide the track was lost.

BUG-13 commit 3 (Hybrid B). ``match_score`` answers "which candidate is best?"
and legitimately includes preference penalties. The two ABSOLUTE accept floors
(``RESOLVE_ACCEPT_FLOOR`` in the resolve worker and ``NOT_FOUND_FLOOR`` in
:func:`classify_scores`) answer a different question, "did we find anything
real at all?", and must therefore see quality evidence only.

The risk in this split is OVER-forgiveness: classify something as preference
that is really a defect and a bad match sails through a gate that exists to
catch it. The negative tests come first here for that reason.
"""

from tuneshift.matching.confidence import NOT_FOUND_FLOOR, classify_scores
from tuneshift.matching.penalties import is_preference_signal
from tuneshift.matching.track import score_match_components, score_match_with_version

SOURCE = ("Vogue", "Madonna", "The Immaculate Collection")


def _score(
    cand_title="Vogue",
    cand_artist="Madonna",
    cand_album="The Immaculate Collection",
    *,
    duration=319,
    reference=319,
    **kwargs,
):
    return score_match_components(
        *SOURCE,
        cand_title,
        cand_artist,
        cand_album,
        result_duration=duration,
        reference_duration=reference,
        **kwargs,
    )


class TestQualityScoreForgivesNothingReal:
    """The floor must still catch every genuine defect after the split."""

    def test_wrong_recording_stays_below_the_floor(self):
        # A live take for a studio source is a wrong-recording REJECT. The clean
        # flag is incidental; stripping the preference must not rescue it.
        scores = _score(
            "Vogue (Live)", cand_album="Blond Ambition Tour", cand_explicit=False
        )
        assert scores.match_score == 0
        assert scores.quality_score < NOT_FOUND_FLOOR

    def test_wrong_artist_hard_reject_is_zero_in_both_projections(self):
        # artist_overlap_absent short-circuits to 0. That is an identity
        # judgement, not a preference, so both scores must see it.
        scores = _score(
            cand_artist="Kidz Bop Kids", cand_album="Kidz Bop 5", cand_explicit=False
        )
        assert scores.match_score == 0
        assert scores.quality_score == 0

    def test_compound_defect_plus_preference_still_quarantines(self):
        """Real defects plus a preference must still land below the floor.

        Scoped to the IDENTITY axis deliberately. Commit 3b moved the whole
        edition axis to preference-grade, so an edition mismatch is no longer a
        defect for floor purposes; using one here would test the opposite of
        the current rule. A karaoke cut is a wrong recording, and the clean
        flag (-10) is incidental. Removing only the preference must leave this
        below the floor. If it ever passes, the split has become an amnesty.
        """
        scores = _score(
            "Vogue (Karaoke Version)",
            cand_album="Karaoke Hits",
            duration=200,
            cand_explicit=False,
        )
        assert scores.quality_score < NOT_FOUND_FLOOR

    def test_default_edition_downrank_is_preference_not_quality(self):
        """Reverses the rule this test asserted in commit 3a. Deliberate.

        3a kept the default (user-silent) edition down-rank in quality_score on
        the reasoning that "candidate carries a marker the source lacks" is a
        real mismatch. That left quality_score preference-DEPENDENT: a listener
        who configured ``avoid`` saw the penalty excluded while a silent
        listener saw it charged, so the same candidate could clear the floor
        for one and be quarantined for the other. That is BUG-13's own defect
        pointing the permissive way, so 3b moved all three regimes to ``pref:``.

        The edition axis is a ranking axis. Identity is the ``version:`` axis
        (reject/karaoke/instrumental/substitute), which is untouched and still
        charged in full at the floor.
        """
        # Hold the album constant so only the edition marker differs; changing
        # the album too would mix in a real album-mismatch penalty.
        marked = _score("Vogue (Radio Edit)")
        unmarked = _score()
        # Ranking still separates them: the album cut wins.
        assert marked.match_score < unmarked.match_score
        # The floor does not: both are Vogue.
        assert marked.quality_score == unmarked.quality_score

    def test_bug18_coupling_edition_plus_saturated_duration_clears_the_floor(self):
        """BUG-18 COUPLING PIN. Read this before "fixing" a failure here.

        This candidate is a 200s radio edit on a compilation against a 319s
        album source. Before 3b it scored 45 quality and was quarantined; it
        now scores 65 and resolves, because every edition penalty left the
        quality axis and duration is the only real defect remaining.

        Duration SHOULD be doing that work and cannot: it saturates at -20
        (BUG-18), so it can never sink a candidate on its own. This test
        therefore pins a value that is only correct while BUG-18 is unfixed.
        It is deliberately coupled to its opposite number:

            tests/matching/test_residual_axis_is_preference.py
              ::TestIdentityAxisStillGatesTheFloor
              ::test_a_wildly_wrong_duration_still_sinks_a_radio_edit

        which is a strict xfail asserting the behaviour we actually want.

        MEASURED coupling, not assumed. This candidate's ratio is 200/319 =
        0.627, which lands in the `ratio < 0.65` band (duration_short_high),
        while the xfail's 40s candidate lands in `ratio < 0.5`
        (duration_short_max). Both were simulated:

          deepen duration_short_max only   -> xfail XPASSes, THIS TEST STILL
                                              PASSES. Only one side goes red.
          deepen the whole short side      -> both go red together.

        So a partial BUG-18 fix can trip the xfail alone. If you are here
        because only that one failed, this pin is the thing you have not fixed
        yet: the 0.65 band still forgives a 119 second gap.

        When both go red, BUG-18 is properly fixed and the correct response is:
        delete this pin and remove that xfail marker. Do NOT lower
        NOT_FOUND_FLOOR, weaken the assertion, or restore edition penalties to
        the quality axis to make this pass again. A 119 second gap is a
        different recording, not an edition preference, and the floor rejecting
        it is the outcome BUG-18 exists to obtain.
        """
        scores = _score(
            "Vogue (Radio Edit)",
            cand_album="Now Thats What I Call Music",
            duration=200,
            cand_explicit=False,
        )
        assert scores.match_score == 35
        assert scores.quality_score == 65
        assert scores.quality_score >= NOT_FOUND_FLOOR


class TestPreferenceNeverSinksACandidate:
    def test_clean_only_candidate_clears_the_floor(self):
        """The BUG-13 symptom itself.

        An otherwise perfect candidate whose only fault is carrying the
        non-preferred lyric rating. It may rank below an explicit alternative,
        but it must not be treated as "nothing was found".
        """
        scores = _score(cand_explicit=False)
        assert scores.match_score == 90  # still ranked below an explicit release
        assert scores.quality_score == 100  # nothing is actually wrong with it
        assert scores.quality_score >= NOT_FOUND_FLOOR

    def test_preference_is_the_entire_gap(self):
        clean = _score(cand_explicit=False)
        explicit = _score(cand_explicit=True)
        assert explicit.match_score - clean.match_score == 10
        assert explicit.quality_score == clean.quality_score

    def test_absent_rating_is_unaffected(self):
        # No rating captured means no preference signal fires at all.
        scores = _score()
        assert scores.match_score == scores.quality_score == 100


class TestMatchScoreIsUnchanged:
    """The split must not perturb ranking. Parity is structural, not asserted:
    both numbers come from one pass over one signal list. These cases pin it."""

    def test_wrapper_agrees_with_components(self):
        cases = [
            {},
            {"cand_explicit": True},
            {"cand_explicit": False},
            {"cand_explicit": False, "prefer": frozenset({"clean"})},
            {"cand_title": "Vogue (Radio Edit)", "cand_explicit": False},
            {"cand_title": "Vogue (Live)", "cand_album": "Blond Ambition Tour"},
            {"cand_artist": "Kidz Bop Kids"},
        ]
        for case in cases:
            components = _score(**case)
            legacy = score_match_with_version(
                *SOURCE,
                case.get("cand_title", "Vogue"),
                case.get("cand_artist", "Madonna"),
                case.get("cand_album", "The Immaculate Collection"),
                result_duration=319,
                reference_duration=319,
                **{
                    k: v
                    for k, v in case.items()
                    if k not in ("cand_title", "cand_artist", "cand_album")
                },
            )
            assert components.match_score == legacy, case

    def test_quality_never_below_match(self):
        # quality_score omits only penalties, so it can never be the harsher
        # of the two. A violation means a signal was double-counted.
        for kwargs in ({}, {"cand_explicit": False}, {"cand_title": "Vogue (Live)"}):
            scores = _score(**kwargs)
            assert scores.quality_score >= scores.match_score


class TestPreferenceSignalClassification:
    def test_pref_prefix_is_the_single_definition(self):
        assert is_preference_signal("pref:lyric")
        assert is_preference_signal("pref:radio_edit")
        # The identity axis is what the accept floor scores on, so none of
        # these may ever read as preference-grade.
        assert not is_preference_signal("version:reject")
        assert not is_preference_signal("version:karaoke")
        assert not is_preference_signal("version:instrumental")
        assert not is_preference_signal("version:substitute")
        assert not is_preference_signal("duration")


class TestClassifyScoresFloor:
    def test_floor_uses_quality_when_supplied(self):
        # Ranked at 45 (below the floor) purely because of a preference, but
        # actually a clean 100. It was found; it must not report not_found.
        assert classify_scores([45]) == "not_found"
        assert classify_scores([45], quality_scores=[100]) != "not_found"

    def test_floor_still_rejects_a_genuinely_poor_best(self):
        assert classify_scores([45], quality_scores=[45]) == "not_found"
        assert classify_scores([20], quality_scores=[30]) == "not_found"

    def test_ranking_bands_ignore_quality_scores(self):
        """Only the not_found floor changes. high/ambiguous are ranking
        decisions, where preference participation is correct."""
        ranking = [82, 79]
        assert classify_scores(ranking) == classify_scores(
            ranking, quality_scores=[100, 100]
        )

    def test_default_is_unchanged(self):
        for scores in ([], [10], [55], [90], [90, 88], [95, 20]):
            assert classify_scores(scores) == classify_scores(
                scores, quality_scores=None
            )
