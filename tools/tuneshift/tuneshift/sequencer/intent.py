"""Playlist intent inference from track metadata."""

from collections import Counter
from dataclasses import dataclass, field

from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.narrative_parser import (  # noqa: F401
    NarrativeSection,
    parse_narrative,
)


@dataclass
class PlaylistIntent:
    """Inferred narrative intent for a playlist."""

    dominant_themes: list[str] = field(default_factory=list)
    emotional_range: tuple[float, float] = (0.0, 1.0)
    tonal_center: str = ""
    sonic_palette: list[str] = field(default_factory=list)
    climax_candidates: list[int] = field(default_factory=list)
    suggested_arc: str = "narrative"
    chapter_boundaries: list[int] = field(default_factory=list)


def infer_intent(
    tracks: list[TrackMetadata],
    narrative: str | None = None,
) -> PlaylistIntent:
    """Analyze track metadata to determine playlist narrative intent.

    If a narrative description is provided, uses it to derive chapter boundaries
    and climax regions rather than inferring purely from metadata.
    """
    if not tracks:
        return PlaylistIntent()

    theme_counter: Counter[str] = Counter()
    for t in tracks:
        for tag in t.themes + t.vibes:
            theme_counter[tag] += 1
    dominant_themes = [tag for tag, _ in theme_counter.most_common(5)]

    intensities = [
        t.emotional_intensity for t in tracks if t.emotional_intensity is not None
    ]
    if intensities:
        emotional_range = (min(intensities), max(intensities))
    else:
        emotional_range = (0.0, 1.0)

    stance_counter: Counter[str] = Counter()
    for t in tracks:
        if t.narrator_stance:
            stance_counter[t.narrator_stance] += 1
    tonal_center = stance_counter.most_common(1)[0][0] if stance_counter else ""

    texture_counter: Counter[str] = Counter()
    for t in tracks:
        if t.sonic_texture:
            texture_counter[t.sonic_texture] += 1
    sonic_palette = [tex for tex, _ in texture_counter.most_common(3)]

    # Parse narrative for explicit sections if provided
    if narrative:
        chapter_boundaries = _parse_narrative_sections(narrative, len(tracks))
        climax_candidates = _parse_narrative_climax(narrative, tracks)
    else:
        chapter_boundaries = _detect_chapters(tracks)
        climax_candidates = _infer_climax_from_metadata(tracks)

    intensity_spread = emotional_range[1] - emotional_range[0]
    suggested_arc = "narrative" if intensity_spread > 0.4 or narrative else "wave"

    return PlaylistIntent(
        dominant_themes=dominant_themes,
        emotional_range=emotional_range,
        tonal_center=tonal_center,
        sonic_palette=sonic_palette,
        climax_candidates=climax_candidates,
        suggested_arc=suggested_arc,
        chapter_boundaries=chapter_boundaries,
    )


def _detect_chapters(tracks: list[TrackMetadata], window: int = 3) -> list[int]:
    """Detect chapter boundaries by sliding window Jaccard similarity drop."""
    if len(tracks) < 4:
        return []

    effective_window = min(window, len(tracks) // 2)
    if effective_window < 1:
        return []

    boundaries: list[int] = []
    for i in range(effective_window, len(tracks) - effective_window + 1):
        left_tags: set[str] = set()
        for t in tracks[i - effective_window : i]:
            left_tags.update(t.themes + t.vibes)
        right_tags: set[str] = set()
        for t in tracks[i : min(i + effective_window, len(tracks))]:
            right_tags.update(t.themes + t.vibes)

        if not left_tags and not right_tags:
            continue
        union = len(left_tags | right_tags)
        if union == 0:
            continue
        similarity = len(left_tags & right_tags) / union
        if similarity < 0.3:
            if not boundaries or i - boundaries[-1] >= effective_window:
                boundaries.append(i)

    return boundaries


def _infer_climax_from_metadata(tracks: list[TrackMetadata]) -> list[int]:
    """Infer climax candidates from emotional intensity metadata."""
    if len(tracks) <= 3:
        n_climax = 1
    elif len(tracks) <= 5:
        n_climax = min(2, len(tracks))
    elif len(tracks) <= 15:
        n_climax = max(2, len(tracks) // 7)
    else:
        n_climax = max(2, len(tracks) // 10)

    sorted_by_intensity = sorted(
        [(t.track_id, t.emotional_intensity or 0.0) for t in tracks],
        key=lambda x: x[1],
        reverse=True,
    )
    return [tid for tid, _ in sorted_by_intensity[:n_climax]]


def _parse_narrative_sections(narrative: str, track_count: int) -> list[int]:
    """Extract chapter boundaries from narrative text.

    Looks for section headers with position ranges like "WRATH (11-18):"
    or "BUILD (3-8):" and returns the starting positions as boundaries.
    """
    import re

    boundaries: list[int] = []
    # Match patterns like "SECTION (N-M)" or "SECTION (N)"
    section_re = re.compile(r"[A-Z]+\s*\((\d+)(?:-\d+)?\)")
    for match in section_re.finditer(narrative):
        start = int(match.group(1)) - 1  # Convert 1-indexed to 0-indexed
        if 0 < start < track_count:
            boundaries.append(start)
    return sorted(boundaries)


def _parse_narrative_climax(narrative: str, tracks: list[TrackMetadata]) -> list[int]:
    """Identify climax tracks from narrative text.

    Looks for section names suggesting peak intensity (WRATH, CLIMAX, PEAK,
    ANTHEM) and returns track IDs in those regions.
    """
    import re

    climax_keywords = {"wrath", "climax", "peak", "anthem", "fury", "rage", "eruption"}
    section_re = re.compile(r"([A-Z]+)\s*\((\d+)-(\d+)\)")

    climax_positions: list[int] = []
    for match in section_re.finditer(narrative):
        section_name = match.group(1).lower()
        if section_name in climax_keywords:
            start = int(match.group(2)) - 1  # 0-indexed
            end = int(match.group(3))  # exclusive
            climax_positions.extend(range(start, min(end, len(tracks))))

    if climax_positions:
        return [tracks[i].track_id for i in climax_positions if 0 <= i < len(tracks)]

    # Fallback to metadata-based inference
    return _infer_climax_from_metadata(tracks)
