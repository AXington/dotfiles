"""SEQ-C1: non-narrative sequencing must be deterministic under a fixed seed.

The optimizer used the module-global ``random`` for bold jumps, so repeated
runs on identical input produced different orderings. A caller-supplied seed
(defaulting to a stable value derived from the playlist) must make the output
reproducible while still allowing genuinely different seeds to explore.
"""

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import optimize_sequence

WEIGHTS = {
    "themes": 0.35,
    "energy": 0.22,
    "instrumentation": 0.18,
    "bpm": 0.12,
    "mode": 0.08,
    "key": 0.05,
}


def _track(i: int) -> TrackMetadata:
    return TrackMetadata(
        track_id=i,
        title=f"T{i}",
        artist=f"A{i % 4}",
        duration_ms=200000,
        energy=(i * 0.07) % 1.0,
        valence=(i * 0.13) % 1.0,
    )


def _tracks(n: int = 15) -> list[TrackMetadata]:
    return [_track(i) for i in range(1, n + 1)]


def _order(**kwargs) -> tuple[int, ...]:
    seq = optimize_sequence(_tracks(), WEIGHTS, arc="wave", **kwargs)
    return tuple(t.track_id for t in seq)


def test_fixed_seed_is_deterministic() -> None:
    """Eight runs with the same seed produce byte-identical orderings."""
    orders = {_order(seed=0) for _ in range(8)}
    assert len(orders) == 1


def test_different_seeds_can_differ() -> None:
    """Distinct seeds can produce distinct orderings (seed is actually used)."""
    orders = {_order(bold_jump_chance=1.0, seed=s) for s in range(12)}
    assert len(orders) > 1
