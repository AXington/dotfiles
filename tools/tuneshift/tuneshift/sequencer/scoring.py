"""Pairwise transition scoring with weighted dimensions."""

from collections.abc import Callable

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.weights import PRESETS


def jaccard(set_a: list[str], set_b: list[str]) -> float:
    """Jaccard similarity between two tag lists."""
    if not set_a and not set_b:
        return 0.0
    a = set(set_a)
    b = set(set_b)
    intersection = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return intersection / union


def energy_score(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score energy and valence continuity."""
    if a.energy is None or b.energy is None:
        return 0.5
    energy_delta = abs(a.energy - b.energy)
    valence_delta = abs((a.valence or 0.5) - (b.valence or 0.5))
    return max(0.0, 1.0 - 0.6 * energy_delta - 0.4 * valence_delta)


def bpm_score(
    bpm_a: float | None,
    bpm_b: float | None,
    tolerance: float = 0.175,
) -> float:
    """Score BPM proximity."""
    if bpm_a is None or bpm_b is None:
        return 0.5
    if bpm_a == 0 or bpm_b == 0:
        return 0.5

    ratio = max(bpm_a, bpm_b) / min(bpm_a, bpm_b)
    if 1.9 < ratio < 2.1:
        return 0.9

    delta = abs(bpm_a - bpm_b)
    avg_bpm = (bpm_a + bpm_b) / 2
    return max(0.0, 1.0 - delta / (avg_bpm * tolerance))


def duration_score(
    duration_ms_a: int | None,
    duration_ms_b: int | None,
) -> float:
    """Score duration proximity for fallback sequencing."""
    if duration_ms_a is None or duration_ms_b is None:
        return 0.5
    if duration_ms_a <= 0 or duration_ms_b <= 0:
        return 0.5

    delta = abs(duration_ms_a - duration_ms_b)
    avg_duration = (duration_ms_a + duration_ms_b) / 2
    tolerance = max(avg_duration * 0.35, 30000.0)
    return max(0.0, 1.0 - delta / tolerance)


_SONIC_BRIDGE_PAIRS = {
    ("fade to silence", "silence to vocal"),
    ("sustained chord", "pad"),
    ("sustained chord", "synth pad"),
    ("fade to silence", "ambient"),
    ("hard cut", "drum fill"),
    ("hard cut", "explosion"),
    ("piano", "piano"),
    ("guitar strum", "guitar"),
}

_COMPLEMENTARY_TEXTURES = {
    ("warm", "lush"),
    ("raw", "gritty"),
    ("polished", "crystalline"),
    ("lo-fi", "warm"),
    ("cold", "crystalline"),
}

_SMOOTH_SPACE_TRANSITIONS = {
    ("intimate", "intimate"),
    ("intimate", "room"),
    ("room", "hall"),
    ("hall", "vast"),
    ("vast", "vast"),
    ("room", "room"),
}


def transition_score(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score how well track A flows into track B sonically."""
    score = 0.5

    if a.closes_with and b.opens_with:
        a_close = a.closes_with.lower()
        b_open = b.opens_with.lower()
        for close_kw, open_kw in _SONIC_BRIDGE_PAIRS:
            if close_kw in a_close and open_kw in b_open:
                score += 0.3
                break
        else:
            if ("silence" in a_close and "explosion" in b_open) or (
                "silence" in a_close and "drum" in b_open
            ):
                score += 0.15

    if a.sonic_texture and b.sonic_texture:
        if a.sonic_texture == b.sonic_texture:
            score += 0.1
        elif (a.sonic_texture, b.sonic_texture) in _COMPLEMENTARY_TEXTURES or (
            b.sonic_texture,
            a.sonic_texture,
        ) in _COMPLEMENTARY_TEXTURES:
            score += 0.05

    if a.space and b.space:
        if (a.space, b.space) in _SMOOTH_SPACE_TRANSITIONS or (
            b.space,
            a.space,
        ) in _SMOOTH_SPACE_TRANSITIONS:
            score += 0.1

    return min(1.0, score)


_STANCE_PROGRESSION = {
    ("vulnerable", "defiant"): 0.2,
    ("defiant", "triumphant"): 0.2,
    ("introspective", "celebratory"): 0.15,
    ("introspective", "defiant"): 0.15,
    ("bitter", "resigned"): 0.1,
    ("joyful", "vulnerable"): 0.1,
    ("vulnerable", "introspective"): 0.1,
    ("resigned", "joyful"): 0.15,
    ("triumphant", "bitter"): -0.1,
    ("celebratory", "resigned"): -0.1,
    ("joyful", "bitter"): -0.1,
}


def narrative_connection_score(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score thematic/lyrical connection between adjacent tracks."""
    score = 0.5

    if a.narrator_stance and b.narrator_stance:
        pair = (a.narrator_stance, b.narrator_stance)
        progression = _STANCE_PROGRESSION.get(pair, 0.0)
        score += progression

    if a.lyrical_subject and b.lyrical_subject:
        a_words = set(a.lyrical_subject.lower().split())
        b_words = set(b.lyrical_subject.lower().split())
        overlap = len(a_words & b_words)
        if overlap > 0:
            score += min(0.15, overlap * 0.05)

    return max(0.0, min(1.0, score))


def emotional_arc_score(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score emotional intensity continuity (avoid jarring jumps)."""
    if a.emotional_intensity is None or b.emotional_intensity is None:
        return 0.5
    delta = abs(a.emotional_intensity - b.emotional_intensity)
    if delta < 0.2:
        return 0.9
    if delta < 0.4:
        return 0.6
    return 0.3


def score_mood_continuity(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score mood/emotional continuity between adjacent tracks."""
    if a.emotional_intensity is None and b.emotional_intensity is None:
        if not a.vibes and not b.vibes:
            return 0.5
    intensity_sim = 1.0 - abs(
        (a.emotional_intensity or 0.5) - (b.emotional_intensity or 0.5)
    )
    vibes_sim = jaccard(a.vibes, b.vibes)
    era_sim = jaccard(a.era_mood, b.era_mood) if a.era_mood or b.era_mood else 0.5
    return 0.5 * intensity_sim + 0.3 * vibes_sim + 0.2 * era_sim


def score_sonic_texture(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score sonic texture/space/density transition quality."""
    if not a.sonic_texture and not b.sonic_texture:
        return 0.5
    texture_match = 1.0 if a.sonic_texture == b.sonic_texture else 0.3
    space_match = 1.0 if a.space == b.space else 0.4
    density_map = {"sparse": 0, "mid": 1, "dense": 2}
    da = density_map.get(a.density or "mid", 1)
    db_val = density_map.get(b.density or "mid", 1)
    density_sim = 1.0 - abs(da - db_val) / 2.0
    return 0.4 * texture_match + 0.3 * space_match + 0.3 * density_sim


def score_lyrical_thread(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score lyrical subject and narrator stance continuity."""
    if not a.lyrical_subject and not b.lyrical_subject:
        return 0.5
    subject_match = 1.0 if a.lyrical_subject == b.lyrical_subject else 0.3
    stance_match = 1.0 if a.narrator_stance == b.narrator_stance else 0.4
    return 0.6 * subject_match + 0.4 * stance_match


def score_groove_coherence(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score rhythmic/groove coherence."""
    groove_match = 1.0 if a.groove_feel == b.groove_feel else 0.4
    bpm_sim = bpm_score(a.bpm, b.bpm) if a.bpm and b.bpm else 0.5
    density_map = {"sparse": 0, "mid": 1, "dense": 2}
    da = density_map.get(a.density or "mid", 1)
    db_val = density_map.get(b.density or "mid", 1)
    density_sim = 1.0 - abs(da - db_val) / 2.0
    return 0.4 * groove_match + 0.35 * bpm_sim + 0.25 * density_sim


def score_era_mood_transition(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score era/aesthetic coherence."""
    return jaccard(a.era_mood, b.era_mood) if a.era_mood or b.era_mood else 0.5


def score_variety(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score variety/contrast (inverse of similarity)."""
    theme_sim = jaccard(a.themes, b.themes)
    vibe_sim = jaccard(a.vibes, b.vibes)
    instrument_sim = jaccard(a.instruments, b.instruments)
    similarity = 0.4 * theme_sim + 0.3 * vibe_sim + 0.3 * instrument_sim
    return 1.0 - similarity


def score_artist_separation_transition(a: TrackMetadata, b: TrackMetadata) -> float:
    """Score artist separation (1.0 if different artist, 0.0 if same)."""
    if a.artist.lower().strip() == b.artist.lower().strip():
        return 0.0
    return 1.0


def score_narrative_arc_transition(a: TrackMetadata, b: TrackMetadata) -> float:  # noqa: ARG001 - uniform pairwise-scorer signature (dispatch table)
    """Narrative arc is enforced by chapter hard-breaks, not pairwise scoring."""
    return 0.5


DIMENSION_SCORERS: dict[str, Callable[[TrackMetadata, TrackMetadata], float]] = {
    "narrative_arc": score_narrative_arc_transition,
    "energy_flow": energy_score,
    "mood_continuity": score_mood_continuity,
    "sonic_texture": score_sonic_texture,
    "lyrical_thread": score_lyrical_thread,
    "emotional_arc": emotional_arc_score,
    "groove_coherence": score_groove_coherence,
    "era_mood": score_era_mood_transition,
    "variety": score_variety,
    "artist_separation": score_artist_separation_transition,
}

# Default equal-weight blend when nothing specified
DEFAULT_WEIGHTS: dict[str, float] = {dim: 0.5 for dim in DIMENSION_SCORERS}


def resolve_weights(
    cli_weights: dict[str, float] | None,
    db_weights: dict[str, float] | None,
    preset_name: str | None,
) -> dict[str, float]:
    """Resolve weight vector from cascade: CLI > DB > preset > default.

    Priority: CLI-provided values override DB, which overrides preset.
    Unspecified dimensions fall through to the next level.
    """
    # Start with base
    if preset_name and preset_name in PRESETS:
        base = dict(PRESETS[preset_name])
    else:
        base = dict(DEFAULT_WEIGHTS)

    # DB overrides base
    if db_weights:
        base.update(db_weights)

    # CLI overrides everything
    if cli_weights:
        base.update(cli_weights)

    return base


_LEGACY_DIMENSION_MAP = {
    "themes": "mood_continuity",
    "energy": "energy_flow",
    "instrumentation": "sonic_texture",
    "bpm": "groove_coherence",
    "narrative": "narrative_arc",
    "emotional_arc": "emotional_arc",
}


def _has_dimension_data(track: TrackMetadata, dimension: str) -> bool:
    """Check if a track has data for a scoring dimension.

    Accepts both legacy names (themes, energy, etc.) and new-style names
    (narrative_arc, energy_flow, etc.) by resolving through _LEGACY_DIMENSION_MAP
    in reverse.
    """
    # Resolve new-style names to the legacy checks they depend on
    _NEW_TO_CHECK = {
        "narrative_arc": "narrative",
        "energy_flow": "energy",
        "mood_continuity": "themes",
        "sonic_texture": "instrumentation",
        "lyrical_thread": "themes",
        "emotional_arc": "emotional_arc",
        "groove_coherence": "bpm",
        "era_mood": "themes",
        "variety": "instrumentation",
        "artist_separation": "artist_separation",
    }
    check_name = _NEW_TO_CHECK.get(dimension, dimension)

    if check_name == "themes":
        return len(track.themes) > 0 or len(track.vibes) > 0
    if check_name == "energy":
        return track.energy is not None
    if check_name == "instrumentation":
        return len(track.instruments) > 0 or track.acousticness is not None
    if check_name == "bpm":
        return track.bpm is not None
    if check_name == "mode":
        return track.mode is not None
    if check_name == "key":
        return track.camelot_code is not None
    if check_name == "transition":
        return track.opens_with is not None or track.closes_with is not None
    if check_name == "narrative":
        return track.narrator_stance is not None
    if check_name == "emotional_arc":
        return track.emotional_intensity is not None
    if check_name == "artist_separation":
        return True  # Always applicable (uses track.artist)
    return False


def score_pair(
    a: TrackMetadata,
    b: TrackMetadata,
    weights: dict[str, float],
) -> float:
    """Compute a weighted transition score between two tracks."""
    applicable: dict[str, float] = {}

    for dimension, weight in weights.items():
        if weight <= 0:
            continue
        resolved = _LEGACY_DIMENSION_MAP.get(dimension, dimension)
        if resolved not in DIMENSION_SCORERS:
            continue
        if _has_dimension_data(a, dimension) and _has_dimension_data(b, dimension):
            applicable[dimension] = weight

    if not applicable:
        return duration_score(a.duration_ms, b.duration_ms)

    total = sum(applicable.values())
    normalized = {dim: w / total for dim, w in applicable.items()}

    score = 0.0
    for dimension, weight in normalized.items():
        resolved = _LEGACY_DIMENSION_MAP.get(dimension, dimension)
        scorer = DIMENSION_SCORERS[resolved]
        score += weight * scorer(a, b)

    return score
