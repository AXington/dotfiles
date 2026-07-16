"""Curation scoring engine: rate each track's contribution to the playlist."""

from collections.abc import Callable

from tuneshift.curation.context import PlaylistContext
from tuneshift.sequencer.metadata import TrackMetadata


def _word_similarity(word1: str, word2: str) -> float:
    """Check semantic similarity between two words (handles synonyms and stems)."""
    w1, w2 = word1.lower(), word2.lower()
    if w1 == w2:
        return 1.0
    # Synonym groups
    synonyms = {
        "fury": {"rage", "wrath", "anger", "fury"},
        "rage": {"fury", "wrath", "anger", "rage"},
        "wrath": {"fury", "rage", "anger", "wrath"},
        "anger": {"fury", "rage", "wrath", "anger"},
        "defiant": {"defiance", "defiant"},
        "defiance": {"defiant", "defiance"},
        "empowerment": {"power", "empower", "empowerment"},
        "power": {"empowerment", "empower", "power"},
        "empower": {"empowerment", "power", "empower"},
        "trans": {"transgender", "trans"},
        "transgender": {"trans", "transgender"},
    }
    if w1 in synonyms and w2 in synonyms[w1]:
        return 0.85
    # Simple stem matching (suffix removal)
    if len(w1) > 3 and len(w2) > 3 and (w1.startswith(w2[:3]) or w2.startswith(w1[:3])):
        return 0.6
    return 0.0


def score_narrative_fit(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score how well this track serves the narrative goal."""
    if not ctx.goal and not ctx.narrative_sections:
        return 0.5
    if not track.lyrical_subject and not track.themes:
        return 0.5
    # Keyword overlap between goal text and track classification
    goal_words = set(ctx.goal.lower().split()) if ctx.goal else set()
    section_words = set()
    for section in ctx.narrative_sections:
        section_words.update(section.get("description", "").lower().split())
        section_words.add(section.get("name", "").lower())

    target_words = goal_words | section_words
    track_words = set()
    if track.lyrical_subject:
        track_words.add(track.lyrical_subject.lower())
    if track.narrator_stance:
        track_words.add(track.narrator_stance.lower())
    if track.themes:
        track_words.update(t.lower() for t in track.themes)

    if not target_words or not track_words:
        return 0.5

    # Compute similarity between all word pairs
    total_similarity = 0.0
    max_similarities = 0
    for tword in track_words:
        max_sim = max(
            (_word_similarity(tword, gword) for gword in target_words), default=0.0
        )
        total_similarity += max_sim
        if max_sim > 0:
            max_similarities += 1

    base_score = total_similarity / max(len(track_words), 1) if track_words else 0.0

    # Boost for high emotional intensity when goal implies intensity
    intensity_boost = 0.0
    intensity_words = {"fury", "rage", "wrath", "defiance", "empowerment", "anger"}
    if target_words & intensity_words and track.emotional_intensity:
        intensity_boost = track.emotional_intensity * 0.2

    return min(1.0, base_score + intensity_boost)


def score_mood_contribution(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score how well this track contributes to the mood profile."""
    if not ctx.mood_profile:
        return 0.5
    if not track.vibes and track.emotional_intensity is None:
        return 0.5
    # Simple: check if track vibes overlap with mood_profile keywords
    mood_words = set()
    for _key, val in ctx.mood_profile.items():
        if isinstance(val, str):
            mood_words.add(val.lower())
    track_vibes = set(v.lower() for v in (track.vibes or []))
    if not mood_words or not track_vibes:
        return 0.5
    overlap = len(mood_words & track_vibes) / max(len(mood_words), 1)
    return min(1.0, 0.3 + overlap * 0.7)


def score_sonic_role(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score the sonic diversity contribution of this track."""
    if not track.sonic_texture and not track.instruments:
        return 0.5
    # Unique sonic texture relative to other tracks adds value
    other_textures = [
        getattr(t, "sonic_texture", None) for t in all_tracks if t != track
    ]
    if track.sonic_texture and track.sonic_texture not in other_textures:
        return 0.7
    return 0.5


def score_energy_role(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score whether this track fills a needed energy niche."""
    if track.energy is None:
        return 0.5
    return 0.5  # Neutral by default; enhanced by curation engine with full context


def score_uniqueness(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score how unique this track is relative to the rest of the playlist."""
    if not track.themes and not track.vibes:
        return 0.5
    track_signature = set(track.themes or []) | set(track.vibes or [])
    if not track_signature:
        return 0.5
    similarities = []
    for other in all_tracks:
        if other == track:
            continue
        other_sig = set(getattr(other, "themes", None) or []) | set(
            getattr(other, "vibes", None) or []
        )
        if other_sig:
            overlap = len(track_signature & other_sig) / max(
                len(track_signature | other_sig), 1
            )
            similarities.append(overlap)
    if not similarities:
        return 0.8  # Only track = very unique
    avg_similarity = sum(similarities) / len(similarities)
    return 1.0 - avg_similarity


def score_redundancy(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> float:
    """Score redundancy (inverse: high = NOT redundant, low = very redundant)."""
    # This is the inverse of uniqueness from a different angle
    # Check artist repetition
    artist_count = sum(
        1 for t in all_tracks if getattr(t, "artist", "") == track.artist
    )
    if artist_count > 3:
        return 0.2  # Very redundant
    if artist_count > 2:
        return 0.4
    return 0.7


CURATION_SCORERS: dict[str, Callable[[TrackMetadata, PlaylistContext, list], float]] = {
    "narrative_fit": score_narrative_fit,
    "mood_contribution": score_mood_contribution,
    "sonic_role": score_sonic_role,
    "energy_role": score_energy_role,
    "uniqueness": score_uniqueness,
    "redundancy": score_redundancy,
}


def score_track_contribution(
    track: TrackMetadata, ctx: PlaylistContext, all_tracks: list
) -> dict[str, float]:
    """Score a track across all curation dimensions."""
    return {
        name: scorer(track, ctx, all_tracks)
        for name, scorer in CURATION_SCORERS.items()
    }
