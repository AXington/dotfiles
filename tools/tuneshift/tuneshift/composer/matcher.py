"""Concept-aware track matching for composer sections."""

from __future__ import annotations

import re

from tuneshift.composer.models import (
    EnhancedSection,
    MisfitTrack,
    PlaylistConcept,
    SectionAssignments,
)
from tuneshift.sequencer.metadata import TrackMetadata

_WORD_RE = re.compile(r"[a-z0-9']+")
_STANCE_ALIASES: dict[str, set[str]] = {
    "defiant": {"defiant", "angry", "fierce", "rebellious"},
    "vulnerable": {"vulnerable", "gentle", "introspective", "tender"},
    "triumphant": {"triumphant", "empowered", "victorious", "anthemic"},
    "peaceful": {"peaceful", "calm", "still", "quiet"},
}


def _normalize_text(value: str) -> str:
    """Word-tokenize + casefold text for concept/theme overlap scoring.

    NOT a title/artist normalizer: this is the sequencer's CONCEPT tokenizer
    (deliberately separate from db/matching/identity normalizers). It extracts
    ``[a-z0-9']+`` word runs and joins them, discarding punctuation. Contract
    pinned by tests/matching/test_normalizer_contracts.py.
    """
    return " ".join(_WORD_RE.findall(value.casefold()))


def _word_set(*values: str | None) -> set[str]:
    words: set[str] = set()
    for value in values:
        if not value:
            continue
        words.update(_WORD_RE.findall(value.casefold()))
    return words


def _track_terms(track: TrackMetadata) -> set[str]:
    terms = _word_set(
        track.title,
        track.artist,
        track.lyrical_subject,
        track.narrator_stance,
        track.energy_arc_within,
    )
    for group in (track.themes, track.vibes, track.era_mood, track.lastfm_tags):
        for item in group:
            terms.update(_WORD_RE.findall(item.casefold()))
    return terms


def _concept_terms(concept: PlaylistConcept | None) -> set[str]:
    if concept is None:
        return set()
    terms = _word_set(concept.theme, concept.era)
    for group in (concept.hard_rules, concept.soft_rules, concept.genres):
        for item in group:
            terms.update(_WORD_RE.findall(item.casefold()))
    return terms


def _stance_alignment(track: TrackMetadata, section: EnhancedSection) -> float:
    if not section.implied_stance:
        return 1.0
    if not track.narrator_stance:
        return 0.5
    track_stance = track.narrator_stance.casefold()
    target = section.implied_stance.casefold()
    if track_stance == target:
        return 1.0
    if track_stance in _STANCE_ALIASES.get(target, set()):
        return 0.8
    return 0.0


def _intensity_alignment(track: TrackMetadata, section: EnhancedSection) -> float:
    track_intensity = track.emotional_intensity
    if track_intensity is None:
        track_intensity = track.energy if track.energy is not None else 0.5
    return max(0.0, 1.0 - abs(track_intensity - section.implied_intensity))


def _overlap_score(left: set[str], right: set[str], neutral: float = 0.5) -> float:
    if not left or not right:
        return neutral
    overlap = left & right
    if not overlap:
        return 0.0
    return len(overlap) / len(left | right)


def _mood_theme_alignment(
    track: TrackMetadata,
    section: EnhancedSection,
    concept: PlaylistConcept | None,
) -> float:
    target = {mood.casefold() for mood in section.mood} | _concept_terms(concept)
    return _overlap_score(_track_terms(track), target)


def _description_alignment(
    track: TrackMetadata,
    section: EnhancedSection,
    concept: PlaylistConcept | None,
) -> float:
    description_words = _word_set(section.description, section.section_concept)
    if concept is not None:
        description_words |= _word_set(concept.theme)
    return _overlap_score(_track_terms(track), description_words)


def _score_track_for_section(
    track: TrackMetadata,
    section: EnhancedSection,
    concept: PlaylistConcept | None,
) -> float:
    return (
        0.3 * _stance_alignment(track, section)
        + 0.25 * _intensity_alignment(track, section)
        + 0.25 * _mood_theme_alignment(track, section, concept)
        + 0.2 * _description_alignment(track, section, concept)
    )


def _find_required_track(
    tracks: list[TrackMetadata],
    used_ids: set[int],
    required_title: str,
) -> TrackMetadata | None:
    normalized_required = _normalize_text(required_title)
    for track in tracks:
        if track.track_id in used_ids:
            continue
        if _normalize_text(track.title) == normalized_required:
            return track
    return None


def _find_required_artist(
    tracks: list[TrackMetadata],
    used_ids: set[int],
    required_artist: str,
) -> TrackMetadata | None:
    normalized_required = _normalize_text(required_artist)
    for track in tracks:
        if track.track_id in used_ids:
            continue
        if _normalize_text(track.artist) == normalized_required:
            return track
    return None


def match_tracks_to_sections(
    tracks: list[TrackMetadata],
    sections: list[EnhancedSection],
    concept: PlaylistConcept | None,
) -> SectionAssignments:
    """Assign tracks to sections using concept-aware greedy scoring."""
    if not sections:
        return SectionAssignments(assignments={}, misfits=[], unassigned=list(tracks))

    assignments: dict[str, list[TrackMetadata]] = {
        section.name: [] for section in sections
    }
    used_ids: set[int] = set()
    fitness_by_assignment: dict[tuple[str, int], float] = {}

    for section in sections:
        for required_title in section.required_tracks:
            track = _find_required_track(tracks, used_ids, required_title)
            if track is None:
                continue
            assignments[section.name].append(track)
            used_ids.add(track.track_id)
            fitness_by_assignment[(section.name, track.track_id)] = 1.0
        for required_artist in section.required_artists:
            track = _find_required_artist(tracks, used_ids, required_artist)
            if track is None:
                continue
            assignments[section.name].append(track)
            used_ids.add(track.track_id)
            fitness_by_assignment[(section.name, track.track_id)] = 1.0

    scored_pairs: list[tuple[float, TrackMetadata, EnhancedSection]] = []
    for track in tracks:
        if track.track_id in used_ids:
            continue
        for section in sections:
            if len(assignments[section.name]) >= section.capacity:
                continue
            scored_pairs.append(
                (_score_track_for_section(track, section, concept), track, section)
            )

    scored_pairs.sort(key=lambda item: item[0], reverse=True)

    for fitness, track, section in scored_pairs:
        if track.track_id in used_ids:
            continue
        if len(assignments[section.name]) >= section.capacity:
            continue
        assignments[section.name].append(track)
        used_ids.add(track.track_id)
        fitness_by_assignment[(section.name, track.track_id)] = fitness

    misfits: list[MisfitTrack] = []
    for section in sections:
        for track in assignments[section.name]:
            fitness = fitness_by_assignment[(section.name, track.track_id)]
            if fitness >= 0.3:
                continue
            misfits.append(
                MisfitTrack(
                    track=track,
                    section_name=section.name,
                    fitness_score=fitness,
                    explanation=f"Low section fitness for {section.name}",
                )
            )

    unassigned = [track for track in tracks if track.track_id not in used_ids]
    return SectionAssignments(
        assignments=assignments,
        misfits=misfits,
        unassigned=unassigned,
    )
