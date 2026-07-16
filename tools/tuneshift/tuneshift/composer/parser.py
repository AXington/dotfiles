"""Enhanced parser for composer narrative sections."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from tuneshift.composer.models import EnhancedSection, TransitionType
from tuneshift.sequencer.narrative_parser import _SECTION_PATTERN

_MOOD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fury": ("fury", "wrath", "rage", "anger", "storm", "unrelenting"),
    "defiant": ("defiant", "defiance", "claiming", "rebel", "resist", "fight"),
    "vulnerable": (
        "vulnerable",
        "introspection",
        "introspective",
        "gentle",
        "quiet realization",
        "tender",
    ),
    "triumphant": (
        "triumphant",
        "empowerment",
        "empower",
        "anthem",
        "victory",
        "self-possession",
    ),
    "peaceful": ("peaceful", "quiet", "calm", "drone", "still", "exhale", "gentle"),
}

_HIGH_INTENSITY_WORDS = frozenset(
    {
        "fury",
        "wrath",
        "rage",
        "anger",
        "unrelenting",
        "defiance",
        "defiant",
        "anthem",
        "triumphant",
        "empowerment",
        "sharp",
    }
)
_LOW_INTENSITY_WORDS = frozenset(
    {
        "gentle",
        "quiet",
        "drone",
        "collapse",
        "aftermath",
        "still",
        "peaceful",
        "vulnerable",
        "intro",
        "introspection",
    }
)

_REQUIRED_TRACK_RE = re.compile(r"required:\s*([^.;]+)", re.IGNORECASE)
_REQUIRED_ARTIST_RE = re.compile(r"required artist:\s*([^.;]+)", re.IGNORECASE)

# Matches text inside parentheses that looks like a track mention:
# at least 2 chars, not purely numeric, not a common non-track pattern
_PAREN_MENTION_RE = re.compile(r"\(([^)]{2,})\)")
_NON_TRACK_PARENS = frozenset(
    {"intro", "outro", "cont", "cont'd", "continued", "reprise"}
)

_MATCH_THRESHOLD = 0.72


def _fuzzy_match_track(mention: str, tracklist: list[str]) -> str | None:
    """Find the best fuzzy match for a mention against the tracklist.

    Returns the canonical track title if a match exceeds threshold, else None.
    """
    mention_lower = mention.casefold()
    best_ratio = 0.0
    best_title: str | None = None

    for title in tracklist:
        title_lower = title.casefold()

        # Exact match (case-insensitive)
        if mention_lower == title_lower:
            return title

        # Substring containment: only valid when the longer string contains
        # the shorter as a distinct segment (bounded by non-alphanumeric or
        # string boundary). Prevents "I Am Her" matching "I Am Here".
        if mention_lower in title_lower:
            end_idx = title_lower.index(mention_lower) + len(mention_lower)
            if end_idx >= len(title_lower) or not title_lower[end_idx].isalnum():
                return title
        elif title_lower in mention_lower:
            end_idx = mention_lower.index(title_lower) + len(title_lower)
            if end_idx >= len(mention_lower) or not mention_lower[end_idx].isalnum():
                return title

        ratio = SequenceMatcher(None, mention_lower, title_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_title = title

    if best_ratio >= _MATCH_THRESHOLD and best_title is not None:
        return best_title
    return None


def _extract_track_mentions(description: str, tracklist: list[str] | None) -> list[str]:
    """Extract track mentions from parenthetical references in prose.

    Scans for (Track Title) patterns and fuzzy-matches each against the
    known tracklist. Only returns validated matches to avoid false positives
    from non-track parentheticals like (Intro) or position ranges.
    """
    if not tracklist:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for match in _PAREN_MENTION_RE.finditer(description):
        candidate = match.group(1).strip()

        # Skip purely numeric, very short, or known non-track terms
        if candidate.isdigit():
            continue
        if candidate.casefold() in _NON_TRACK_PARENS:
            continue
        # Skip position range patterns like "11-18"
        if re.fullmatch(r"\d+-\d+", candidate):
            continue

        matched = _fuzzy_match_track(candidate, tracklist)
        if matched and matched not in seen:
            found.append(matched)
            seen.add(matched)

    return found


def _extract_prose_track_mentions(
    description: str, tracklist: list[str] | None, already_found: set[str]
) -> list[str]:
    """Extract bare track title mentions from prose (not in parentheses).

    Looks for track titles that appear at sentence boundaries: after a period,
    newline, or at the start of the description. Only matches tracks with
    2+ word titles or distinctive single-word titles to reduce false positives.
    """
    if not tracklist:
        return []

    # Normalize the description: collapse whitespace for easier matching
    text = " ".join(description.split())
    text_lower = text.casefold()

    found: list[str] = []

    for title in tracklist:
        if title in already_found:
            continue
        title_lower = title.casefold()

        # Skip very short titles (high false-positive risk in prose)
        if len(title_lower) < 4:
            continue

        # Check if the title appears in the text
        idx = text_lower.find(title_lower)
        if idx < 0:
            continue

        # Validate: title should be at a sentence/clause boundary
        # (start of text, after ". ", after "\n", or after ": ")
        if idx == 0:
            found.append(title)
            continue

        prefix = text[max(0, idx - 2) : idx]
        if prefix.endswith(". ") or prefix.endswith(".\n") or prefix.endswith(": "):
            found.append(title)
            continue

        # After a newline (section descriptions can have internal newlines)
        if text[idx - 1] == "\n":
            found.append(title)

    return found


def _extract_moods(description: str) -> list[str]:
    text = description.lower()
    moods: list[str] = []
    for mood, keywords in _MOOD_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            moods.append(mood)
    return moods


def _estimate_intensity(name: str, description: str) -> float:
    text = f"{name} {description}".lower()
    words = re.findall(r"[a-z']+", text)
    high_hits = sum(1 for word in words if word in _HIGH_INTENSITY_WORDS)
    low_hits = sum(1 for word in words if word in _LOW_INTENSITY_WORDS)
    intensity = 0.5 + (high_hits * 0.12) - (low_hits * 0.1)
    return max(0.1, min(1.0, intensity))


def _estimate_stance(name: str, description: str) -> str | None:
    text = f"{name} {description}".lower()
    if any(
        word in text for word in ("defiant", "defiance", "rebel", "fight", "claiming")
    ):
        return "defiant"
    if any(
        word in text
        for word in (
            "triumphant",
            "victory",
            "anthem",
            "empowerment",
            "self-possession",
        )
    ):
        return "triumphant"
    if any(
        word in text
        for word in ("vulnerable", "introspection", "gentle", "quiet realization")
    ):
        return "vulnerable"
    if any(word in text for word in ("peaceful", "still", "calm", "drone")):
        return "peaceful"
    return None


def _infer_transition_in(description: str) -> TransitionType:
    text = description.lower()
    if "collapse" in text or "aftermath" in text:
        return TransitionType.COLLAPSE
    if "sharp cut" in text:
        return TransitionType.SHARP_CUT
    if "sustain" in text or "holds" in text:
        return TransitionType.SUSTAIN
    return TransitionType.GRADUAL


def _infer_transition_out(description: str) -> TransitionType:
    text = description.lower()
    if "sharp cut" in text:
        return TransitionType.SHARP_CUT
    if (
        "builds" in text
        or "build back up" in text
        or "build up" in text
        or "rising tension" in text
    ):
        return TransitionType.BUILD
    if "sustain" in text or "holds" in text:
        return TransitionType.SUSTAIN
    return TransitionType.GRADUAL


def _section_concept(description: str) -> str | None:
    cleaned = _REQUIRED_TRACK_RE.sub("", description)
    cleaned = _REQUIRED_ARTIST_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or None


def parse_enhanced_narrative(
    narrative: str | None, tracklist: list[str] | None = None
) -> list[EnhancedSection]:
    """Parse structured narrative text into enhanced composer sections.

    Args:
        narrative: The narrative text with section headers like WRATH (11-18): ...
        tracklist: Optional list of known track titles. When provided, parenthetical
            mentions in the prose are fuzzy-matched against this list to populate
            required_tracks alongside any explicit Required: annotations.
    """
    if not narrative:
        return []

    sections: list[EnhancedSection] = []
    for match in _SECTION_PATTERN.finditer(narrative):
        name = match.group(1)
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        description = match.group(4).strip()

        # Explicit Required: annotations (always honored)
        explicit_tracks = [
            item.strip() for item in _REQUIRED_TRACK_RE.findall(description)
        ]

        # Parenthetical track mentions (fuzzy-matched against tracklist)
        prose_tracks = _extract_track_mentions(description, tracklist)

        # Merge both sources, deduplicating (explicit wins on overlap)
        seen = {t.casefold() for t in explicit_tracks}
        all_tracks = list(explicit_tracks)
        for t in prose_tracks:
            if t.casefold() not in seen:
                all_tracks.append(t)
                seen.add(t.casefold())

        # Bare title mentions at sentence boundaries (final pass)
        bare_tracks = _extract_prose_track_mentions(description, tracklist, seen)
        for t in bare_tracks:
            if t.casefold() not in seen:
                all_tracks.append(t)
                seen.add(t.casefold())

        sections.append(
            EnhancedSection(
                name=name,
                start_position=start,
                end_position=end,
                description=description,
                implied_intensity=_estimate_intensity(name, description),
                implied_stance=_estimate_stance(name, description),
                capacity=end - start + 1,
                mood=_extract_moods(description),
                transition_in=_infer_transition_in(description),
                transition_out=_infer_transition_out(description),
                required_tracks=all_tracks,
                required_artists=[
                    item.strip() for item in _REQUIRED_ARTIST_RE.findall(description)
                ],
                section_concept=_section_concept(description),
            )
        )

    return sections
