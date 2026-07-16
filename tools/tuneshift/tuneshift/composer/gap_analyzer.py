"""Gap analysis for narrative section assignments."""

from __future__ import annotations

from tuneshift.composer.models import (
    EnhancedSection,
    GapReport,
    GapSpec,
    SectionAssignments,
    TransitionType,
)
from tuneshift.sequencer.metadata import TrackMetadata


def analyze_composition_gaps(
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
) -> list[GapReport]:
    """Inspect section assignments for coverage and flow problems."""
    gaps: list[GapReport] = []

    for section in sections:
        tracks = assignments.assignments.get(section.name, [])
        empty_slot_gap = _analyze_empty_slots(section, tracks)
        if empty_slot_gap is not None:
            gaps.append(empty_slot_gap)

        monotony_gap = _analyze_monotony(section, tracks)
        if monotony_gap is not None:
            gaps.append(monotony_gap)

    for index in range(len(sections) - 1):
        current = sections[index]
        nxt = sections[index + 1]
        current_tracks = assignments.assignments.get(current.name, [])
        next_tracks = assignments.assignments.get(nxt.name, [])
        transition_gap = _analyze_transition_gap(
            current,
            current_tracks,
            nxt,
            next_tracks,
        )
        if transition_gap is not None:
            gaps.append(transition_gap)

    return gaps


def _analyze_empty_slots(
    section: EnhancedSection,
    tracks: list[TrackMetadata],
) -> GapReport | None:
    count = len(tracks)
    if count < section.min_tracks:
        deficit = section.min_tracks - count
        severity = min(1.0, 0.8 + 0.2 * (deficit / max(section.min_tracks, 1)))
        return GapReport(
            gap_type="empty_slot",
            section_name=section.name,
            description=(
                f"{section.name} is below its minimum track count: "
                f"{count}/{section.min_tracks}."
            ),
            severity=severity,
            fill_spec=_build_fill_spec(section),
        )

    if section.capacity > count:
        fill_ratio = count / section.capacity if section.capacity else 1.0
        severity = max(0.15, min(0.7, (1.0 - fill_ratio) * 0.7))
        return GapReport(
            gap_type="empty_slot",
            section_name=section.name,
            description=(
                f"{section.name} has open capacity: {count}/{section.capacity} "
                "tracks assigned."
            ),
            severity=severity,
            fill_spec=_build_fill_spec(section),
        )

    return None


def _analyze_transition_gap(
    current: EnhancedSection,
    current_tracks: list[TrackMetadata],
    nxt: EnhancedSection,
    next_tracks: list[TrackMetadata],
) -> GapReport | None:
    if not current_tracks or not next_tracks:
        return None

    transition_style = _resolve_transition_style(current, nxt)
    if transition_style in {TransitionType.GRADUAL, TransitionType.SUSTAIN}:
        return None

    closer = current_tracks[-1]
    opener = next_tracks[0]

    if transition_style is TransitionType.COLLAPSE:
        if _supports_collapse(closer):
            return None
        energy = _energy_value(closer)
        severity = min(1.0, 0.55 + energy * 0.35)
        return GapReport(
            gap_type="transition_gap",
            section_name=current.name,
            description=(
                f"{current.name} should collapse into {nxt.name}, but its boundary "
                "track does not fade or drop in energy."
            ),
            severity=severity,
        )

    if transition_style is TransitionType.SHARP_CUT:
        contrast = _contrast_value(closer, opener)
        if contrast >= 0.45:
            return None
        severity = min(1.0, 0.45 + (0.45 - contrast) * 1.2)
        return GapReport(
            gap_type="transition_gap",
            section_name=current.name,
            description=(
                f"{current.name} to {nxt.name} needs a sharper boundary contrast."
            ),
            severity=severity,
        )

    return None


def _analyze_monotony(
    section: EnhancedSection,
    tracks: list[TrackMetadata],
) -> GapReport | None:
    if len(tracks) < 3:
        return None

    energies = [track.energy for track in tracks if track.energy is not None]
    if len(energies) < 3:
        return None

    spread = max(energies) - min(energies)
    if spread >= 0.1:
        return None

    severity = min(0.8, 0.4 + (0.1 - spread) * 3.0)
    return GapReport(
        gap_type="monotony",
        section_name=section.name,
        description=(
            f"{section.name} has too little internal energy variation "
            f"(range={spread:.2f})."
        ),
        severity=severity,
    )


def _build_fill_spec(section: EnhancedSection) -> GapSpec:
    center = _clamp(section.implied_intensity)
    intensity_range = (
        _clamp(center - 0.15),
        _clamp(center + 0.15),
    )
    keywords = [
        section.name.lower(),
        section.transition_in.value,
        section.transition_out.value,
    ]
    if section.implied_stance:
        keywords.append(section.implied_stance)
    for mood in section.mood:
        if mood not in keywords:
            keywords.append(mood)
    return GapSpec(
        section_name=section.name,
        mood=list(section.mood),
        intensity_range=intensity_range,
        stance=section.implied_stance,
        keywords=keywords,
    )


def _resolve_transition_style(
    current: EnhancedSection,
    nxt: EnhancedSection,
) -> TransitionType:
    if current.transition_out is not TransitionType.GRADUAL:
        return current.transition_out
    return nxt.transition_in


def _supports_collapse(track: TrackMetadata) -> bool:
    if (track.energy or 0.5) <= 0.35:
        return True
    arc = (track.energy_arc_within or "").lower()
    closing = (track.closes_with or "").lower()
    collapse_markers = ("fade", "fading", "decay", "descending", "falling")
    return any(marker in arc or marker in closing for marker in collapse_markers)


def _contrast_value(a: TrackMetadata, b: TrackMetadata) -> float:
    if a.energy is not None and b.energy is not None:
        return abs(a.energy - b.energy)
    if a.emotional_intensity is not None and b.emotional_intensity is not None:
        return abs(a.emotional_intensity - b.emotional_intensity)
    return 0.0


def _energy_value(track: TrackMetadata) -> float:
    if track.energy is not None:
        return _clamp(track.energy)
    if track.emotional_intensity is not None:
        return _clamp(track.emotional_intensity)
    return 0.5


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
