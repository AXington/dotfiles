"""Tests for sequencer pin integrity (SEQ-C2, C3, C4, C6).

Covers: rejecting contradictory pins on a single track (C3), keeping anchor
blocks atomic against colliding position pins (C4), explicit position pins
winning over soft moment targets (C6), and honoring pins in the narrative
arc path (C2).
"""

import pytest

from tuneshift.models import PlaylistPin
from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import optimize_sequence

WEIGHTS = {"energy": 0.5, "themes": 0.5}


def _track(
    track_id: int, energy: float = 0.5, artist: str | None = None
) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=artist or f"Artist{track_id}",
        duration_ms=200000,
        energy=energy,
        valence=0.5,
    )


def _pin(
    track_id: int,
    pin_type: str,
    group_id: str | None = None,
    group_order: int | None = None,
) -> PlaylistPin:
    return PlaylistPin(
        playlist_id=1,
        track_id=track_id,
        pin_type=pin_type,
        group_id=group_id,
        group_order=group_order,
    )


class TestPinConflictRejection:
    """C3: a single track with two contradictory placement pins is rejected
    loudly rather than silently producing a duplicated track."""

    def test_anchor_plus_position_on_same_track_raises(self):
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "anchor", group_id="g", group_order=0),
            _pin(4, "anchor", group_id="g", group_order=1),
            _pin(3, "position", group_order=5),
        ]
        with pytest.raises(ValueError, match=r"[Cc]onflict.*track 3"):
            optimize_sequence(tracks, WEIGHTS, pins=pins)

    def test_opener_plus_closer_on_same_track_raises(self):
        tracks = [_track(i) for i in range(1, 9)]
        pins = [_pin(2, "opener"), _pin(2, "closer")]
        with pytest.raises(ValueError, match=r"[Cc]onflict.*track 2"):
            optimize_sequence(tracks, WEIGHTS, pins=pins)

    def test_position_plus_opener_on_same_track_raises(self):
        tracks = [_track(i) for i in range(1, 9)]
        pins = [_pin(6, "opener"), _pin(6, "position", group_order=4)]
        with pytest.raises(ValueError, match=r"[Cc]onflict.*track 6"):
            optimize_sequence(tracks, WEIGHTS, pins=pins)

    def test_two_anchor_groups_on_same_track_raises(self):
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "anchor", group_id="g1", group_order=0),
            _pin(4, "anchor", group_id="g1", group_order=1),
            _pin(3, "anchor", group_id="g2", group_order=0),
            _pin(5, "anchor", group_id="g2", group_order=1),
        ]
        with pytest.raises(ValueError, match=r"[Cc]onflict.*track 3"):
            optimize_sequence(tracks, WEIGHTS, pins=pins)

    def test_two_position_pins_on_same_track_raises(self):
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "position", group_order=2),
            _pin(3, "position", group_order=5),
        ]
        with pytest.raises(ValueError, match=r"[Cc]onflict.*track 3"):
            optimize_sequence(tracks, WEIGHTS, pins=pins)

    def test_cross_track_position_vs_opener_is_not_a_conflict(self):
        """Different tracks each carrying one pin is precedence-resolved, not
        a conflict (position wins over opener)."""
        tracks = [_track(i) for i in range(1, 9)]
        pins = [_pin(1, "opener"), _pin(2, "position", group_order=0)]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins)
        assert result[0].track_id == 2
        assert sorted(t.track_id for t in result) == list(range(1, 9))

    def test_moment_plus_position_on_same_track_is_allowed(self):
        """Moment is a soft target; combining it with an explicit position pin
        is resolved by precedence (C6), not rejected."""
        tracks = [_track(i) for i in range(1, 9)]
        pins = [_pin(3, "moment"), _pin(3, "position", group_order=4)]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins)
        assert sorted(t.track_id for t in result) == list(range(1, 9))

    def test_moment_on_anchor_member_does_not_duplicate_track(self):
        """C6 regression: a moment pin on a track that also belongs to an anchor
        group must be dropped, not honored as a second placement. Honoring it
        placed the track both inside its anchor block and at the moment index,
        producing N+1 outputs (a duplicated track)."""
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "anchor", group_id="g", group_order=0),
            _pin(4, "anchor", group_id="g", group_order=1),
            _pin(3, "moment"),
        ]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
        ids = [t.track_id for t in result]
        assert len(ids) == 8, f"expected 8 tracks, got {len(ids)}: {ids}"
        assert sorted(ids) == list(range(1, 9)), f"duplicate/drop: {ids}"

    def test_moment_on_second_anchor_member_does_not_duplicate(self):
        """C6 regression: the moment landing on the non-lead anchor member is
        also dropped rather than duplicating that member."""
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "anchor", group_id="g", group_order=0),
            _pin(4, "anchor", group_id="g", group_order=1),
            _pin(4, "moment"),
        ]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
        ids = [t.track_id for t in result]
        assert sorted(ids) == list(range(1, 9)), f"duplicate/drop: {ids}"

    def test_moment_on_non_pinned_track_is_still_honored(self):
        """The drop only applies to hard-placed tracks: a moment on a free
        track must still be honored so anchor+moment does not over-suppress."""
        tracks = [_track(i) for i in range(1, 9)]
        pins = [
            _pin(3, "anchor", group_id="g", group_order=0),
            _pin(4, "anchor", group_id="g", group_order=1),
            _pin(7, "moment"),
        ]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
        ids = [t.track_id for t in result]
        assert sorted(ids) == list(range(1, 9)), f"duplicate/drop: {ids}"

    def test_moment_on_opener_or_closer_does_not_duplicate(self):
        """C6 regression: opener/closer are hard placements too, so a coincident
        moment must be dropped rather than re-placing the endpoint."""
        tracks = [_track(i) for i in range(1, 9)]
        for endpoint in ("opener", "closer"):
            pins = [_pin(2, endpoint), _pin(2, "moment")]
            result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
            ids = [t.track_id for t in result]
            assert sorted(ids) == list(range(1, 9)), f"{endpoint}: {ids}"


class TestAnchorBlockAtomicity:
    """C4: an anchor block stays contiguous even when a position pin targets an
    index that would otherwise split it."""

    def _tracks(self, n=8):
        return [_track(i, energy=0.1 * i) for i in range(1, n + 1)]

    def _adjacent(self, result, a, b):
        ids = [t.track_id for t in result]
        return abs(ids.index(a) - ids.index(b)) == 1

    def test_position_pin_does_not_split_anchor_block(self):
        for target_idx in range(1, 7):
            tracks = self._tracks()
            pins = [
                _pin(3, "anchor", group_id="g", group_order=0),
                _pin(4, "anchor", group_id="g", group_order=1),
                _pin(7, "position", group_order=target_idx),
            ]
            result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
            ids = [t.track_id for t in result]
            assert sorted(ids) == list(range(1, 9)), f"idx={target_idx}: {ids}"
            assert self._adjacent(result, 3, 4), (
                f"anchor block 3-4 split at target_idx={target_idx}: {ids}"
            )

    def test_three_track_anchor_block_stays_contiguous(self):
        tracks = self._tracks(9)
        pins = [
            _pin(3, "anchor", group_id="g", group_order=0),
            _pin(4, "anchor", group_id="g", group_order=1),
            _pin(5, "anchor", group_id="g", group_order=2),
            _pin(8, "position", group_order=4),
        ]
        result = optimize_sequence(tracks, WEIGHTS, pins=pins, seed=1)
        ids = [t.track_id for t in result]
        assert sorted(ids) == list(range(1, 10)), ids
        i3, i4, i5 = ids.index(3), ids.index(4), ids.index(5)
        assert max(i3, i4, i5) - min(i3, i4, i5) == 2, ids


NARRATIVE_5 = """
OPENING (1-2): Gentle intro, setting the scene.
WRATH (3-4): Fury and defiance.
CLOSER (5): Triumphant anthem.
"""


class TestNarrativePinIntegration:
    """C2: explicit placement pins are honored (folded into the arc) under a
    narrative arc with a description, instead of being silently dropped."""

    def _tracks(self, n=5):
        return [_track(i, energy=0.1 * i) for i in range(1, n + 1)]

    def test_opener_pin_honored_under_narrative(self):
        tracks = self._tracks()
        pins = [_pin(4, "opener")]
        result = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=pins, seed=1
        )
        ids = [t.track_id for t in result]
        assert ids[0] == 4, ids
        assert sorted(ids) == [1, 2, 3, 4, 5], ids

    def test_closer_pin_honored_under_narrative(self):
        tracks = self._tracks()
        pins = [_pin(1, "closer")]
        result = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=pins, seed=1
        )
        ids = [t.track_id for t in result]
        assert ids[-1] == 1, ids
        assert sorted(ids) == [1, 2, 3, 4, 5], ids

    def test_position_pin_honored_under_narrative(self):
        tracks = self._tracks()
        pins = [_pin(2, "position", group_order=3)]
        result = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=pins, seed=1
        )
        ids = [t.track_id for t in result]
        assert ids[3] == 2, ids
        assert sorted(ids) == [1, 2, 3, 4, 5], ids

    def test_anchor_block_stays_contiguous_under_narrative(self):
        tracks = self._tracks()
        pins = [
            _pin(1, "anchor", group_id="g", group_order=0),
            _pin(5, "anchor", group_id="g", group_order=1),
        ]
        result = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=pins, seed=1
        )
        ids = [t.track_id for t in result]
        assert sorted(ids) == [1, 2, 3, 4, 5], ids
        assert abs(ids.index(1) - ids.index(5)) == 1, ids

    def test_opener_and_position_pins_together_under_narrative(self):
        tracks = self._tracks()
        pins = [_pin(5, "opener"), _pin(2, "position", group_order=2)]
        result = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=pins, seed=1
        )
        ids = [t.track_id for t in result]
        assert ids[0] == 5, ids
        assert ids[2] == 2, ids
        assert sorted(ids) == [1, 2, 3, 4, 5], ids

    def test_no_pins_narrative_unchanged(self):
        tracks = self._tracks()
        baseline = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, seed=1
        )
        with_empty = optimize_sequence(
            tracks, WEIGHTS, arc="narrative", narrative=NARRATIVE_5, pins=[], seed=1
        )
        assert [t.track_id for t in baseline] == [t.track_id for t in with_empty]


class TestEndpointPinMembership:
    """A pinned closer must never also be auto-selected as the opener.

    Regression for the corruption found by fuzzing the membership invariant:
    ``_select_endpoints`` excluded only position/anchor tracks from opener
    selection, so a lone ``--closer`` pin could pick the same track as opener.
    ``_greedy_build`` then emitted it twice and dropped another track. The
    duplicate was masked downstream by a ``set()`` call, so the user saw a
    plausible-looking playlist with one track duplicated and one missing.
    """

    @staticmethod
    def _tracks(count: int) -> list[TrackMetadata]:
        energies = [0.4, 0.97, 0.64, 0.2, 0.81, 0.55, 0.33, 0.9, 0.11, 0.72]
        return [
            _track(i, energy=energies[(i - 1) % len(energies)])
            for i in range(1, count + 1)
        ]

    def test_lone_closer_pin_minimal_case(self):
        """Three tracks, closer pinned to track 1: pre-fix this returned [1, 2, 1]."""
        result = optimize_sequence(
            self._tracks(3), WEIGHTS, arc="wave", pins=[_pin(1, "closer")], seed=1
        )
        ids = [t.track_id for t in result]
        assert sorted(ids) == [1, 2, 3], ids
        assert ids[-1] == 1, ids

    @pytest.mark.parametrize("arc", ["wave", "rise", "fall", "flat", "peak"])
    @pytest.mark.parametrize("size", [3, 4, 5, 8])
    @pytest.mark.parametrize("target", [1, 2])
    def test_lone_closer_pin_preserves_membership(self, arc, size, target):
        tracks = self._tracks(size)
        expected = sorted(t.track_id for t in tracks)
        result = optimize_sequence(
            tracks, WEIGHTS, arc=arc, pins=[_pin(target, "closer")], seed=7
        )
        ids = [t.track_id for t in result]
        assert sorted(ids) == expected, (arc, size, target, ids)
        assert len(ids) == len(set(ids)), f"duplicate track in {ids}"
        assert ids[-1] == target, ids

    @pytest.mark.parametrize("seed", range(12))
    def test_lone_closer_pin_stable_across_seeds(self, seed):
        tracks = self._tracks(6)
        result = optimize_sequence(
            tracks, WEIGHTS, arc="wave", pins=[_pin(3, "closer")], seed=seed
        )
        ids = [t.track_id for t in result]
        assert sorted(ids) == [1, 2, 3, 4, 5, 6], (seed, ids)
        assert ids[-1] == 3, (seed, ids)

    def test_lone_opener_pin_preserves_membership(self):
        """Control: the opener path was already correct and must stay correct."""
        result = optimize_sequence(
            self._tracks(6), WEIGHTS, arc="wave", pins=[_pin(4, "opener")], seed=3
        )
        ids = [t.track_id for t in result]
        assert sorted(ids) == [1, 2, 3, 4, 5, 6], ids
        assert ids[0] == 4, ids

    def test_closer_pin_with_position_pin_exhausting_pool(self):
        """Exclusions must never be relaxed far enough to re-admit the closer."""
        tracks = self._tracks(3)
        pins = [_pin(1, "closer"), _pin(2, "position", group_order=1)]
        result = optimize_sequence(tracks, WEIGHTS, arc="wave", pins=pins, seed=1)
        ids = [t.track_id for t in result]
        assert sorted(ids) == [1, 2, 3], ids
        assert ids[-1] == 1, ids
        assert ids[1] == 2, ids

    def test_moment_targets_are_deduplicated(self):
        """Repeated moment ids must map to one position, not be inserted twice."""
        from tuneshift.sequencer.optimizer import _place_moments

        placements = _place_moments([], [3, 3, 3], total=10)
        assert sorted(placements.values()) == [3], placements

    def test_distinct_moment_targets_still_each_placed(self):
        from tuneshift.sequencer.optimizer import _place_moments

        placements = _place_moments([], [3, 4], total=10)
        assert sorted(placements.values()) == [3, 4], placements
