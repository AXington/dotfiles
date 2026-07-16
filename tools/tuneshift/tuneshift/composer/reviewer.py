"""Review composed playlists for section integrity and transition quality."""

from __future__ import annotations

import re

from tuneshift.composer.concept_llm import ConceptJudge, TrackCtx
from tuneshift.composer.models import (
    EnhancedSection,
    PlaylistConcept,
    ReviewFinding,
    SectionAssignments,
    TransitionType,
)
from tuneshift.composer.rules import (
    RuleKind,
    classify_rule,
    normalize_rule_key,
    parse_era,
)
from tuneshift.models import Artist
from tuneshift.sequencer.metadata import TrackMetadata

# Pattern matchers for common hard/soft rules
_ARTIST_MUST_BE_RE = re.compile(r"artist must be (\w+)", re.IGNORECASE)
_NO_GENRE_RE = re.compile(r"no (\w+)", re.IGNORECASE)

# "queer" is an umbrella: any of these tags satisfies "artist must be queer"
_QUEER_UMBRELLA = frozenset(
    {
        "queer",
        "gay",
        "lesbian",
        "bisexual",
        "trans",
        "nonbinary",
        "pansexual",
        "genderqueer",
        "genderfluid",
        "intersex",
        "asexual",
        "two-spirit",
        "questioning",
    }
)


def _energy_value(track: TrackMetadata) -> float:
    if track.energy is not None:
        return max(0.0, min(1.0, track.energy))
    if track.emotional_intensity is not None:
        return max(0.0, min(1.0, track.emotional_intensity))
    return 0.5


def _resolve_transition_type(
    current: EnhancedSection,
    nxt: EnhancedSection,
) -> TransitionType:
    if current.transition_out is not TransitionType.GRADUAL:
        return current.transition_out
    return nxt.transition_in


def _score_transition(
    transition_type: TransitionType,
    last_track: TrackMetadata,
    first_track: TrackMetadata,
) -> float:
    last_energy = _energy_value(last_track)
    first_energy = _energy_value(first_track)
    energy_delta = abs(last_energy - first_energy)

    if transition_type is TransitionType.SHARP_CUT:
        return min(1.0, energy_delta * 2)
    if transition_type is TransitionType.COLLAPSE:
        if last_energy > first_energy:
            return min(1.0, (last_energy - first_energy) * 2)
        return 0.2
    if transition_type is TransitionType.BUILD:
        if first_energy >= last_energy:
            return 1.0
        return max(0.3, 1.0 - energy_delta)
    if transition_type is TransitionType.GRADUAL:
        return max(0.0, 1.0 - energy_delta * 2)
    return max(0.0, 1.0 - energy_delta * 3)


def _review_section_integrity(
    ordered_tracks: list[TrackMetadata],
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    positions = {
        track.track_id: index + 1 for index, track in enumerate(ordered_tracks)
    }

    for section in sections:
        for required_title in section.required_tracks:
            assigned_tracks = assignments.assignments.get(section.name, [])
            if not any(
                track.title.casefold() == required_title.casefold()
                for track in assigned_tracks
            ):
                findings.append(
                    ReviewFinding(
                        category="section_integrity",
                        description=(
                            f'{section.name} does not contain required track "{required_title}" '  # noqa: E501
                            "in its assignment."
                        ),
                        severity=1.0,
                        section_name=section.name,
                    )
                )
                continue

            matching = [
                track
                for track in ordered_tracks
                if track.title.casefold() == required_title.casefold()
            ]
            if not matching:
                findings.append(
                    ReviewFinding(
                        category="section_integrity",
                        description=f'{section.name} is missing required track "{required_title}".',  # noqa: E501
                        severity=1.0,
                        section_name=section.name,
                    )
                )
                continue

            for track in matching:
                position = positions[track.track_id]
                if section.start_position <= position <= section.end_position:
                    break
            else:
                findings.append(
                    ReviewFinding(
                        category="section_integrity",
                        description=(
                            f'"{required_title}" falls outside the declared {section.name} '  # noqa: E501
                            "section range."
                        ),
                        severity=0.8,
                        section_name=section.name,
                    )
                )

    return findings


def _review_transition_quality(
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []

    for index in range(len(sections) - 1):
        current = sections[index]
        nxt = sections[index + 1]
        current_tracks = assignments.assignments.get(current.name, [])
        next_tracks = assignments.assignments.get(nxt.name, [])
        if not current_tracks or not next_tracks:
            continue

        transition_type = _resolve_transition_type(current, nxt)
        quality = _score_transition(transition_type, current_tracks[-1], next_tracks[0])
        if quality >= 0.4:
            continue

        findings.append(
            ReviewFinding(
                category="transition_quality",
                description=(
                    f"{current.name} to {nxt.name} has weak {transition_type.value} "
                    f"boundary quality ({quality:.2f})."
                ),
                severity=1.0 - quality,
                section_name=current.name,
            )
        )

    return findings


def _check_rule_against_artist(rule: str, artist: Artist) -> bool | None:
    """Check if an artist satisfies a rule. Returns True/False/None (can't determine)."""  # noqa: E501
    match = _ARTIST_MUST_BE_RE.match(rule)
    if match:
        required_tag = match.group(1).casefold()

        # "queer" is an umbrella: any LGBTQ+ tag satisfies it
        if required_tag == "queer":
            if artist.tags:
                artist_tags_lower = {t.casefold() for t in artist.tags}
                if artist_tags_lower & _QUEER_UMBRELLA:
                    return True
            # Check identity dict for sexuality/gender_identity
            if artist.identity:
                identity_values = " ".join(
                    str(v) for v in artist.identity.values()
                ).casefold()
                if any(term in identity_values for term in _QUEER_UMBRELLA):
                    return True
                if artist.identity_confidence == "confirmed":
                    return False
            if not artist.tags and not artist.identity:
                return None
            return False

        # Specific tag check (e.g., "artist must be trans")
        if not artist.tags:
            if artist.identity:
                identity_values = " ".join(
                    str(v) for v in artist.identity.values()
                ).casefold()
                if required_tag in identity_values:
                    return True
                if artist.identity_confidence == "confirmed":
                    return False
            return None
        return required_tag in [t.casefold() for t in artist.tags]
    return None  # rule pattern not recognized


def _enforce_artist_tag(
    rule: str,
    tracks: list[TrackMetadata],
    artist_lookup: dict[str, Artist],
    accepted_ids: frozenset[int] = frozenset(),
) -> list[ReviewFinding]:
    """Enforce an ``artist must be <tag>`` rule per track (existing behaviour)."""
    findings: list[ReviewFinding] = []
    for track in tracks:
        if track.track_id in accepted_ids:
            continue
        artist_key = track.artist.casefold() if track.artist else ""
        artist = artist_lookup.get(artist_key)
        if artist is None:
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'HARD: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - artist not in library, cannot verify'
                    ),
                    severity=0.5,
                    section_name=None,
                )
            )
            continue
        result = _check_rule_against_artist(rule, artist)
        if result is False:
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'HARD: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - FAILS '
                        f"(tags: {artist.tags}, confidence: {artist.identity_confidence})"  # noqa: E501
                    ),
                    severity=1.0,
                    section_name=None,
                )
            )
        elif result is None:
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'UNKNOWN: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - artist not enriched, cannot verify'
                    ),
                    severity=0.3,
                    section_name=None,
                )
            )
    return findings


def _enforce_era(
    rule: str,
    tracks: list[TrackMetadata],
    year_lookup: dict[int, int | None],
    accepted_ids: frozenset[int] = frozenset(),
) -> list[ReviewFinding]:
    """Enforce an era/year rule deterministically against each track's release year."""
    era_range = parse_era(rule)
    if era_range is None:
        return []
    lo, hi = era_range
    findings: list[ReviewFinding] = []
    for track in tracks:
        if track.track_id in accepted_ids:
            continue
        year = year_lookup.get(track.track_id)
        if year is None:
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'UNKNOWN: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - release year unavailable, cannot verify'
                    ),
                    severity=0.3,
                    section_name=None,
                )
            )
        elif not (lo <= year <= hi):
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'HARD: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - FAILS (released {year}, outside {lo}-{hi})'
                    ),
                    severity=1.0,
                    section_name=None,
                )
            )
    return findings


def _enforce_thematic_unavailable(
    rule: str,
    tracks: list[TrackMetadata],  # noqa: ARG001 - rule-handler signature; emits once per rule
) -> list[ReviewFinding]:
    """Placeholder for a thematic rule with no LLM judge (replaced in Chunk 2).

    Emitted ONCE per rule (not per track), so it never claims "artist not in
    library" for a rule that has nothing to do with artist identity.
    """
    return [
        ReviewFinding(
            category="concept_violation",
            description=(
                f'UNKNOWN: Rule "{rule}" is a thematic rule - requires an LLM backend '
                f"to evaluate; none supplied."
            ),
            severity=0.3,
            section_name=None,
        )
    ]


def _enforce_thematic_llm(
    rule: str,
    tracks: list[TrackMetadata],
    llm_judge: ConceptJudge,
    accepted_ids: frozenset[int] = frozenset(),
) -> list[ReviewFinding]:
    """Enforce a thematic rule via one batched LLM verdict per rule.

    ``violates`` -> a hard finding (severity 1.0); ``unsure`` -> unknown
    (0.3); ``complies`` -> no finding. The judge itself degrades to "unsure"
    on any backend/parse failure, so a flaky model never blocks a review.
    Tracks already accepted for this rule are excluded from the judged batch.
    """
    judged = [t for t in tracks if t.track_id not in accepted_ids]
    if not judged:
        return []
    contexts = [
        TrackCtx(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            themes=list(track.themes),
            lyrical_subject=track.lyrical_subject,
        )
        for track in judged
    ]
    verdicts = llm_judge(rule, contexts)
    findings: list[ReviewFinding] = []
    for track in judged:
        verdict = verdicts.get(track.track_id, "unsure")
        if verdict == "violates":
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'HARD: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - FAILS (LLM judged the track violates it)'
                    ),
                    severity=1.0,
                    section_name=None,
                )
            )
        elif verdict != "complies":
            findings.append(
                ReviewFinding(
                    category="concept_violation",
                    description=(
                        f'UNKNOWN: "{track.title}" by {track.artist} - '
                        f'Rule: "{rule}" - LLM was unsure, cannot verify'
                    ),
                    severity=0.3,
                    section_name=None,
                )
            )
    return findings


def _review_soft_rules(
    tracks: list[TrackMetadata],
    concept: PlaylistConcept,
) -> list[ReviewFinding]:
    """Soft-rule mood-contradiction check against track vibes/themes."""
    findings: list[ReviewFinding] = []
    contradictions = {
        ("celebration", "joy", "pride", "happy", "upbeat"): {
            "sad",
            "depressing",
            "heartbreak",
            "grief",
            "mourning",
        },
    }
    for rule in concept.soft_rules:
        rule_words = set(re.findall(r"[a-z]+", rule.casefold()))
        for track in tracks:
            track_words: set[str] = set()
            for group in (track.vibes, track.themes):
                for item in group:
                    track_words.update(re.findall(r"[a-z]+", item.casefold()))
            if track.lyrical_subject:
                track_words.update(
                    re.findall(r"[a-z]+", track.lyrical_subject.casefold())
                )
            for positive_set, negative_set in contradictions.items():
                if rule_words & set(positive_set) and track_words & negative_set:
                    findings.append(
                        ReviewFinding(
                            category="soft_rule_mismatch",
                            description=(
                                f'"{track.title}" by {track.artist} - '
                                f"vibes [{', '.join(track.vibes)}] may not fit "
                                f'playlist mood: "{rule}"'
                            ),
                            severity=0.5,
                            section_name=None,
                        )
                    )
    return findings


def _review_concept_compliance(
    tracks: list[TrackMetadata],
    concept: PlaylistConcept,
    artist_lookup: dict[str, Artist],
    *,
    year_lookup: dict[int, int | None] | None = None,
    llm_judge: ConceptJudge | None = None,
    accepted: set[tuple[int, str]] | None = None,
) -> list[ReviewFinding]:
    """Check tracks against concept hard_rules and soft_rules.

    Each hard rule is routed by :func:`classify_rule` to the right enforcer:
    ``artist must be <tag>`` uses the artist-identity check; a year/era rule is
    enforced deterministically against ``year_lookup``; a thematic rule is judged
    by ``llm_judge`` when supplied, and otherwise reported once as needing an LLM.

    A ``(track_id, rule_key)`` pair present in ``accepted`` is suppressed for that
    rule, so a user-accepted finding stays quiet across runs even if a thematic
    LLM verdict later flips.
    """
    years = year_lookup or {}
    accepted_pairs = accepted or set()
    findings: list[ReviewFinding] = []
    for rule in concept.hard_rules:
        rule_key = normalize_rule_key(rule)
        accepted_ids = frozenset(tid for (tid, rk) in accepted_pairs if rk == rule_key)
        kind = classify_rule(rule)
        if kind is RuleKind.ARTIST_TAG:
            findings.extend(
                _enforce_artist_tag(rule, tracks, artist_lookup, accepted_ids)
            )
        elif kind is RuleKind.ERA:
            findings.extend(_enforce_era(rule, tracks, years, accepted_ids))
        elif llm_judge is not None:
            findings.extend(
                _enforce_thematic_llm(rule, tracks, llm_judge, accepted_ids)
            )
        else:
            findings.extend(_enforce_thematic_unavailable(rule, tracks))
    findings.extend(_review_soft_rules(tracks, concept))
    return findings


def _review_section_fitness(
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
) -> list[ReviewFinding]:
    """Score each track against its assigned section's mood/intensity/stance."""
    findings: list[ReviewFinding] = []

    for section in sections:
        tracks = assignments.assignments.get(section.name, [])
        for track in tracks:
            score = 0.0
            checks = 0

            # Stance alignment
            if section.implied_stance and track.narrator_stance:
                checks += 1
                if (
                    track.narrator_stance.casefold()
                    == section.implied_stance.casefold()
                ):
                    score += 1.0
                else:
                    score += 0.2

            # Intensity alignment
            if section.implied_intensity is not None:
                track_intensity = track.emotional_intensity or track.energy or 0.5
                checks += 1
                score += max(
                    0.0, 1.0 - abs(track_intensity - section.implied_intensity)
                )

            # Mood overlap
            if section.mood and track.vibes:
                checks += 1
                section_moods = {m.casefold() for m in section.mood}
                track_vibes = {v.casefold() for v in track.vibes}
                overlap = section_moods & track_vibes
                score += len(overlap) / max(len(section_moods), 1)

            if checks == 0:
                continue

            fitness = score / checks
            if fitness < 0.25:
                findings.append(
                    ReviewFinding(
                        category="section_misfit",
                        description=(
                            f'"{track.title}" by {track.artist} - '
                            f"fitness {fitness:.2f} in {section.name} "
                            f"(section wants: {section.implied_stance or 'any'} / "
                            f"{', '.join(section.mood) if section.mood else 'any mood'})"  # noqa: E501
                        ),
                        severity=0.7,
                        section_name=section.name,
                    )
                )

    return findings


def review_composition(
    ordered_tracks: list[TrackMetadata],
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
    concept: PlaylistConcept | None = None,
    artist_lookup: dict[str, Artist] | None = None,
    *,
    year_lookup: dict[int, int | None] | None = None,
    llm_judge: ConceptJudge | None = None,
    accepted: set[tuple[int, str]] | None = None,
) -> list[ReviewFinding]:
    """Review section integrity, transition quality, and concept compliance."""
    findings = _review_section_integrity(ordered_tracks, assignments, sections)
    findings.extend(_review_transition_quality(assignments, sections))

    if sections:
        findings.extend(_review_section_fitness(assignments, sections))

    if concept and concept.has_hard_rules:
        findings.extend(
            _review_concept_compliance(
                ordered_tracks,
                concept,
                artist_lookup or {},
                year_lookup=year_lookup,
                llm_judge=llm_judge,
                accepted=accepted,
            )
        )
    elif concept and concept.soft_rules:
        findings.extend(
            _review_concept_compliance(
                ordered_tracks,
                concept,
                artist_lookup or {},
                year_lookup=year_lookup,
                llm_judge=llm_judge,
                accepted=accepted,
            )
        )

    return findings


def review_playlist(
    tracks: list[TrackMetadata],
    concept: PlaylistConcept | None = None,
    artist_lookup: dict[str, Artist] | None = None,
    *,
    year_lookup: dict[int, int | None] | None = None,
    llm_judge: ConceptJudge | None = None,
    accepted: set[tuple[int, str]] | None = None,
) -> list[ReviewFinding]:
    """Review a playlist for concept compliance and vibe outliers.

    Checks:
    1. Concept hard/soft rules (if concept exists)
    2. Vibe cluster outliers (always, if enough tracks have metadata)
    """
    findings: list[ReviewFinding] = []
    if concept:
        findings.extend(
            _review_concept_compliance(
                tracks,
                concept,
                artist_lookup or {},
                year_lookup=year_lookup,
                llm_judge=llm_judge,
                accepted=accepted,
            )
        )
    findings.extend(_review_vibe_outliers(tracks))
    return findings


def _review_vibe_outliers(tracks: list[TrackMetadata]) -> list[ReviewFinding]:
    """Detect tracks that are sonic outliers from the playlist's vibe profile.

    Uses vibe categories (positive/negative/intense/mellow/dark) to group
    similar vibes together, avoiding false positives from inconsistent
    classifier vocabulary.
    """
    if len(tracks) < 5:
        return []

    # Map vibes to broad categories for fuzzy comparison
    _VIBE_CATEGORIES: dict[str, set[str]] = {
        "upbeat": {
            "upbeat",
            "energetic",
            "playful",
            "joyful",
            "fun",
            "lively",
            "bouncy",
            "danceable",
            "celebratory",
            "exuberant",
            "groovy",
        },
        "dark": {
            "dark",
            "haunting",
            "eerie",
            "sinister",
            "ominous",
            "gothic",
            "brooding",
            "foreboding",
            "menacing",
            "gloomy",
        },
        "melancholic": {
            "melancholic",
            "sad",
            "sorrowful",
            "longing",
            "wistful",
            "bittersweet",
            "nostalgic",
            "yearning",
            "mournful",
            "heartbreak",
        },
        "intense": {
            "intense",
            "passionate",
            "powerful",
            "anthemic",
            "dramatic",
            "emotive",
            "fierce",
            "urgent",
            "explosive",
            "raw",
        },
        "confident": {
            "confident",
            "assertive",
            "sassy",
            "empowering",
            "bold",
            "defiant",
            "swagger",
            "fierce",
            "proud",
            "independent",
        },
        "romantic": {
            "romantic",
            "sensual",
            "tender",
            "intimate",
            "loving",
            "flirtatious",
            "arousing",
            "seductive",
            "warm",
            "affectionate",
        },
        "chill": {
            "chill",
            "mellow",
            "relaxed",
            "laid-back",
            "dreamy",
            "smooth",
            "atmospheric",
            "ambient",
            "peaceful",
            "serene",
        },
        "country": {
            "country",
            "folk",
            "acoustic",
            "nashville",
            "twangy",
            "americana",
            "bluegrass",
            "honky-tonk",
        },
        "rock": {
            "rock",
            "gritty",
            "distorted",
            "heavy",
            "punk",
            "grunge",
            "alternative",
            "garage",
        },
    }

    def categorize_vibes(vibes: list[str]) -> set[str]:
        categories: set[str] = set()
        for v in vibes:
            v_lower = v.casefold()
            for cat, words in _VIBE_CATEGORIES.items():
                if v_lower in words:
                    categories.add(cat)
                    break
        return categories

    # Build playlist category profile
    cat_counts: dict[str, int] = {}
    tracks_with_vibes = 0

    for track in tracks:
        if track.vibes:
            tracks_with_vibes += 1
            cats = categorize_vibes(track.vibes)
            for c in cats:
                cat_counts[c] = cat_counts.get(c, 0) + 1

    if tracks_with_vibes < len(tracks) * 0.5:
        return []

    # Categories present in the playlist at all (at least 2 tracks)
    present_cats = {c for c, count in cat_counts.items() if count >= 2}

    if not present_cats:
        return []

    # Dominant categories for display (appearing in >25% of tracks)
    dominant_cats = {
        c for c, count in cat_counts.items() if count >= tracks_with_vibes * 0.25
    }

    findings: list[ReviewFinding] = []

    for track in tracks:
        if not track.vibes:
            continue

        track_cats = categorize_vibes(track.vibes)
        if not track_cats:
            continue

        # Check overlap with the playlist's present categories
        overlap = track_cats & present_cats
        foreign_cats = track_cats - present_cats

        # Pure outlier: NO overlap at all with anything on the playlist
        if not overlap:
            findings.append(
                ReviewFinding(
                    category="vibe_outlier",
                    description=(
                        f'"{track.title}" by {track.artist} - '
                        f"vibes [{', '.join(sorted(track.vibes)[:4])}] "
                        f"(categories: {', '.join(sorted(track_cats))}) "
                        f"do not match playlist profile "
                        f"(dominant: {', '.join(sorted(dominant_cats or present_cats))})"  # noqa: E501
                    ),
                    severity=0.7,
                    section_name=None,
                )
            )
        # Partial outlier: has foreign categories that appear nowhere else
        # AND those foreign categories are genre-defining (country, rock)
        elif foreign_cats & {"country", "rock"}:
            findings.append(
                ReviewFinding(
                    category="vibe_outlier",
                    description=(
                        f'"{track.title}" by {track.artist} - '
                        f"includes [{', '.join(sorted(foreign_cats))}] vibes "
                        f"not found elsewhere on this playlist"
                    ),
                    severity=0.5,
                    section_name=None,
                )
            )

    return findings
