"""Context-aware scoring modifiers for the sequence optimizer."""

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tuneshift.sequencer.metadata import TrackMetadata

if TYPE_CHECKING:
    from tuneshift.sequencer.intent import PlaylistIntent

SCORE_FLOOR = 0.05


@dataclass
class SequenceContext:
    """Running state tracked during greedy sequence construction."""

    recent_tracks: list[TrackMetadata] = field(default_factory=list)
    seen_artists: dict[str, int] = field(default_factory=dict)
    recent_themes: Counter = field(default_factory=Counter)
    recent_energies: list[float] = field(default_factory=list)
    position: int = 0
    total: int = 0
    narrative_mode: str = "river"
    context_window: int = 5

    def advance(self, track: TrackMetadata) -> None:
        """Update context after placing a track."""
        self.recent_tracks.append(track)
        if len(self.recent_tracks) > self.context_window:
            self.recent_tracks = self.recent_tracks[-self.context_window :]

        self.seen_artists[track.artist] = self.position

        self.recent_themes = Counter()
        for recent_track in self.recent_tracks:
            for tag in recent_track.themes + recent_track.vibes:
                self.recent_themes[tag] += 1

        energy = track.energy if track.energy is not None else 0.5
        self.recent_energies.append(energy)
        if len(self.recent_energies) > self.context_window:
            self.recent_energies = self.recent_energies[-self.context_window :]

        self.position += 1


def artist_recency_penalty(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Penalize candidates whose artist appeared recently."""
    if candidate.artist not in context.seen_artists:
        return 1.0

    last_pos = context.seen_artists[candidate.artist]
    distance = context.position - last_pos

    if distance <= 0:
        return 0.10 * strength + (1.0 - strength)

    decay_table = {
        1: 0.10,
        2: 0.15,
        3: 0.25,
        4: 0.40,
        5: 0.55,
        6: 0.70,
        7: 0.82,
        8: 0.90,
        9: 0.95,
    }
    raw_penalty = decay_table.get(distance, 1.0)
    return raw_penalty * strength + 1.0 * (1.0 - strength)


def artist_variety_bonus(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Bonus for artists not yet heard in the playlist."""
    if candidate.artist not in context.seen_artists:
        return 1.0 + 0.12 * strength
    return 1.0


def subgenre_staleness_penalty(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Penalize candidates whose tags heavily overlap with the recent window."""
    if not context.recent_themes:
        return 1.0

    candidate_tags = set(candidate.themes + candidate.vibes)
    if not candidate_tags:
        return 1.0

    window_size = len(context.recent_tracks) or 1
    saturation = 0.0
    for tag in candidate_tags:
        saturation += context.recent_themes.get(tag, 0) / window_size

    avg_saturation = saturation / len(candidate_tags)
    if avg_saturation <= 0.6:
        return 1.0

    penalty = 1.0 - 0.3 * ((avg_saturation - 0.6) / 0.4)
    penalty = max(0.7, penalty)
    return penalty * strength + 1.0 * (1.0 - strength)


def era_diversity_bonus(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Bonus for tracks from a different era than recent tracks."""
    if not context.recent_tracks or not candidate.era_mood:
        return 1.0

    recent_eras: set[str] = set()
    for recent_track in context.recent_tracks:
        recent_eras.update(recent_track.era_mood)

    if not recent_eras:
        return 1.0

    candidate_eras = set(candidate.era_mood)
    overlap = len(candidate_eras & recent_eras)
    total_candidate = len(candidate_eras) or 1
    novelty = 1.0 - (overlap / total_candidate)
    if novelty > 0.5:
        return 1.0 + 0.08 * strength
    return 1.0


def energy_monotony_penalty(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Penalize tracks that continue an energy flatline."""
    if len(context.recent_energies) < 3:
        return 1.0

    mean = sum(context.recent_energies) / len(context.recent_energies)
    variance = sum((energy - mean) ** 2 for energy in context.recent_energies) / len(
        context.recent_energies
    )

    if variance > 0.02:
        return 1.0

    candidate_energy = candidate.energy if candidate.energy is not None else 0.5
    deviation_from_mean = abs(candidate_energy - mean)

    if deviation_from_mean > 0.15:
        return 1.0 + 0.1 * strength
    return 0.85 * strength + 1.0 * (1.0 - strength)


def narrative_arc_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Narrative-mode-specific modifier."""
    mode = context.narrative_mode
    if mode == "river":
        return _river_modifier(candidate, context, strength)
    if mode == "chapter":
        return _chapter_modifier(candidate, context, strength)
    if mode == "dj_set":
        return _dj_set_modifier(candidate, context, strength)
    return 1.0


def _river_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float,
) -> float:
    """Reward gradual drift, penalize stagnation or jarring jumps."""
    if not context.recent_tracks:
        return 1.0

    current = context.recent_tracks[-1]
    current_tags = set(current.themes + current.vibes)
    candidate_tags = set(candidate.themes + candidate.vibes)

    if not current_tags or not candidate_tags:
        return 1.0

    overlap = len(current_tags & candidate_tags)
    union = len(current_tags | candidate_tags)
    similarity = overlap / union if union > 0 else 0.5

    if 0.3 <= similarity <= 0.7:
        return 1.0 + 0.05 * strength
    if similarity > 0.9:
        return 0.9 * strength + 1.0 * (1.0 - strength)
    if similarity < 0.1:
        return 0.9 * strength + 1.0 * (1.0 - strength)
    return 1.0


def _chapter_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float,
) -> float:
    """After a similar run, boost contrast."""
    if len(context.recent_tracks) < 3:
        return 1.0

    recent_tags_list = [
        set(track.themes + track.vibes) for track in context.recent_tracks[-3:]
    ]
    if not all(recent_tags_list):
        return 1.0

    similarities: list[float] = []
    for left_index in range(len(recent_tags_list)):
        for right_index in range(left_index + 1, len(recent_tags_list)):
            left_tags = recent_tags_list[left_index]
            right_tags = recent_tags_list[right_index]
            union = len(left_tags | right_tags)
            if union > 0:
                similarities.append(len(left_tags & right_tags) / union)

    avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0

    candidate_tags = set(candidate.themes + candidate.vibes)
    last_tags = recent_tags_list[-1]
    union = len(candidate_tags | last_tags)
    candidate_similarity = len(candidate_tags & last_tags) / union if union > 0 else 0.5

    if avg_similarity > 0.7:
        if candidate_similarity < 0.4:
            return 1.0 + 0.15 * strength
        if candidate_similarity > 0.7:
            return 0.85 * strength + 1.0 * (1.0 - strength)
    return 1.0


def _dj_set_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float,
) -> float:
    """Reward tracks that fit the energy arc at this position."""
    import math

    if context.total <= 1:
        return 1.0

    frac = context.position / max(context.total - 1, 1)
    target_energy = 0.5 + 0.3 * math.sin(2 * math.pi * frac)

    candidate_energy = candidate.energy if candidate.energy is not None else 0.5
    deviation = abs(candidate_energy - target_energy)

    if deviation < 0.15:
        return 1.0 + 0.05 * strength
    if deviation > 0.35:
        return 0.85 * strength + 1.0 * (1.0 - strength)
    return 1.0


def score_candidate(
    candidate: TrackMetadata,
    current: TrackMetadata,
    context: SequenceContext,
    base_score: float,
    penalty_strengths: dict[str, float] | None = None,
    intent: "PlaylistIntent | None" = None,
) -> float:
    """Apply all context modifiers to a base pairwise score."""
    strengths = penalty_strengths or {}

    effective_base = base_score
    if candidate.artist == current.artist:
        effective_base = min(base_score, 0.55)

    modifiers = [
        artist_recency_penalty(
            candidate, context, strengths.get("artist_recency", 1.0)
        ),
        artist_variety_bonus(candidate, context, strengths.get("artist_variety", 1.0)),
        subgenre_staleness_penalty(
            candidate,
            context,
            strengths.get("subgenre_staleness", 1.0),
        ),
        era_diversity_bonus(candidate, context, strengths.get("era_diversity", 1.0)),
        energy_monotony_penalty(
            candidate,
            context,
            strengths.get("energy_monotony", 1.0),
        ),
        narrative_arc_modifier(candidate, context, strengths.get("narrative_arc", 1.0)),
        intensity_arc_modifier(candidate, context, strengths.get("intensity_arc", 1.0)),
        chapter_break_modifier(
            candidate, context, intent, strengths.get("chapter_break", 1.0)
        ),
        duration_pacing_modifier(
            candidate, context, strengths.get("duration_pacing", 1.0)
        ),
    ]

    product = 1.0
    for modifier in modifiers:
        product *= modifier

    result = effective_base * product
    return max(result, SCORE_FLOOR)


def _intensity_curve(frac: float) -> float:
    """Emotional intensity target independent of energy."""
    if frac < 0.15:
        return 0.4
    if frac < 0.35:
        return 0.55
    if frac < 0.55:
        return 0.7
    if frac < 0.75:
        return 0.95
    if frac < 0.90:
        return 0.6
    return 0.45


def intensity_arc_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Reward tracks whose emotional intensity fits the narrative position."""
    if context.total <= 1:
        return 1.0
    intensity = candidate.emotional_intensity
    if intensity is None:
        return 1.0
    frac = context.position / max(context.total - 1, 1)
    target = _intensity_curve(frac)
    fit = 1.0 - abs(intensity - target)
    return (0.85 + 0.30 * fit) * strength + 1.0 * (1.0 - strength)


def chapter_break_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    intent: "PlaylistIntent | None" = None,
    strength: float = 1.0,
) -> float:
    """At chapter boundaries, reward contrast."""
    if intent is None or context.position not in intent.chapter_boundaries:
        return 1.0

    recent_textures = {
        t.sonic_texture for t in context.recent_tracks if t.sonic_texture
    }
    recent_stances = {
        t.narrator_stance for t in context.recent_tracks if t.narrator_stance
    }

    novelty_bonus = 0.0
    if candidate.sonic_texture and candidate.sonic_texture not in recent_textures:
        novelty_bonus += 0.1
    if candidate.narrator_stance and candidate.narrator_stance not in recent_stances:
        novelty_bonus += 0.1

    return (1.0 + novelty_bonus) * strength + 1.0 * (1.0 - strength)


def duration_pacing_modifier(
    candidate: TrackMetadata,
    context: SequenceContext,
    strength: float = 1.0,
) -> float:
    """Penalize same-length runs."""
    if not candidate.duration_ms:
        return 1.0

    recent_durations = [t.duration_ms for t in context.recent_tracks if t.duration_ms]
    if len(recent_durations) >= 3:
        avg_recent = sum(recent_durations) / len(recent_durations)
        if abs(candidate.duration_ms - avg_recent) < 25000:
            return 0.92 * strength + 1.0 * (1.0 - strength)

    return 1.0
