"""Dedicated narrative parser producing structured NarrativeSection objects."""

import re
from dataclasses import dataclass


@dataclass
class NarrativeSection:
    name: str
    start_position: int
    end_position: int
    description: str
    implied_intensity: float
    implied_stance: str | None
    capacity: int


# Keywords that suggest high emotional intensity
_HIGH_INTENSITY_WORDS = frozenset(
    {
        "fury",
        "rage",
        "wrath",
        "anger",
        "fire",
        "defiance",
        "defiant",
        "fight",
        "scream",
        "storm",
        "chaos",
        "menace",
        "annihilation",
    }
)

# Keywords that suggest low intensity
_LOW_INTENSITY_WORDS = frozenset(
    {
        "exhale",
        "drone",
        "collapse",
        "quiet",
        "gentle",
        "calm",
        "rest",
        "still",
        "fade",
        "whisper",
        "lull",
    }
)

# Section name pattern: NAME (N) or NAME (N-M)
# Captures everything from the colon to the next section header or end of text.
# Uses DOTALL via inline flag for the body group, and MULTILINE for ^ anchoring.
_SECTION_PATTERN = re.compile(
    r"^([A-Z][A-Z_]*)\s*\((\d+)(?:-(\d+))?\)\s*:\s*(.*?)(?=\n[A-Z][A-Z_]*\s*\(\d|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _estimate_intensity(name: str, description: str) -> float:
    """Estimate emotional intensity from section name and description."""
    text = f"{name} {description}".lower()
    words = set(text.split())

    high_hits = len(words & _HIGH_INTENSITY_WORDS)
    low_hits = len(words & _LOW_INTENSITY_WORDS)

    if high_hits > 0 and low_hits == 0:
        return min(0.7 + (high_hits * 0.1), 1.0)
    if low_hits > 0 and high_hits == 0:
        return max(0.3 - (low_hits * 0.05), 0.1)

    # Default: moderate intensity
    return 0.5


def _estimate_stance(name: str, description: str) -> str | None:
    """Estimate narrator stance from description keywords."""
    text = f"{name} {description}".lower()
    if any(w in text for w in ("defiant", "fury", "fight", "rebel", "refuse")):
        return "defiant"
    if any(w in text for w in ("vulnerable", "plea", "quiet", "gentle")):
        return "vulnerable"
    if any(w in text for w in ("triumph", "victory", "fist", "anthem", "empowerment")):
        return "triumphant"
    return None


def parse_narrative(narrative: str | None) -> list[NarrativeSection]:
    """Parse a structured narrative string into NarrativeSection objects.

    Expected format per line: SECTION_NAME (start-end): Description text.
    Single positions: SECTION_NAME (N): Description.
    """
    if not narrative:
        return []

    sections: list[NarrativeSection] = []
    for match in _SECTION_PATTERN.finditer(narrative):
        name = match.group(1)
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        description = match.group(4).strip()

        sections.append(
            NarrativeSection(
                name=name,
                start_position=start,
                end_position=end,
                description=description,
                implied_intensity=_estimate_intensity(name, description),
                implied_stance=_estimate_stance(name, description),
                capacity=end - start + 1,
            )
        )

    return sections
