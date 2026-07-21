"""Weight vector resolution and preset management for sequencer."""

PRESETS: dict[str, dict[str, float]] = {
    "narrative-queen": {
        "narrative_arc": 0.9,
        "emotional_arc": 0.8,
        "lyrical_thread": 0.8,
        "mood_continuity": 0.7,
        "energy_flow": 0.3,
        "sonic_texture": 0.5,
        "variety": 0.4,
        "artist_separation": 0.6,
        "groove_coherence": 0.4,
        "era_mood": 0.3,
        "harmonic_key": 0.2,
        "harmonic_mode": 0.3,
    },
    "energy-wave": {
        "energy_flow": 0.9,
        "mood_continuity": 0.6,
        "sonic_texture": 0.5,
        "variety": 0.5,
        "artist_separation": 0.5,
        "groove_coherence": 0.6,
        "narrative_arc": 0.0,
        "lyrical_thread": 0.1,
        "emotional_arc": 0.3,
        "era_mood": 0.2,
        "harmonic_key": 0.5,
        "harmonic_mode": 0.4,
    },
    "mood-bath": {
        "mood_continuity": 0.9,
        "sonic_texture": 0.8,
        "groove_coherence": 0.7,
        "energy_flow": 0.3,
        "variety": 0.3,
        "emotional_arc": 0.5,
        "narrative_arc": 0.0,
        "lyrical_thread": 0.2,
        "artist_separation": 0.4,
        "era_mood": 0.6,
        "harmonic_key": 0.5,
        "harmonic_mode": 0.5,
    },
    "discovery": {
        "variety": 0.9,
        "energy_flow": 0.6,
        "sonic_texture": 0.5,
        "mood_continuity": 0.4,
        "artist_separation": 0.8,
        "groove_coherence": 0.3,
        "narrative_arc": 0.0,
        "lyrical_thread": 0.1,
        "emotional_arc": 0.2,
        "era_mood": 0.3,
        "harmonic_key": 0.3,
        "harmonic_mode": 0.2,
    },
    "workout": {
        "energy_flow": 0.9,
        "groove_coherence": 0.8,
        "variety": 0.3,
        "mood_continuity": 0.4,
        "sonic_texture": 0.3,
        "artist_separation": 0.5,
        "narrative_arc": 0.0,
        "lyrical_thread": 0.0,
        "emotional_arc": 0.2,
        "era_mood": 0.1,
        "harmonic_key": 0.4,
        "harmonic_mode": 0.3,
    },
}

ALL_DIMENSIONS = [
    "narrative_arc",
    "energy_flow",
    "mood_continuity",
    "sonic_texture",
    "lyrical_thread",
    "emotional_arc",
    "groove_coherence",
    "era_mood",
    "variety",
    "artist_separation",
    "harmonic_key",
    "harmonic_mode",
]

DEFAULT_WEIGHTS: dict[str, float] = PRESETS["energy-wave"]


def resolve_weights(
    stored_weights: dict[str, float] | None,
    preset_name: str | None,
) -> dict[str, float]:
    """Resolve final weight vector from stored weights and/or preset.

    Priority: stored_weights override preset values.
    If neither provided, returns DEFAULT_WEIGHTS.
    """
    if preset_name and preset_name not in PRESETS:
        raise ValueError(
            f"Unknown preset: '{preset_name}'. Available: {list(PRESETS.keys())}"
        )

    base = dict(PRESETS.get(preset_name, DEFAULT_WEIGHTS)) if preset_name else {}

    if stored_weights:
        if not base:
            # Custom weights with no preset: start from zeros
            base = {dim: 0.0 for dim in ALL_DIMENSIONS}
        base.update(stored_weights)

    return base if base else dict(DEFAULT_WEIGHTS)
