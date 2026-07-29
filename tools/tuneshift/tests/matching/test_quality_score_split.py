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
        """The exact shape BUG-13 describes, minus the preference contribution.

        A radio edit the source does not carry (-20) plus a duration mismatch,
        on a candidate that also happens to be clean (-10). Removing only the
        preference must leave it below the floor, because the remaining
        penalties are real. If this ever passes the floor the split has become
        an amnesty.
        """
        scores = _score(
            "Vogue (Radio Edit)",
            cand_album="Now Thats What I Call Music",
            duration=200,
            cand_explicit=False,
        )
        assert scores.match_score == 35
        assert scores.quality_score == 45
        assert scores.quality_score < NOT_FOUND_FLOOR

    def test_default_edition_downrank_is_quality_not_preference(self):
        # "Candidate carries a marker the source lacks" is a genuine mismatch
        # against the source, so it must survive into quality_score.
        marked = _score("Vogue (Radio Edit)", cand_album="Now Thats What I Call Music")
        clean_source = _score()
        assert marked.quality_score < clean_source.quality_score


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
        assert not is_preference_signal("version:reject")
        assert not is_preference_signal("version:radio_edit")
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
