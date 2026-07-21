"""Performance guard for large-playlist sequencing (Task 1.7 / SEQ-A4).

The swap-based local search used to recompute the full O(n) objective for every
candidate swap, giving O(n^3)-ish behavior: a 600-track playlist took minutes.
The windowed local-delta path bounds this. These tests pin the budget and the
invariants (permutation preserved, deterministic) so a regression back to the
quadratic/cubic objective is caught.
"""

import random
import time

import pytest

from tuneshift.sequencer import optimizer
from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import optimize_sequence

WEIGHTS = {"energy": 0.5, "themes": 0.3, "key": 0.2}


def _make_tracks(count: int, *, seed: int = 1) -> list[TrackMetadata]:
    rng = random.Random(seed)
    tracks: list[TrackMetadata] = []
    for i in range(count):
        tracks.append(
            TrackMetadata(
                track_id=i,
                title=f"Track {i}",
                artist=f"Artist{i % 40}",
                duration_ms=180000 + i,
                energy=rng.random(),
                valence=rng.random(),
                mode=rng.randint(0, 1),
                camelot_code=f"{rng.randint(1, 12)}{rng.choice('AB')}",
            )
        )
    return tracks


class TestLargePlaylistBudget:
    def test_600_tracks_completes_under_budget(self):
        tracks = _make_tracks(600)
        start = time.perf_counter()
        result = optimize_sequence(tracks, WEIGHTS, arc="wave", seed=0)
        elapsed = time.perf_counter() - start
        # Local runtime ~2.3s; a 10s ceiling leaves headroom for slow CI while
        # still failing loudly on a regression to the O(n^3) full-objective path
        # (which took minutes at this size).
        assert elapsed < 10.0, f"600-track sequencing took {elapsed:.1f}s"
        assert len(result) == 600
        assert {t.track_id for t in result} == {t.track_id for t in tracks}

    def test_large_output_is_deterministic(self):
        tracks = _make_tracks(600)
        first = optimize_sequence(tracks, WEIGHTS, arc="wave", seed=0)
        second = optimize_sequence(
            list(tracks), WEIGHTS, arc="wave", seed=0
        )
        assert [t.track_id for t in first] == [t.track_id for t in second]


class TestWindowedPathParity:
    """Force the windowed path on a small input to exercise the region rescore."""

    def test_windowed_path_preserves_membership(self, monkeypatch):
        monkeypatch.setattr(optimizer, "_SWAP_WINDOW_THRESHOLD", 0)
        tracks = _make_tracks(30, seed=7)
        result = optimize_sequence(tracks, WEIGHTS, arc="wave", seed=0)
        assert len(result) == 30
        assert {t.track_id for t in result} == {t.track_id for t in tracks}
        # Endpoints and interior are all present exactly once.
        assert len({t.track_id for t in result}) == 30

    def test_windowed_path_is_deterministic(self, monkeypatch):
        monkeypatch.setattr(optimizer, "_SWAP_WINDOW_THRESHOLD", 0)
        tracks = _make_tracks(30, seed=7)
        a = optimize_sequence(tracks, WEIGHTS, arc="wave", seed=0)
        b = optimize_sequence(list(tracks), WEIGHTS, arc="wave", seed=0)
        assert [t.track_id for t in a] == [t.track_id for t in b]


class TestSmallInputUsesExactPath:
    def test_threshold_keeps_small_inputs_on_exact_path(self):
        # Guard the parity contract: inputs at/below the threshold must not take
        # the heuristic windowed branch, so their output stays byte-identical to
        # the exact local search covered by the rest of the suite.
        assert optimizer._SWAP_WINDOW_THRESHOLD >= 500


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
