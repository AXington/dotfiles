"""Find candidate tracks to fill composition gaps."""

from __future__ import annotations

import re

from tuneshift.composer.models import Candidate, GapSpec, PlaylistConcept
from tuneshift.db import Database
from tuneshift.models import Track

_WORD_RE = re.compile(r"[a-z0-9']+")


def _word_set(*values: str | None) -> set[str]:
    words: set[str] = set()
    for value in values:
        if not value:
            continue
        words.update(_WORD_RE.findall(value.casefold()))
    return words


def _track_terms(track: Track) -> set[str]:
    metadata = track.metadata or {}
    terms = _word_set(track.title, track.artist, track.album)
    for group in (
        track.themes,
        metadata.get("vibes", []),
        metadata.get("era_mood", []),
        metadata.get("lastfm_tags", []),
    ):
        for item in group:
            terms.update(_WORD_RE.findall(str(item).casefold()))
    for key in (
        "lyrical_subject",
        "narrator_stance",
        "sonic_texture",
        "space",
        "groove_feel",
        "opens_with",
        "closes_with",
        "energy_arc_within",
    ):
        value = metadata.get(key)
        if value:
            terms.update(_WORD_RE.findall(str(value).casefold()))
    return terms


def _concept_terms(concept: PlaylistConcept | None) -> set[str]:
    if concept is None:
        return set()
    terms = _word_set(concept.theme, concept.era)
    for group in (concept.hard_rules, concept.soft_rules, concept.genres):
        for item in group:
            terms.update(_WORD_RE.findall(item.casefold()))
    return terms


def _track_intensity(track: Track) -> float | None:
    metadata = track.metadata or {}
    value = metadata.get("emotional_intensity", track.energy)
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _intensity_score(track: Track, intensity_range: tuple[float, float]) -> float:
    track_intensity = _track_intensity(track)
    if track_intensity is None:
        return 0.0
    minimum, maximum = intensity_range
    if minimum <= track_intensity <= maximum:
        center = (minimum + maximum) / 2
        half_span = max((maximum - minimum) / 2, 0.05)
        return max(0.0, 1.0 - (abs(track_intensity - center) / half_span) * 0.5)
    distance = min(abs(track_intensity - minimum), abs(track_intensity - maximum))
    return max(0.0, 1.0 - distance * 3.0)


def _stance_score(track: Track, stance: str | None) -> float:
    if stance is None:
        return 1.0
    track_stance = (track.metadata or {}).get("narrator_stance")
    if not track_stance:
        return 0.0
    return 1.0 if str(track_stance).casefold() == stance.casefold() else 0.0


def _keyword_score(
    track: Track, gap: GapSpec, concept: PlaylistConcept | None
) -> float:
    target_terms = {item.casefold() for item in gap.mood}
    target_terms.update(item.casefold() for item in gap.keywords)
    target_terms |= _concept_terms(concept)
    if not target_terms:
        return 1.0
    overlap = _track_terms(track) & target_terms
    return len(overlap) / len(target_terms) if overlap else 0.0


def _score_track(track: Track, gap: GapSpec, concept: PlaylistConcept | None) -> float:
    return (
        0.4 * _intensity_score(track, gap.intensity_range)
        + 0.3 * _stance_score(track, gap.stance)
        + 0.3 * _keyword_score(track, gap, concept)
    )


def find_candidates(
    gap: GapSpec,
    db: Database | None = None,
    concept: PlaylistConcept | None = None,
    tiers: list[str] | None = None,
    exclude_ids: set[int] | None = None,
    limit: int = 5,
) -> list[Candidate]:
    """Find candidate tracks for a composition gap from the local library."""
    if db is None:
        return []

    requested_tiers = tiers or ["library"]
    if "library" not in requested_tiers:
        return []

    excluded_track_ids = exclude_ids or set()
    search_keywords = list(dict.fromkeys([*gap.keywords, *gap.mood]))
    tracks = db.search_tracks_by_metadata(
        intensity_range=gap.intensity_range,
        stance=gap.stance,
        keywords=search_keywords,
        limit=max(limit * 5, 20),
    )

    deduped: dict[tuple[str, str], Candidate] = {}
    for track in tracks:
        if track.id in excluded_track_ids:
            continue
        fitness_score = _score_track(track, gap, concept)
        candidate = Candidate(
            title=track.title,
            artist=track.artist,
            source="library",
            fitness_score=fitness_score,
            track_id=track.id,
            isrc=track.isrc,
        )
        dedupe_key = (track.artist.casefold(), track.title.casefold())
        existing = deduped.get(dedupe_key)
        if existing is None or candidate.fitness_score > existing.fitness_score:
            deduped[dedupe_key] = candidate

    ranked = sorted(deduped.values(), key=lambda item: item.fitness_score, reverse=True)
    return ranked[:limit]
