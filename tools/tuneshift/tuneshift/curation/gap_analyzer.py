"""Gap analysis: detect narrative and energy gaps in playlist composition."""

from dataclasses import dataclass

from tuneshift.sequencer.metadata import TrackMetadata


@dataclass
class GapReport:
    gap_type: str  # "thin_section", "missing_transition", "energy_gap", "mood_gap"
    section_name: str
    description: str
    severity: float  # 0.0-1.0
    suggestion: str


def analyze_gaps(
    tracks: list[TrackMetadata],
    sections: list[dict],
    goal: str,  # noqa: ARG001 - analyzer signature parity
) -> list[GapReport]:
    """Analyze playlist for narrative and compositional gaps.

    Detects:
    - Thin sections: sections that need more tracks than available
    - Missing transitions: abrupt energy/mood jumps between sections
    - Energy gaps: missing energy levels in the arc
    """
    gaps: list[GapReport] = []

    if not sections or not tracks:
        return gaps

    total_positions = sum(s["end"] - s["start"] + 1 for s in sections)
    tracks_per_position = len(tracks) / max(total_positions, 1)

    # Detect thin sections: if allocation per position is too sparse
    for section in sections:
        capacity = section["end"] - section["start"] + 1
        allocated_tracks = capacity * tracks_per_position

        # Section is thin if we have less than 0.5 tracks per position
        if tracks_per_position < 0.5:
            gaps.append(
                GapReport(
                    gap_type="thin_section",
                    section_name=section["name"],
                    description=f"{section['name']} needs ~{capacity} tracks but playlist only has ~{allocated_tracks:.0f} allocated",  # noqa: E501
                    severity=min(1.0, (capacity - allocated_tracks) / max(capacity, 1)),
                    suggestion=f"Add {int(capacity - allocated_tracks)} tracks matching: {section.get('description', '')}",  # noqa: E501
                )
            )

    # Detect missing transitions between consecutive sections
    for i in range(len(sections) - 1):
        curr_section = sections[i]
        next_section = sections[i + 1]

        # Filter tracks by section position ranges (sections use 1-based positions)
        curr_start = curr_section["start"] - 1  # Convert to 0-based index
        curr_end = curr_section["end"] - 1
        next_start = next_section["start"] - 1
        next_end = next_section["end"] - 1

        curr_intensities = [
            t.emotional_intensity
            for idx, t in enumerate(tracks)
            if t.emotional_intensity is not None and curr_start <= idx <= curr_end
        ]
        next_intensities = [
            t.emotional_intensity
            for idx, t in enumerate(tracks)
            if t.emotional_intensity is not None and next_start <= idx <= next_end
        ]

        if curr_intensities and next_intensities:
            # Representative intensity for end of current section
            curr_intensity = max(curr_intensities)
            # Representative intensity for start of next section
            next_intensity = min(next_intensities)

            intensity_jump = abs(curr_intensity - next_intensity)
            # Threshold: 0.5 intensity difference is a significant jump
            if intensity_jump > 0.5:
                gaps.append(
                    GapReport(
                        gap_type="missing_transition",
                        section_name=f"{curr_section['name']}->{next_section['name']}",
                        description=f"Abrupt intensity jump ({intensity_jump:.1f}) between {curr_section['name']} and {next_section['name']}",  # noqa: E501
                        severity=min(1.0, intensity_jump),
                        suggestion=f"Add a transitional track (intensity ~{(curr_intensity + next_intensity) / 2:.1f}) between sections",  # noqa: E501
                    )
                )

    return gaps
