"""Transition-aware sequencing for composed narrative sections."""

from __future__ import annotations

from tuneshift.composer.models import (
    EnhancedSection,
    SectionAssignments,
    TransitionType,
)
from tuneshift.models import PlaylistPin
from tuneshift.sequencer.metadata import TrackMetadata
from tuneshift.sequencer.scoring import resolve_weights, score_pair


def sequence_sections(
    assignments: SectionAssignments,
    sections: list[EnhancedSection],
    weights: dict[str, float] | None = None,
    pins: list[PlaylistPin] | None = None,
) -> list[TrackMetadata]:
    """Sequence tracks within sections, then optimize section boundaries."""
    resolved_weights = resolve_weights(weights, None, None)
    ordered_by_section: dict[str, list[TrackMetadata]] = {}

    for section in sections:
        tracks = list(assignments.assignments.get(section.name, []))
        ordered_by_section[section.name] = _order_within_section(
            tracks,
            section,
            resolved_weights,
        )

    for index in range(len(sections) - 1):
        current = sections[index]
        nxt = sections[index + 1]
        current_tracks = ordered_by_section[current.name]
        next_tracks = ordered_by_section[nxt.name]
        if not current_tracks or not next_tracks:
            continue

        boundary_style = _resolve_transition_style(current, nxt)
        current_tracks = _select_closer(current_tracks, boundary_style)
        ordered_by_section[current.name] = current_tracks

        next_tracks = _select_opener(
            next_tracks,
            boundary_style,
            current_tracks[-1],
            resolved_weights,
        )
        ordered_by_section[nxt.name] = next_tracks

    ordered: list[TrackMetadata] = []
    for section in sections:
        ordered.extend(ordered_by_section[section.name])

    ordered.extend(assignments.unassigned)

    if pins:
        ordered = _apply_pins(ordered, pins)

    return ordered


def _order_within_section(
    tracks: list[TrackMetadata],
    section: EnhancedSection,
    weights: dict[str, float],
) -> list[TrackMetadata]:
    if len(tracks) < 2:
        return tracks

    if section.transition_out is TransitionType.BUILD:
        return sorted(tracks, key=_energy_value)

    if section.transition_out is TransitionType.COLLAPSE:
        return sorted(tracks, key=_energy_value, reverse=True)

    return _greedy_order(tracks, weights)


def _greedy_order(
    tracks: list[TrackMetadata],
    weights: dict[str, float],
) -> list[TrackMetadata]:
    if len(tracks) < 2:
        return tracks

    remaining = list(tracks)
    start = max(
        remaining,
        key=lambda track: _average_neighbor_score(track, remaining, weights),
    )
    ordered = [start]
    remaining.remove(start)

    while remaining:
        current = ordered[-1]
        next_track = max(
            remaining,
            key=lambda candidate: score_pair(current, candidate, weights),
        )
        ordered.append(next_track)
        remaining.remove(next_track)

    return ordered


def _average_neighbor_score(
    track: TrackMetadata,
    candidates: list[TrackMetadata],
    weights: dict[str, float],
) -> float:
    scores = [
        score_pair(track, candidate, weights)
        for candidate in candidates
        if candidate.track_id != track.track_id
    ]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _select_closer(
    tracks: list[TrackMetadata],
    transition_type: TransitionType,
) -> list[TrackMetadata]:
    if len(tracks) < 2:
        return tracks

    closer_index = len(tracks) - 1
    if transition_type in {TransitionType.SHARP_CUT, TransitionType.BUILD}:
        closer_index = max(
            range(len(tracks)), key=lambda index: _energy_value(tracks[index])
        )
    elif transition_type is TransitionType.COLLAPSE:
        closer_index = max(
            range(len(tracks)),
            key=lambda index: _collapse_readiness(tracks[index]),
        )

    return _move_track(tracks, closer_index, len(tracks) - 1)


def _select_opener(
    tracks: list[TrackMetadata],
    transition_type: TransitionType,
    previous_closer: TrackMetadata,
    weights: dict[str, float],
) -> list[TrackMetadata]:
    if len(tracks) < 2:
        return tracks

    opener_index = 0
    if transition_type is TransitionType.SHARP_CUT:
        opener_index = max(
            range(len(tracks)),
            key=lambda index: _contrast_score(previous_closer, tracks[index], weights),
        )
    elif transition_type is TransitionType.COLLAPSE:
        opener_index = min(
            range(len(tracks)), key=lambda index: _energy_value(tracks[index])
        )
    else:
        opener_index = min(
            range(len(tracks)),
            key=lambda index: _contrast_score(previous_closer, tracks[index], weights),
        )

    return _move_track(tracks, opener_index, 0)


def _resolve_transition_style(
    current: EnhancedSection,
    nxt: EnhancedSection,
) -> TransitionType:
    if current.transition_out is not TransitionType.GRADUAL:
        return current.transition_out
    return nxt.transition_in


def _move_track(
    tracks: list[TrackMetadata],
    from_index: int,
    to_index: int,
) -> list[TrackMetadata]:
    updated = list(tracks)
    track = updated.pop(from_index)
    updated.insert(to_index, track)
    return updated


def _contrast_score(
    a: TrackMetadata,
    b: TrackMetadata,
    weights: dict[str, float],
) -> float:
    if a.energy is not None and b.energy is not None:
        return abs(a.energy - b.energy)
    return 1.0 - score_pair(a, b, weights)


def _collapse_readiness(track: TrackMetadata) -> float:
    fade_bonus = 0.35 if _is_fading(track) else 0.0
    return (1.0 - _energy_value(track)) + fade_bonus


def _is_fading(track: TrackMetadata) -> bool:
    markers = ("fade", "fading", "descending", "falling", "decay")
    arc = (track.energy_arc_within or "").lower()
    closing = (track.closes_with or "").lower()
    return any(marker in arc or marker in closing for marker in markers)


def _energy_value(track: TrackMetadata) -> float:
    if track.energy is not None:
        return max(0.0, min(1.0, track.energy))
    if track.emotional_intensity is not None:
        return max(0.0, min(1.0, track.emotional_intensity))
    return 0.5


def _apply_pins(
    ordered: list[TrackMetadata], pins: list[PlaylistPin]
) -> list[TrackMetadata]:
    """Apply opener/closer/position/adjacent pins to the final ordered list.

    Pins override the composer's energy-based ordering to enforce user-defined
    constraints. Processing order: position pins first, then opener, closer,
    and finally adjacent groups.
    """
    track_by_id = {t.track_id: t for t in ordered}
    result = list(ordered)

    # Resolve pin groups
    opener_ids: list[int] = []
    closer_ids: list[int] = []
    position_pins: list[tuple[int, int]] = []  # (target_index, track_id)
    adjacent_groups: dict[
        str, list[tuple[int, int]]
    ] = {}  # group_id -> [(order, track_id)]

    for pin in pins:
        if pin.track_id not in track_by_id:
            continue
        if pin.pin_type == "opener":
            opener_ids.append(pin.track_id)
        elif pin.pin_type == "closer":
            closer_ids.append(pin.track_id)
        elif pin.pin_type == "position" and pin.group_order is not None:
            position_pins.append((pin.group_order, pin.track_id))
        elif pin.pin_type == "anchor" and pin.group_id is not None:
            adjacent_groups.setdefault(pin.group_id, []).append(
                (pin.group_order or 0, pin.track_id)
            )

    # Apply adjacent groups: keep group members together in their specified order.
    # Find the earliest current position of any group member and place the group there.
    for group_id, members in adjacent_groups.items():
        members.sort(key=lambda pair: pair[0])
        member_ids = {tid for _, tid in members}
        member_tracks = [track_by_id[tid] for _, tid in members if tid in track_by_id]
        if not member_tracks:
            continue

        # Find earliest position of any group member
        positions = [i for i, t in enumerate(result) if t.track_id in member_ids]
        if not positions:
            continue
        insert_at = min(positions)

        # Remove all group members from result
        result = [t for t in result if t.track_id not in member_ids]
        # Insert in group order at the earliest position
        for offset, track in enumerate(member_tracks):
            result.insert(insert_at + offset, track)

    # Apply position pins (absolute 0-based index)
    for target_index, track_id in sorted(position_pins):
        track = track_by_id.get(track_id)
        if track is None:
            continue
        result = [t for t in result if t.track_id != track_id]
        clamped = min(target_index, len(result))
        result.insert(clamped, track)

    # Apply opener (move to position 0)
    for track_id in opener_ids:
        track = track_by_id.get(track_id)
        if track is None:
            continue
        result = [t for t in result if t.track_id != track_id]
        result.insert(0, track)

    # Apply closer (move to last position)
    for track_id in closer_ids:
        track = track_by_id.get(track_id)
        if track is None:
            continue
        result = [t for t in result if t.track_id != track_id]
        result.append(track)

    # Re-apply adjacent groups that include opener/closer to preserve group order
    # (opener pin may have pulled a group member to position 0 while the group
    # wants them together)
    for group_id, members in adjacent_groups.items():
        members.sort(key=lambda pair: pair[0])
        member_ids = [tid for _, tid in members]
        # Check if opener is in this group
        group_has_opener = any(tid in opener_ids for tid in member_ids)
        if group_has_opener:
            member_tracks = [
                track_by_id[tid] for tid in member_ids if tid in track_by_id
            ]
            result = [t for t in result if t.track_id not in set(member_ids)]
            for offset, track in enumerate(member_tracks):
                result.insert(offset, track)

    return result
