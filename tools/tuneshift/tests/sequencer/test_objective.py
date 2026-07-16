"""Tests for the single global sequencing objective (SEQ-A1).

The greedy builder and the local search (``_two_opt``) must optimize one
consistent objective. Before the fix, ``_two_opt`` scored swaps with a myopic
pairwise-continuity function that ignored arc fit and context modifiers, so it
could pull a track out of its arc region to improve raw continuity, reducing
the objective the builder actually targeted.
"""

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import _two_opt, sequence_score

WEIGHTS = {"energy": 1.0}


def _track(track_id: int, energy: float, valence: float, artist: str) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=artist,
        duration_ms=200000,
        energy=energy,
        valence=valence,
    )


def _strict_descending() -> list[TrackMetadata]:
    """A strictly energy-descending sequence (arc-optimal for 'descending')."""
    return [
        _track(1, energy=0.90, valence=0.5, artist="A1"),
        _track(2, energy=0.72, valence=0.5, artist="A2"),
        _track(3, energy=0.54, valence=0.5, artist="A3"),
        _track(4, energy=0.36, valence=0.5, artist="A4"),
        _track(5, energy=0.18, valence=0.5, artist="A5"),
        _track(6, energy=0.05, valence=0.5, artist="A6"),
    ]


def _arc_conflict_input() -> list[TrackMetadata]:
    """An input where a myopic swap search degrades the arc-aware objective.

    Under ``arc="descending"``, the pre-fix myopic ``_two_opt`` reorders this to
    ``[1, 5, 3, 4, 2, 6]`` to improve raw pairwise continuity, which *reduces*
    the global ``sequence_score`` (3.818 -> 3.709) by pulling tracks out of
    their arc regions. The unified objective must never accept such a swap.
    """
    return [
        _track(1, energy=0.44, valence=0.25, artist="A1"),
        _track(2, energy=0.52, valence=0.21, artist="A2"),
        _track(3, energy=0.07, valence=0.26, artist="A3"),
        _track(4, energy=0.09, valence=0.30, artist="A4"),
        _track(5, energy=0.33, valence=0.31, artist="A5"),
        _track(6, energy=0.93, valence=0.76, artist="A6"),
    ]


class TestSequenceScoreObjective:
    def test_descending_order_scores_higher_than_reversed_under_descending_arc(self):
        seq = _strict_descending()
        forward = sequence_score(seq, WEIGHTS, arc="descending")
        backward = sequence_score(list(reversed(seq)), WEIGHTS, arc="descending")
        assert forward > backward

    def test_short_sequences_score_zero(self):
        assert sequence_score([], WEIGHTS, arc="descending") == 0.0
        assert (
            sequence_score([_track(1, 0.5, 0.5, "A")], WEIGHTS, arc="descending")
            == 0.0
        )


class TestTwoOptRespectsObjective:
    def test_two_opt_never_reduces_global_objective(self):
        """The core SEQ-A1 contract: local search optimizes the global objective.

        The pre-fix myopic ``_two_opt`` fails this assertion on this input
        (it reduces the objective from 3.818 to 3.709).
        """
        seq = _arc_conflict_input()
        before = sequence_score(seq, WEIGHTS, arc="descending")
        result = _two_opt(list(seq), WEIGHTS, arc="descending")
        after = sequence_score(result, WEIGHTS, arc="descending")
        assert after >= before - 1e-9

    def test_two_opt_preserves_endpoints_and_membership(self):
        seq = _arc_conflict_input()
        result = _two_opt(list(seq), WEIGHTS, arc="descending")
        assert result[0].track_id == seq[0].track_id
        assert result[-1].track_id == seq[-1].track_id
        assert sorted(t.track_id for t in result) == [1, 2, 3, 4, 5, 6]
