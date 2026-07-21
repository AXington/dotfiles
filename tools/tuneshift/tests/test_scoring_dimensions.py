from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.scoring import (
    DIMENSION_SCORERS,
    score_lyrical_thread,
    score_mood_continuity,
    score_sonic_texture,
)


def _track(**kwargs) -> TrackMetadata:
    defaults = {"track_id": 1, "title": "T", "artist": "A"}
    defaults.update(kwargs)
    return TrackMetadata(**defaults)


class TestMoodContinuity:
    def test_same_mood_scores_high(self) -> None:
        a = _track(emotional_intensity=0.8, vibes=["angry", "defiant"])
        b = _track(emotional_intensity=0.7, vibes=["angry", "fierce"])
        score = score_mood_continuity(a, b)
        assert score > 0.6

    def test_opposite_mood_scores_low(self) -> None:
        a = _track(emotional_intensity=0.9, vibes=["angry", "explosive"])
        b = _track(emotional_intensity=0.1, vibes=["peaceful", "gentle"])
        score = score_mood_continuity(a, b)
        assert score < 0.4

    def test_missing_data_returns_neutral(self) -> None:
        a = _track()
        b = _track()
        score = score_mood_continuity(a, b)
        assert score == 0.5


class TestSonicTexture:
    def test_similar_texture_scores_high(self) -> None:
        a = _track(sonic_texture="thick", space="intimate", density="dense")
        b = _track(sonic_texture="thick", space="intimate", density="dense")
        score = score_sonic_texture(a, b)
        assert score > 0.8

    def test_different_texture_scores_low(self) -> None:
        a = _track(sonic_texture="thin", space="vast", density="sparse")
        b = _track(sonic_texture="thick", space="intimate", density="dense")
        score = score_sonic_texture(a, b)
        assert score < 0.4


class TestLyricalThread:
    def test_same_subject_scores_high(self) -> None:
        a = _track(lyrical_subject="identity", narrator_stance="defiant")
        b = _track(lyrical_subject="identity", narrator_stance="defiant")
        score = score_lyrical_thread(a, b)
        assert score > 0.7

    def test_different_subject_scores_moderate(self) -> None:
        a = _track(lyrical_subject="love", narrator_stance="vulnerable")
        b = _track(lyrical_subject="rage", narrator_stance="defiant")
        score = score_lyrical_thread(a, b)
        assert score < 0.5


class TestDimensionRegistry:
    def test_all_dimensions_registered(self) -> None:
        expected = {
            "narrative_arc", "energy_flow", "mood_continuity", "sonic_texture",
            "lyrical_thread", "emotional_arc", "groove_coherence", "era_mood",
            "variety", "artist_separation", "harmonic_key", "harmonic_mode",
        }
        assert set(DIMENSION_SCORERS.keys()) == expected

    def test_all_scorers_callable(self) -> None:
        for name, scorer in DIMENSION_SCORERS.items():
            assert callable(scorer), f"{name} scorer is not callable"
