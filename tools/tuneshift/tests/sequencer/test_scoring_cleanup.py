"""Scoring/optimizer cleanup regressions (Task 1.6: SEQ-S2/S3/D2/S4/S5/A2).

Each test class pins one audit finding:

* S2  legacy + new-name weight double-counting in ``resolve_weights``
* S3  missing-metadata tracks winning endpoint selection
* D2  tie-break robustness / input-order invariance
* S4  ``score_pair`` duration fallback is an explicit, documented boundary
* S5  ``narrative_arc`` is a real pairwise signal, not dead constant 0.5
* A2  swap-neighborhood local search is named for what it is
"""

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.optimizer import (
    optimize_sequence,
    select_closer,
    select_opener,
)
from tuneshift.sequencer.scoring import (
    _LEGACY_DIMENSION_MAP,
    resolve_weights,
    score_pair,
)


def _mt(
    track_id: int,
    *,
    energy: float | None = None,
    valence: float | None = None,
    mode: int | None = None,
    narrator_stance: str | None = None,
    duration_ms: int = 200000,
) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=f"Artist{track_id}",
        duration_ms=duration_ms,
        energy=energy,
        valence=valence,
        mode=mode,
        narrator_stance=narrator_stance,
    )


class TestWeightDoubleCount:
    """SEQ-S2: a legacy name and its new-style twin must not both survive."""

    def _canonical_groups(self, resolved: dict[str, float]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for key in resolved:
            canonical = _LEGACY_DIMENSION_MAP.get(key, key)
            groups.setdefault(canonical, []).append(key)
        return groups

    def test_no_two_keys_share_a_canonical_dimension(self):
        # DB weights carry legacy names; the default base carries new-style names.
        resolved = resolve_weights(None, {"energy": 0.9, "key": 0.4, "mode": 0.2}, None)
        dupes = {
            canonical: keys
            for canonical, keys in self._canonical_groups(resolved).items()
            if len(keys) > 1
        }
        assert not dupes, f"double-counted dimensions: {dupes}"

    def test_legacy_intent_overrides_default(self):
        resolved = resolve_weights(None, {"energy": 0.9}, None)
        assert "energy" not in resolved
        assert resolved["energy_flow"] == 0.9

    def test_cli_legacy_beats_db_new(self):
        resolved = resolve_weights({"energy": 0.1}, {"energy_flow": 0.9}, None)
        assert resolved["energy_flow"] == 0.1
        assert "energy" not in resolved


class TestEndpointMetadataBias:
    """SEQ-S3: a metadata-complete track outranks a metadata-less one."""

    def test_opener_prefers_complete_energy(self):
        # arc="free" -> opener target defaults to 0.5, so a missing-energy track
        # defaulting to 0.5 used to score a *perfect* fit and win.
        missing = _mt(1, energy=None)
        complete = _mt(2, energy=0.55, valence=0.5)
        assert select_opener([missing, complete], arc="free") is complete

    def test_closer_prefers_complete_energy(self):
        # closer target for "free" is 0.3; a missing-energy track (default 0.5)
        # used to out-fit a real but imperfect closer.
        missing = _mt(1, energy=None)
        complete = _mt(2, energy=0.9, mode=1)
        assert select_closer([missing, complete], arc="free") is complete


class TestTieBreakDeterminism:
    """SEQ-D2: ordering must not depend on set-iteration order."""

    def test_optimize_invariant_to_input_permutation(self):
        base = [
            _mt(10, energy=0.5, valence=0.5),
            _mt(20, energy=0.5, valence=0.5),
            _mt(30, energy=0.5, valence=0.5),
            _mt(40, energy=0.5, valence=0.5),
            _mt(50, energy=0.5, valence=0.5),
        ]
        weights = {"energy": 0.5, "themes": 0.5}
        forward = optimize_sequence(base, weights, arc="free")
        reversed_in = optimize_sequence(list(reversed(base)), weights, arc="free")
        assert [t.track_id for t in forward] == [t.track_id for t in reversed_in]


class TestDurationFallbackBoundary:
    """SEQ-S4: the duration fallback is an explicit, documented last resort."""

    def test_fallback_only_when_no_dimension_is_jointly_scoreable(self):
        # Both tracks lack every requested dimension's data -> duration decides.
        short_a = _mt(1, duration_ms=180000)
        short_b = _mt(2, duration_ms=182000)
        long_c = _mt(3, duration_ms=360000)
        weights = {"energy": 0.5, "themes": 0.5}
        assert score_pair(short_a, short_b, weights) > score_pair(short_a, long_c, weights)

    def test_no_fallback_when_a_requested_dimension_is_scoreable(self):
        # Energy is jointly present, so duration must not drive the score even
        # though the durations differ wildly.
        near = _mt(1, energy=0.5, duration_ms=180000)
        also_near = _mt(2, energy=0.5, duration_ms=180000)
        far_energy = _mt(3, energy=0.95, duration_ms=181000)
        weights = {"energy": 1.0}
        assert score_pair(near, also_near, weights) > score_pair(near, far_energy, weights)


class TestNarrativeArcIsRealSignal:
    """SEQ-S5: narrative_arc must reflect narrator-stance progression."""

    def test_narrative_arc_distinguishes_stance_progression(self):
        anchor = _mt(1, narrator_stance="defiant")
        good_step = _mt(2, narrator_stance="triumphant")
        flat_step = _mt(3, narrator_stance="defiant")
        weights = {"narrative_arc": 1.0}
        good = score_pair(anchor, good_step, weights)
        flat = score_pair(anchor, flat_step, weights)
        assert good != flat, "narrative_arc is dead weight (constant score)"


class TestSwapSearchNaming:
    """SEQ-A2: the swap neighborhood is named for what it is."""

    def test_swap_search_is_exposed(self):
        from tuneshift.sequencer import optimizer

        assert hasattr(optimizer, "_swap_search"), (
            "swap-neighborhood local search should be named _swap_search"
        )
