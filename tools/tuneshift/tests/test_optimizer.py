"""Tests for tuneshift.sequencer.optimizer helpers."""

import random

from tuneshift.models import PlaylistPin
from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import (
    _greedy_build,
    _prepare_free_pool,
    _resolve_pins,
    _select_endpoints,
    optimize_sequence,
)


def _make_track(track_id: int, energy: float = 0.5, artist: str = "A") -> TrackMetadata:
    """Helper to create a TrackMetadata for testing."""
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=artist,
        duration_ms=200000,
        energy=energy,
        valence=0.5,
    )


def _make_pin(track_id: int, pin_type: str, group_id: str | None = None, group_order: int | None = None) -> PlaylistPin:
    return PlaylistPin(
        playlist_id=1,
        track_id=track_id,
        pin_type=pin_type,
        group_id=group_id,
        group_order=group_order,
    )


class TestResolvePins:
    def test_none_pins_returns_empty(self):
        track_map = {1: _make_track(1)}
        opener, closer, groups, pos_pins = _resolve_pins(None, track_map)
        assert opener is None
        assert closer is None
        assert groups == {}

    def test_empty_list_returns_empty(self):
        track_map = {1: _make_track(1)}
        opener, closer, groups, pos_pins = _resolve_pins([], track_map)
        assert opener is None
        assert closer is None
        assert groups == {}

    def test_opener_pin(self):
        track_map = {1: _make_track(1), 2: _make_track(2)}
        pins = [_make_pin(1, "opener")]
        opener, closer, groups, pos_pins = _resolve_pins(pins, track_map)
        assert opener == 1
        assert closer is None
        assert groups == {}

    def test_closer_pin(self):
        track_map = {1: _make_track(1), 2: _make_track(2)}
        pins = [_make_pin(2, "closer")]
        opener, closer, groups, pos_pins = _resolve_pins(pins, track_map)
        assert opener is None
        assert closer == 2
        assert groups == {}

    def test_anchor_group_sorted_by_order(self):
        track_map = {i: _make_track(i) for i in range(1, 5)}
        pins = [
            _make_pin(3, "anchor", group_id="drop", group_order=1),
            _make_pin(2, "anchor", group_id="drop", group_order=0),
            _make_pin(4, "anchor", group_id="drop", group_order=2),
        ]
        _, _, groups, _ = _resolve_pins(pins, track_map)
        assert groups == {"drop": [2, 3, 4]}

    def test_pin_for_missing_track_is_ignored(self):
        track_map = {1: _make_track(1)}
        pins = [_make_pin(99, "opener")]
        opener, closer, groups, pos_pins = _resolve_pins(pins, track_map)
        assert opener is None

    def test_multiple_groups(self):
        track_map = {i: _make_track(i) for i in range(1, 7)}
        pins = [
            _make_pin(1, "anchor", group_id="intro", group_order=0),
            _make_pin(2, "anchor", group_id="intro", group_order=1),
            _make_pin(5, "anchor", group_id="climax", group_order=0),
            _make_pin(6, "anchor", group_id="climax", group_order=1),
        ]
        _, _, groups, _ = _resolve_pins(pins, track_map)
        assert groups == {"intro": [1, 2], "climax": [5, 6]}


class TestSelectEndpoints:
    def test_pinned_opener_used(self):
        tracks = [_make_track(i, energy=0.1 * i) for i in range(1, 6)]
        track_map = {t.track_id: t for t in tracks}
        opener, closer, remaining = _select_endpoints(tracks, track_map, 3, None, "wave")
        assert opener.track_id == 3
        assert closer.track_id != 3
        assert all(t.track_id != 3 for t in remaining)
        assert all(t.track_id != closer.track_id for t in remaining)

    def test_pinned_closer_used(self):
        tracks = [_make_track(i, energy=0.1 * i) for i in range(1, 6)]
        track_map = {t.track_id: t for t in tracks}
        opener, closer, remaining = _select_endpoints(tracks, track_map, None, 4, "wave")
        assert closer.track_id == 4
        assert all(t.track_id != 4 for t in remaining)

    def test_both_pinned(self):
        tracks = [_make_track(i) for i in range(1, 6)]
        track_map = {t.track_id: t for t in tracks}
        opener, closer, remaining = _select_endpoints(tracks, track_map, 1, 5, "wave")
        assert opener.track_id == 1
        assert closer.track_id == 5
        assert len(remaining) == 3

    def test_no_pins_selects_automatically(self):
        tracks = [_make_track(i, energy=0.1 * i) for i in range(1, 6)]
        track_map = {t.track_id: t for t in tracks}
        opener, closer, remaining = _select_endpoints(tracks, track_map, None, None, "wave")
        assert opener.track_id in {t.track_id for t in tracks}
        assert closer.track_id in {t.track_id for t in tracks}
        assert opener.track_id != closer.track_id
        assert len(remaining) == 3


class TestPrepareFreePool:
    def test_no_anchors_all_free(self):
        tracks = [_make_track(i) for i in range(1, 6)]
        opener = tracks[0]
        closer = tracks[-1]
        remaining = tracks[1:-1]
        track_map = {t.track_id: t for t in tracks}

        free, blocks = _prepare_free_pool(remaining, track_map, {}, opener, closer)
        assert len(free) == 3
        assert blocks == []

    def test_anchored_tracks_excluded_from_free(self):
        tracks = [_make_track(i) for i in range(1, 8)]
        opener = tracks[0]
        closer = tracks[-1]
        remaining = tracks[1:-1]
        track_map = {t.track_id: t for t in tracks}
        adjacency_groups = {"group1": [3, 4]}

        free, blocks = _prepare_free_pool(remaining, track_map, adjacency_groups, opener, closer)
        free_ids = {t.track_id for t in free}
        assert 3 not in free_ids
        assert 4 not in free_ids
        assert len(blocks) == 1
        assert [t.track_id for t in blocks[0]] == [3, 4]


class TestGreedyBuild:
    def test_produces_correct_length(self):
        tracks = [_make_track(i, energy=0.1 * i, artist=f"Artist{i}") for i in range(1, 11)]
        opener = tracks[0]
        closer = tracks[-1]
        free_tracks = tracks[1:-1]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}

        result = _greedy_build(
            opener, closer, free_tracks, [],
            track_count=10, weights=weights, arc="wave",
            bold_jump_chance=0.0, narrative_mode="river",
            context_window=5, penalty_overrides=None,
            rng=random.Random(42),
        )
        assert len(result) == 10
        assert result[0] == opener
        assert result[-1] == closer

    def test_anchor_block_inserted_together(self):
        tracks = [_make_track(i, energy=0.5, artist=f"Artist{i}") for i in range(1, 8)]
        opener = tracks[0]
        closer = tracks[-1]
        block = [tracks[2], tracks[3]]
        free_tracks = [t for t in tracks[1:-1] if t not in block]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}

        result = _greedy_build(
            opener, closer, free_tracks, [block],
            track_count=7, weights=weights, arc="wave",
            bold_jump_chance=0.0, narrative_mode="river",
            context_window=5, penalty_overrides=None,
            rng=random.Random(42),
        )
        # Block tracks should be adjacent in the result
        ids = [t.track_id for t in result]
        idx_3 = ids.index(3)
        idx_4 = ids.index(4)
        assert idx_4 == idx_3 + 1

    def test_opener_and_closer_positions_fixed(self):
        tracks = [_make_track(i, energy=0.1 * i) for i in range(1, 6)]
        opener = tracks[0]
        closer = tracks[-1]
        free_tracks = tracks[1:-1]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}

        result = _greedy_build(
            opener, closer, free_tracks, [],
            track_count=5, weights=weights, arc="wave",
            bold_jump_chance=0.0, narrative_mode="river",
            context_window=5, penalty_overrides=None,
            rng=random.Random(42),
        )
        assert result[0].track_id == 1
        assert result[-1].track_id == 5


class TestOptimizeSequenceIntegration:
    def test_short_list_returned_unchanged(self):
        tracks = [_make_track(1), _make_track(2)]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}
        result = optimize_sequence(tracks, weights)
        assert result == tracks

    def test_all_tracks_present_in_result(self):
        random.seed(42)
        tracks = [_make_track(i, energy=0.1 * i, artist=f"A{i}") for i in range(1, 15)]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}
        result = optimize_sequence(tracks, weights, arc="wave")
        assert set(t.track_id for t in result) == set(t.track_id for t in tracks)

    def test_pinned_opener_is_first(self):
        random.seed(42)
        tracks = [_make_track(i, energy=0.1 * i, artist=f"A{i}") for i in range(1, 10)]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}
        pins = [_make_pin(5, "opener")]
        result = optimize_sequence(tracks, weights, pins=pins)
        assert result[0].track_id == 5

    def test_pinned_closer_is_last(self):
        random.seed(42)
        tracks = [_make_track(i, energy=0.1 * i, artist=f"A{i}") for i in range(1, 10)]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}
        pins = [_make_pin(3, "closer")]
        result = optimize_sequence(tracks, weights, pins=pins)
        assert result[-1].track_id == 3

    def test_adjacency_group_stays_together(self):
        random.seed(42)
        tracks = [_make_track(i, energy=0.1 * i, artist=f"A{i}") for i in range(1, 12)]
        weights = {"themes": 0.35, "energy": 0.22, "instrumentation": 0.18, "bpm": 0.12, "mode": 0.08, "key": 0.05}
        pins = [
            _make_pin(1, "opener"),
            _make_pin(11, "closer"),
            _make_pin(4, "anchor", group_id="drop", group_order=0),
            _make_pin(5, "anchor", group_id="drop", group_order=1),
        ]
        result = optimize_sequence(tracks, weights, pins=pins, artist_min_separation=1)
        ids = [t.track_id for t in result]
        idx_4 = ids.index(4)
        idx_5 = ids.index(5)
        assert idx_5 == idx_4 + 1


class TestSmallPlaylistPins:
    """<=2-track playlists must still honor pins (regression: the old early
    return `if len(tracks) <= 2: return list(tracks)` bypassed pin logic)."""

    def _pair(self):
        return [_make_track(1), _make_track(2)]

    def test_no_pins_preserves_input_order(self):
        result = optimize_sequence(self._pair(), {})
        assert [t.track_id for t in result] == [1, 2]

    def test_opener_pin_moves_track_to_front(self):
        result = optimize_sequence(self._pair(), {}, pins=[_make_pin(2, "opener")])
        assert [t.track_id for t in result] == [2, 1]

    def test_closer_pin_moves_track_to_end(self):
        result = optimize_sequence(self._pair(), {}, pins=[_make_pin(1, "closer")])
        assert [t.track_id for t in result] == [2, 1]

    def test_position_pin_at_index_zero_overrides_order(self):
        result = optimize_sequence(
            self._pair(), {}, pins=[_make_pin(2, "position", group_order=0)]
        )
        assert [t.track_id for t in result] == [2, 1]

    def test_position_pin_at_last_index_overrides_order(self):
        result = optimize_sequence(
            self._pair(), {}, pins=[_make_pin(1, "position", group_order=1)]
        )
        assert [t.track_id for t in result] == [2, 1]

    def test_adjacency_group_order_sets_order(self):
        pins = [
            _make_pin(2, "anchor", group_id="g", group_order=0),
            _make_pin(1, "anchor", group_id="g", group_order=1),
        ]
        result = optimize_sequence(self._pair(), {}, pins=pins)
        assert [t.track_id for t in result] == [2, 1]

    def test_position_pin_overrides_conflicting_opener(self):
        # Position pin at index 0 must win over an opener pin on the other track.
        pins = [_make_pin(1, "opener"), _make_pin(2, "position", group_order=0)]
        result = optimize_sequence(self._pair(), {}, pins=pins)
        assert [t.track_id for t in result] == [2, 1]

    def test_moment_pin_is_noop_for_two_tracks(self):
        result = optimize_sequence(self._pair(), {}, pins=[_make_pin(1, "moment")])
        assert [t.track_id for t in result] == [1, 2]

    def test_single_track_returns_unchanged(self):
        result = optimize_sequence([_make_track(1)], {}, pins=[_make_pin(1, "closer")])
        assert [t.track_id for t in result] == [1]

    def test_empty_returns_empty(self):
        assert optimize_sequence([], {}) == []

    def test_every_track_appears_exactly_once(self):
        pins = [_make_pin(2, "opener"), _make_pin(1, "closer")]
        result = optimize_sequence(self._pair(), {}, pins=pins)
        assert sorted(t.track_id for t in result) == [1, 2]
        assert [t.track_id for t in result] == [2, 1]
