"""Membership-invariant guard for the sequencer.

Sequencing reorders tracks. It must never add, drop, or duplicate one. A
corrupted sequence is not obviously wrong to a caller: the closer-pin bug
produced a plausible-looking playlist with one track duplicated and one
silently replaced, and it was persisted. These tests pin the invariant
itself rather than any particular pin combination, so a future regression
in any sequencing path fails loudly.
"""

import random
from unittest.mock import patch

import pytest

from tuneshift import SequenceIntegrityError
from tuneshift.db import Database
from tuneshift.models import PlaylistPin, Track
from tuneshift.sequencer import optimizer as optimizer_module
from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import (
    _verify_membership,
    optimize_sequence,
    sequence_playlist,
)

WEIGHTS = {"energy": 0.5, "themes": 0.5}
ARCS = ["wave", "rise", "fall", "flat", "peak"]
PIN_TYPES = ["opener", "closer", "position", "anchor", "moment"]


def _track(track_id: int, energy: float = 0.5) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=f"Artist{track_id}",
        duration_ms=200000,
        energy=energy,
        valence=0.5,
    )


class TestVerifyMembership:
    def test_permutation_passes(self):
        _verify_membership([1, 2, 3], [3, 1, 2], "ctx")

    def test_identical_passes(self):
        _verify_membership([1, 2, 3], [1, 2, 3], "ctx")

    def test_empty_passes(self):
        _verify_membership([], [], "ctx")

    def test_dropped_track_raises(self):
        with pytest.raises(SequenceIntegrityError) as exc:
            _verify_membership([1, 2, 3], [1, 2], "ctx")
        assert exc.value.missing == [3]
        assert exc.value.duplicated == []
        assert "dropped=[3]" in str(exc.value)

    def test_duplicated_track_raises(self):
        """The exact corruption shape of the closer-pin bug."""
        with pytest.raises(SequenceIntegrityError) as exc:
            _verify_membership([1, 2, 3], [1, 2, 1], "ctx")
        assert exc.value.missing == [3]
        assert exc.value.duplicated == [1]

    def test_added_track_raises(self):
        with pytest.raises(SequenceIntegrityError) as exc:
            _verify_membership([1, 2], [1, 2, 9], "ctx")
        assert exc.value.added == [9]

    def test_context_is_reported(self):
        with pytest.raises(SequenceIntegrityError, match="optimize_sequence"):
            _verify_membership([1], [], "optimize_sequence(arc='wave')")


class TestOptimizeSequenceGuard:
    @staticmethod
    def _tracks() -> list[TrackMetadata]:
        return [_track(1, 0.4), _track(2, 0.97), _track(3, 0.64)]

    def test_clean_run_does_not_raise(self):
        result = optimize_sequence(self._tracks(), WEIGHTS, arc="wave", seed=1)
        assert sorted(t.track_id for t in result) == [1, 2, 3]

    def test_guard_catches_duplicate_and_drop(self):
        tracks = self._tracks()
        corrupt = [tracks[0], tracks[1], tracks[0]]
        with patch.object(
            optimizer_module, "_optimize_sequence_unchecked", return_value=corrupt
        ):
            with pytest.raises(SequenceIntegrityError) as exc:
                optimize_sequence(tracks, WEIGHTS, arc="wave", seed=1)
        assert exc.value.duplicated == [1]
        assert exc.value.missing == [3]

    def test_guard_catches_silent_drop(self):
        tracks = self._tracks()
        with patch.object(
            optimizer_module,
            "_optimize_sequence_unchecked",
            return_value=tracks[:2],
        ):
            with pytest.raises(SequenceIntegrityError):
                optimize_sequence(tracks, WEIGHTS, arc="wave", seed=1)


class TestSequencePlaylistGuard:
    @pytest.fixture
    def db_playlist(self, tmp_path):
        db = Database(tmp_path / "integrity.db")
        playlist_id = db.create_playlist("Integrity")
        track_ids = []
        for i, energy in enumerate([0.8, 0.5, 0.2, 0.3, 0.9]):
            tid = db.add_track(
                Track(title=f"T{i}", artist=f"A{i}", energy=energy, valence=0.5)
            )
            db.add_track_to_playlist(playlist_id, tid, position=i)
            track_ids.append(tid)
        return db, playlist_id, track_ids

    def test_clean_run_returns_permutation(self, db_playlist):
        db, playlist_id, track_ids = db_playlist
        result = sequence_playlist(db, playlist_id, arc="wave")
        assert sorted(result) == sorted(track_ids)

    def test_guard_catches_corrupt_inner_result(self, db_playlist):
        db, playlist_id, track_ids = db_playlist
        corrupt = [track_ids[0], *track_ids[1:-1], track_ids[0]]
        with patch.object(
            optimizer_module, "_sequence_playlist_unchecked", return_value=corrupt
        ):
            with pytest.raises(SequenceIntegrityError) as exc:
                sequence_playlist(db, playlist_id, arc="wave")
        assert exc.value.duplicated == [track_ids[0]]
        assert exc.value.missing == [track_ids[-1]]


class TestMembershipProperty:
    """Fuzz the invariant instead of enumerating named pin combinations.

    Every prior audit of this sequencer reasoned only about the pin
    combinations someone had already thought to name, and all of them missed
    a lone --closer pin corrupting the playlist. Randomized membership
    checking finds that class of bug in seconds.
    """

    @staticmethod
    def _pin(track_id, pin_type, group_id=None, group_order=None):
        return PlaylistPin(
            playlist_id=1,
            track_id=track_id,
            pin_type=pin_type,
            group_id=group_id,
            group_order=group_order,
        )

    def _random_pins(self, rng, size, db_realistic):
        pins = []
        used = set()
        for _ in range(rng.randint(0, 3)):
            track_id = rng.randint(1, size)
            # The DB enforces UNIQUE(playlist_id, track_id) on pins, so a
            # track can carry at most one pin via the CLI path.
            if db_realistic and track_id in used:
                continue
            used.add(track_id)
            pin_type = rng.choice(PIN_TYPES)
            if pin_type == "position":
                pins.append(
                    self._pin(track_id, pin_type, group_order=rng.randint(0, size - 1))
                )
            elif pin_type == "anchor":
                pins.append(
                    self._pin(
                        track_id,
                        pin_type,
                        group_id=rng.choice(["g1", "g2"]),
                        group_order=rng.randint(0, 3),
                    )
                )
            else:
                pins.append(self._pin(track_id, pin_type))
        return pins

    @pytest.mark.parametrize("db_realistic", [True, False])
    def test_random_pins_preserve_membership(self, db_realistic):
        rng = random.Random(4242 if db_realistic else 2424)
        for _ in range(400):
            size = rng.randint(1, 12)
            tracks = [_track(i, energy=rng.random()) for i in range(1, size + 1)]
            expected = sorted(t.track_id for t in tracks)
            pins = self._random_pins(rng, size, db_realistic)
            arc = rng.choice(ARCS)
            try:
                result = optimize_sequence(
                    tracks,
                    WEIGHTS,
                    arc=arc,
                    pins=pins,
                    seed=rng.randint(0, 999),
                )
            except ValueError:
                # Contradictory pins are rejected up front by design.
                continue
            ids = [t.track_id for t in result]
            assert sorted(ids) == expected, (arc, size, pins, ids)

    @pytest.mark.parametrize("pin_type", PIN_TYPES)
    @pytest.mark.parametrize("arc", ARCS)
    def test_single_pin_of_every_type_preserves_membership(self, pin_type, arc):
        rng = random.Random(hash((pin_type, arc)) & 0xFFFF)
        for size in (3, 5, 9):
            tracks = [_track(i, energy=rng.random()) for i in range(1, size + 1)]
            expected = sorted(t.track_id for t in tracks)
            target = rng.randint(1, size)
            group_order = 1 if pin_type in ("position", "anchor") else None
            group_id = "g" if pin_type == "anchor" else None
            pins = [self._pin(target, pin_type, group_id, group_order)]
            result = optimize_sequence(tracks, WEIGHTS, arc=arc, pins=pins, seed=3)
            ids = [t.track_id for t in result]
            assert sorted(ids) == expected, (pin_type, arc, size, ids)
            assert len(ids) == len(set(ids)), (pin_type, arc, ids)
