"""Tests for distribute_artists protected-violation handling (SEQ-C5)."""

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import distribute_artists


def _t(track_id: int, artist: str) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=artist,
        duration_ms=200000,
        energy=0.5,
        valence=0.5,
    )


def _adjacent_same_artist(seq):
    return [
        i for i in range(len(seq) - 1) if seq[i].artist == seq[i + 1].artist
    ]


class TestProtectedViolationSkip:
    """A protected same-artist clump at the front must not disable
    redistribution of later, movable clumps (SEQ-C5)."""

    def test_leading_protected_clump_does_not_block_later_redistribution(self):
        # Positions 0,1: pinned clump of artist A (protected, cannot move).
        # Positions 6,7: movable clump of artist B that should be separated.
        seq = [
            _t(1, "A"),
            _t(2, "A"),
            _t(3, "C"),
            _t(4, "D"),
            _t(5, "E"),
            _t(6, "F"),
            _t(7, "B"),
            _t(8, "B"),
            _t(9, "G"),
            _t(10, "H"),
        ]
        result = distribute_artists(seq, min_separation=4, protected={0, 1})
        # The pinned A clump stays put.
        assert result[0].artist == "A" and result[1].artist == "A"
        # The movable B clump must no longer be adjacent.
        b_positions = [i for i, t in enumerate(result) if t.artist == "B"]
        assert abs(b_positions[0] - b_positions[1]) > 1, (
            f"B clump not separated: {[t.artist for t in result]}"
        )

    def test_unprotected_run_still_distributes(self):
        seq = [
            _t(1, "A"),
            _t(2, "A"),
            _t(3, "C"),
            _t(4, "D"),
            _t(5, "E"),
            _t(6, "F"),
        ]
        result = distribute_artists(seq, min_separation=4)
        a_positions = [i for i, t in enumerate(result) if t.artist == "A"]
        assert abs(a_positions[0] - a_positions[1]) > 1
