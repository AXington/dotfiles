"""Tests for Camelot key + mode harmonic scorers (SEQ-S1).

Before the fix, profile weights for ``key`` (0.05) and ``mode`` (0.08) were
silently dropped: no scorer was registered, so changing a track's Camelot code
or mode never changed the transition score.
"""

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.scoring import score_pair


def _t(track_id: int, camelot: str | None = None, mode: int | None = None) -> TrackMetadata:
    return TrackMetadata(
        track_id=track_id,
        title=f"Track {track_id}",
        artist=f"Artist{track_id}",
        duration_ms=200000,
        camelot_code=camelot,
        mode=mode,
    )


class TestCamelotKeyScorer:
    def test_adjacent_key_scores_higher_than_distant(self):
        a = _t(1, camelot="8B")
        adjacent = _t(2, camelot="9B")  # +1 hour, same letter -> harmonic
        distant = _t(3, camelot="3A")  # far around the wheel
        w = {"key": 1.0}
        assert score_pair(a, adjacent, w) > score_pair(a, distant, w)

    def test_identical_key_scores_highest(self):
        a = _t(1, camelot="8B")
        same = _t(2, camelot="8B")
        adjacent = _t(3, camelot="9B")
        w = {"key": 1.0}
        assert score_pair(a, same, w) >= score_pair(a, adjacent, w)

    def test_relative_major_minor_is_compatible(self):
        a = _t(1, camelot="8B")
        relative = _t(2, camelot="8A")  # relative minor of 8B
        distant = _t(3, camelot="2A")
        w = {"key": 1.0}
        assert score_pair(a, relative, w) > score_pair(a, distant, w)


class TestModeScorer:
    def test_same_mode_scores_higher_than_mode_change(self):
        a = _t(1, mode=1)
        same = _t(2, mode=1)
        changed = _t(3, mode=0)
        w = {"mode": 1.0}
        assert score_pair(a, same, w) > score_pair(a, changed, w)


class TestKeyModeWeightApplied:
    """The 0.05/0.08 profile weights must measurably affect the blend."""

    def test_changing_key_changes_blended_score(self):
        base = _t(1, camelot="8B", mode=1)
        near = _t(2, camelot="9B", mode=1)
        far = _t(3, camelot="3A", mode=1)
        # With key weight in the blend, near vs far must differ.
        w = {"energy": 0.5, "key": 0.5}
        # energy identical (both None -> gated out), so key dominates
        assert score_pair(base, near, w) != score_pair(base, far, w)
