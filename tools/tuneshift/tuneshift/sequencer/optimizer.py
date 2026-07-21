"""Greedy nearest-neighbor plus 2-opt sequence optimizer."""

import math
import random
from typing import TYPE_CHECKING

from tuneshift.db import Database
from tuneshift.sequencer.metadata import TrackMetadata, get_track_metadata_map
from tuneshift.sequencer.modifiers import SequenceContext, score_candidate
from tuneshift.sequencer.narrative_parser import NarrativeSection
from tuneshift.sequencer.profiles import get_profile
from tuneshift.sequencer.scoring import score_pair

if TYPE_CHECKING:
    from tuneshift.sequencer.intent import PlaylistIntent


def _target_energy(position_frac: float, arc: str) -> float | None:
    """Target energy at a given fractional position for an arc shape."""
    if arc == "free":
        return None
    if arc == "wave":
        return 0.5 + 0.3 * math.sin(2 * math.pi * position_frac)
    if arc == "narrative":
        if position_frac < 0.2:
            return 0.3 + 2.0 * position_frac
        if position_frac < 0.6:
            return 0.7
        if position_frac < 0.7:
            return 0.7 - 2.0 * (position_frac - 0.6)
        if position_frac < 0.9:
            return 0.5 + 2.5 * (position_frac - 0.7)
        return 1.0 - 4.0 * (position_frac - 0.9)
    if arc == "descending":
        return 0.8 - 0.6 * position_frac
    if arc == "ascending":
        return 0.2 + 0.6 * position_frac
    return None


def _arc_fit_multiplier(
    track: TrackMetadata,
    position: int,
    total: int,
    arc: str,
) -> float:
    """Multiplier based on how well track energy fits the arc position."""
    if arc == "free" or total <= 1:
        return 1.0
    target = _target_energy(position / max(total - 1, 1), arc)
    if target is None or track.energy is None:
        return 1.0
    return 1.0 - 0.3 * abs(track.energy - target)


def select_opener(tracks: list[TrackMetadata], arc: str) -> TrackMetadata:
    """Select the best opening track for the given arc shape."""
    target = _target_energy(0.0, arc)
    if target is None:
        target = 0.5

    scored: list[tuple[float, TrackMetadata]] = []
    for track in tracks:
        energy = track.energy if track.energy is not None else 0.5
        energy_fit = 1.0 - abs(energy - target)
        valence = track.valence if track.valence is not None else 0.5
        valence_fit = 1.0 - abs(valence - 0.5) * 0.5
        scored.append((energy_fit * 0.7 + valence_fit * 0.3, track))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def select_closer(tracks: list[TrackMetadata], arc: str) -> TrackMetadata:
    """Select the best closing track for the given arc shape."""
    target = _target_energy(1.0, arc)
    if target is None:
        target = 0.3

    scored: list[tuple[float, TrackMetadata]] = []
    for track in tracks:
        energy = track.energy if track.energy is not None else 0.5
        energy_fit = 1.0 - abs(energy - target)
        mode_bonus = 0.2 if track.mode == 1 else 0.0
        scored.append((energy_fit + mode_bonus, track))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def distribute_artists(
    sequence: list[TrackMetadata],
    min_separation: int = 4,
    protected: set[int] | None = None,
) -> list[TrackMetadata]:
    """Distribute same-artist tracks more evenly across the sequence."""
    result = list(sequence)
    track_count = len(result)
    if track_count <= 3:
        return result
    protected = protected or set()

    def _has_adjacency_violation(seq: list[TrackMetadata], idx: int) -> bool:
        if idx > 0 and seq[idx].artist == seq[idx - 1].artist:
            return True
        if idx < len(seq) - 1 and seq[idx].artist == seq[idx + 1].artist:
            return True
        return False

    max_passes = max(50, min_separation * 10)
    for _ in range(max_passes):
        violation_idx = None
        for index in range(track_count - 1):
            if result[index].artist == result[index + 1].artist:
                violation_idx = index + 1
                break

        if violation_idx is None:
            break

        # Skip if the violator is pinned
        if violation_idx in protected:
            break

        violator = result[violation_idx]
        best_target = None
        best_distance = -1
        artist_positions = [
            index
            for index in range(track_count)
            if result[index].artist == violator.artist
        ]

        for target_idx in range(track_count):
            if target_idx == violation_idx:
                continue
            if target_idx in protected:
                continue
            target_track = result[target_idx]
            if target_track.artist == violator.artist:
                continue

            result[violation_idx], result[target_idx] = (
                result[target_idx],
                result[violation_idx],
            )
            creates_violation = _has_adjacency_violation(
                result,
                violation_idx,
            ) or _has_adjacency_violation(result, target_idx)
            result[violation_idx], result[target_idx] = (
                result[target_idx],
                result[violation_idx],
            )

            if creates_violation:
                continue

            if len(artist_positions) > 1:
                min_dist = min(
                    abs(target_idx - position)
                    for position in artist_positions
                    if position != violation_idx
                )
            else:
                min_dist = track_count

            if min_dist > best_distance:
                best_distance = min_dist
                best_target = target_idx

        if best_target is not None:
            result[violation_idx], result[best_target] = (
                result[best_target],
                result[violation_idx],
            )
            continue

        for target_idx in range(track_count - 1, -1, -1):
            if target_idx == violation_idx:
                continue
            if target_idx in protected:
                continue
            if result[target_idx].artist == violator.artist:
                continue
            result[violation_idx], result[target_idx] = (
                result[target_idx],
                result[violation_idx],
            )
            break
        else:
            break

    return result


break_artist_runs = distribute_artists


def _place_moments(
    tracks: list,  # noqa: ARG001 - signature parity with placement helpers
    moments: list[int],
    total: int,
) -> dict[int, int]:
    """Assign moment tracks to positions in the climax region (55-75%).

    Returns dict of target_position -> track_id.
    """
    if not moments:
        return {}
    climax_start = int(total * 0.55)
    climax_end = int(total * 0.75)
    available_positions = list(range(climax_start, min(climax_end + 1, total - 1)))
    if not available_positions:
        return {}
    step = max(1, len(available_positions) // (len(moments) + 1))
    result: dict[int, int] = {}
    for i, track_id in enumerate(moments):
        pos = climax_start + (i + 1) * step
        pos = min(pos, climax_end, total - 2)
        result[pos] = track_id
    return result


def _validate_pin_conflicts(
    pins: list | None,
    track_map: dict[int, TrackMetadata],
) -> None:
    """Reject a single track carrying two contradictory placement pins (SEQ-C3).

    A track may have at most one placement pin among opener/closer/position/
    anchor. Moment pins are soft climax targets resolved by precedence, so they
    are excluded here. Fails loudly rather than silently emitting a duplicate.
    """
    if not pins:
        return

    placements: dict[int, list[str]] = {}
    for pin in pins:
        track_id = pin.track_id
        if track_id not in track_map or pin.pin_type == "moment":
            continue
        if pin.pin_type == "opener":
            placements.setdefault(track_id, []).append("opener")
        elif pin.pin_type == "closer":
            placements.setdefault(track_id, []).append("closer")
        elif pin.pin_type == "position" and pin.group_order is not None:
            placements.setdefault(track_id, []).append(
                f"position (index {pin.group_order})"
            )
        elif pin.pin_type == "anchor" and pin.group_id:
            placements.setdefault(track_id, []).append(
                f"anchor (group '{pin.group_id}')"
            )

    for track_id, descriptors in placements.items():
        if len(descriptors) >= 2:
            title = track_map[track_id].title
            raise ValueError(
                f"Conflicting pins for track {track_id} ('{title}'): "
                f"{descriptors[0]} and {descriptors[1]}. A track may have only one "
                "placement pin (opener, closer, position, or anchor). "
                "Remove one pin and retry."
            )


def _resolve_pins(
    pins: list | None,
    track_map: dict[int, TrackMetadata],
) -> tuple[int | None, int | None, dict[str, list[int]], dict[int, int]]:
    """Parse pin list into opener_id, closer_id, adjacency groups, and position pins.

    Returns (pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins)
    where position_pins maps target_index -> track_id.
    """
    pinned_opener_id: int | None = None
    pinned_closer_id: int | None = None
    adjacency_groups: dict[str, list[int]] = {}
    position_pins: dict[int, int] = {}

    if not pins:
        return pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins

    _validate_pin_conflicts(pins, track_map)

    for pin in pins:
        if pin.track_id not in track_map:
            continue
        if pin.pin_type == "opener":
            pinned_opener_id = pin.track_id
        elif pin.pin_type == "closer":
            pinned_closer_id = pin.track_id
        elif pin.pin_type == "anchor" and pin.group_id:
            if pin.group_id not in adjacency_groups:
                adjacency_groups[pin.group_id] = []
            adjacency_groups[pin.group_id].append((pin.group_order or 0, pin.track_id))
        elif pin.pin_type == "position" and pin.group_order is not None:
            position_pins[pin.group_order] = pin.track_id
        # Moment pins are handled separately in optimize_sequence

    # Sort adjacency groups by group_order
    for group_id in adjacency_groups:
        adjacency_groups[group_id] = [
            tid for _, tid in sorted(adjacency_groups[group_id])
        ]

    return pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins


def _order_small_playlist(
    tracks: list[TrackMetadata],
    track_map: dict[int, TrackMetadata],
    pinned_opener_id: int | None,
    pinned_closer_id: int | None,
    adjacency_groups: dict[str, list[int]],
    position_pins: dict[int, int],
) -> list[TrackMetadata]:
    """Order a playlist of <=2 tracks while honoring pins.

    A single track (or empty list) is returned unchanged. For two tracks the
    resolution mirrors the >=3 path's precedence:

    * position pins at the first/last index override opener/closer pins,
    * an explicit opener/closer pin fixes that endpoint,
    * otherwise a 2-track adjacency group's ``group_order`` sets the order,
    * with no constraints, the input order is preserved.

    Moment pins are a deliberate no-op at this size: ``_place_moments`` targets
    the 55-75% climax region, which is empty for <=2 tracks. Conflicting pins
    are never fatal.
    """
    track_count = len(tracks)
    if track_count <= 1:
        return list(tracks)

    # Position pins at the endpoints override opener/closer, matching the
    # precedence applied in the >=3 path.
    if 0 in position_pins:
        pinned_opener_id = position_pins.pop(0)
    if (track_count - 1) in position_pins:
        pinned_closer_id = position_pins.pop(track_count - 1)

    # The same track pinned to both ends is impossible via the DB schema
    # (UNIQUE + INSERT OR REPLACE); if it somehow occurs, keep it as the
    # opener rather than raising.
    if pinned_closer_id is not None and pinned_closer_id == pinned_opener_id:
        pinned_closer_id = None

    ids = [track.track_id for track in tracks]

    # An endpoint pin fixes one slot; the other track takes the remaining one.
    # Endpoint pins take precedence over adjacency here because a 2-track
    # playlist has no interior region for an adjacency block to occupy.
    if pinned_opener_id in track_map:
        second = next(tid for tid in ids if tid != pinned_opener_id)
        return [track_map[pinned_opener_id], track_map[second]]
    if pinned_closer_id in track_map:
        first = next(tid for tid in ids if tid != pinned_closer_id)
        return [track_map[first], track_map[pinned_closer_id]]

    # No endpoint pins: honor a 2-track adjacency group's ordering.
    for group in adjacency_groups.values():
        group_ids = [tid for tid in group if tid in track_map]
        if len(group_ids) == 2 and set(group_ids) == set(ids):
            return [track_map[group_ids[0]], track_map[group_ids[1]]]

    # Nothing constrains the order: preserve the input order.
    return list(tracks)


def _select_endpoints(
    tracks: list[TrackMetadata],
    track_map: dict[int, TrackMetadata],
    pinned_opener_id: int | None,
    pinned_closer_id: int | None,
    arc: str,
    exclude_from_auto: set[int] | None = None,
) -> tuple[TrackMetadata, TrackMetadata, list[TrackMetadata]]:
    """Choose opener and closer, return (opener, closer, remaining).

    exclude_from_auto: track IDs that should not be auto-selected as
    opener/closer (e.g., position-pinned tracks that belong elsewhere).
    """
    excluded = exclude_from_auto or set()

    if pinned_opener_id and pinned_opener_id in track_map:
        opener = track_map[pinned_opener_id]
    else:
        candidates = [t for t in tracks if t.track_id not in excluded]
        opener = select_opener(candidates or tracks, arc)

    remaining = [t for t in tracks if t.track_id != opener.track_id]

    if pinned_closer_id and pinned_closer_id in track_map:
        closer = track_map[pinned_closer_id]
    else:
        candidates = [t for t in remaining if t.track_id not in excluded]
        closer = select_closer(candidates or remaining, arc)

    remaining = [t for t in remaining if t.track_id != closer.track_id]
    return opener, closer, remaining


def _prepare_free_pool(
    remaining: list[TrackMetadata],
    track_map: dict[int, TrackMetadata],
    adjacency_groups: dict[str, list[int]],
    opener: TrackMetadata,
    closer: TrackMetadata,
) -> tuple[list[TrackMetadata], list[list[TrackMetadata]]]:
    """Separate free tracks from adjacency blocks.

    Returns (free_tracks, anchor_blocks).
    """
    anchored_ids: set[int] = set()
    for group_track_ids in adjacency_groups.values():
        for tid in group_track_ids:
            if tid != opener.track_id and tid != closer.track_id:
                anchored_ids.add(tid)

    free_tracks = [t for t in remaining if t.track_id not in anchored_ids]

    anchor_blocks: list[list[TrackMetadata]] = []
    for group_track_ids in adjacency_groups.values():
        block = [
            track_map[tid]
            for tid in group_track_ids
            if tid in track_map and tid != opener.track_id and tid != closer.track_id
        ]
        if block:
            anchor_blocks.append(block)

    return free_tracks, anchor_blocks


def _greedy_build(
    opener: TrackMetadata,
    closer: TrackMetadata,
    free_tracks: list[TrackMetadata],
    anchor_blocks: list[list[TrackMetadata]],
    track_count: int,
    weights: dict[str, float],
    arc: str,
    bold_jump_chance: float,
    narrative_mode: str,
    context_window: int,
    penalty_overrides: dict[str, float] | None,
    rng: random.Random,
    intent: "PlaylistIntent | None" = None,
) -> list[TrackMetadata]:
    """Build sequence using greedy nearest-neighbor with bold jumps and block insertion."""  # noqa: E501
    context = SequenceContext(
        position=0,
        total=track_count,
        narrative_mode=narrative_mode,
        context_window=context_window,
    )

    sequence = [opener]
    context.advance(opener)

    available = {track.track_id for track in free_tracks}
    free_map = {track.track_id: track for track in free_tracks}

    # Mix anchor blocks into free selection by treating each block's lead track
    # as a candidate; when selected, the whole block is inserted
    block_leads: dict[int, list[TrackMetadata]] = {}
    for block in anchor_blocks:
        lead = block[0]
        block_leads[lead.track_id] = block
        available.add(lead.track_id)
        free_map[lead.track_id] = lead

    bold_jump_cooldown = 0

    for position in range(1, track_count - 1):
        if not available:
            break

        current = sequence[-1]
        candidates: list[tuple[float, TrackMetadata]] = []

        for track_id in available:
            candidate = free_map[track_id]
            base = score_pair(current, candidate, weights)
            arc_mult = _arc_fit_multiplier(candidate, position, track_count, arc)
            adjusted = score_candidate(
                candidate,
                current,
                context,
                base * arc_mult,
                penalty_overrides,
                intent,
            )
            candidates.append((adjusted, candidate))

        candidates.sort(key=lambda item: item[0], reverse=True)

        bold_jump_cooldown = max(0, bold_jump_cooldown - 1)
        protect_region = position <= 2 or position >= track_count - 3
        use_bold_jumps = arc != "narrative" or intent is None
        if (
            use_bold_jumps
            and not protect_region
            and bold_jump_cooldown == 0
            and rng.random() < bold_jump_chance
            and len(candidates) > 3
        ):
            bottom_start = max(1, int(len(candidates) * 0.7))
            chosen = rng.choice(candidates[bottom_start:])[1]
            bold_jump_cooldown = 10
        else:
            chosen = candidates[0][1]

        # If chosen is a block lead, insert the whole block
        if chosen.track_id in block_leads:
            block = block_leads[chosen.track_id]
            for block_track in block:
                sequence.append(block_track)
                context.advance(block_track)
            available.remove(chosen.track_id)
            del block_leads[chosen.track_id]
        else:
            sequence.append(chosen)
            context.advance(chosen)
            available.remove(chosen.track_id)

    sequence.append(closer)
    return sequence


def sequence_score(
    sequence: list[TrackMetadata],
    weights: dict[str, float],
    arc: str,
    *,
    penalty_overrides: dict[str, float] | None = None,
    intent: "PlaylistIntent | None" = None,
    narrative_mode: str = "river",
    context_window: int = 5,
) -> float:
    """Single global objective for a full sequence (SEQ-A1).

    Sums, over every adjacent transition, the same arc-fit x context-modified
    pairwise score that ``_greedy_build`` optimizes position-by-position. The
    greedy builder and the local search (``_two_opt``) both optimize this one
    function, so local search can no longer improve raw pairwise continuity at
    the expense of arc fit or context (artist spacing, variety, monotony, ...).

    Higher is better. Returns 0.0 for sequences shorter than two tracks.
    """
    total_tracks = len(sequence)
    if total_tracks < 2:
        return 0.0

    context = SequenceContext(
        position=0,
        total=total_tracks,
        narrative_mode=narrative_mode,
        context_window=context_window,
    )
    context.advance(sequence[0])

    total = 0.0
    for position in range(1, total_tracks):
        current = sequence[position - 1]
        candidate = sequence[position]
        base = score_pair(current, candidate, weights)
        arc_mult = _arc_fit_multiplier(candidate, position, total_tracks, arc)
        total += score_candidate(
            candidate,
            current,
            context,
            base * arc_mult,
            penalty_overrides,
            intent,
        )
        context.advance(candidate)

    return total


def optimize_sequence(
    tracks: list[TrackMetadata],
    weights: dict[str, float],
    arc: str = "wave",
    artist_min_separation: int = 4,
    bold_jump_chance: float = 0.10,
    narrative_mode: str = "river",
    context_window: int = 5,
    penalty_overrides: dict[str, float] | None = None,
    pins: list | None = None,
    narrative: str | None = None,
    seed: int | None = None,
) -> list[TrackMetadata]:
    """Produce an optimized track sequence respecting pinned positions.

    ``seed`` makes the bold-jump exploration reproducible. When ``None`` a
    fixed default (0) is used so output is deterministic by default; callers
    that want per-playlist variation pass a stable playlist-derived seed.
    """
    track_count = len(tracks)
    track_map = {track.track_id: track for track in tracks}

    pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins = _resolve_pins(
        pins, track_map
    )

    # Playlists of <=2 tracks have no interior region to optimize, but pins
    # must still be honored (opener/closer/position/adjacency). The greedy /
    # 2-opt / distribute machinery below assumes >=3 tracks, so resolve the
    # small case directly.
    if track_count <= 2:
        return _order_small_playlist(
            tracks,
            track_map,
            pinned_opener_id,
            pinned_closer_id,
            adjacency_groups,
            position_pins,
        )

    # Narrative section-based sequencing: hard-place tracks into declared sections
    if arc == "narrative" and narrative:
        from tuneshift.sequencer.narrative_parser import parse_narrative

        sections = parse_narrative(narrative)
        if sections:
            has_pins = bool(
                pinned_opener_id is not None
                or pinned_closer_id is not None
                or adjacency_groups
                or position_pins
            )

            def _finish_narrative(ordered: list[TrackMetadata]) -> list[TrackMetadata]:
                # Fold explicit pins into the narrative arc (SEQ-C2) so they are
                # honored instead of silently dropped.
                if has_pins:
                    return _reorder_with_pins(
                        ordered,
                        pinned_opener_id,
                        pinned_closer_id,
                        adjacency_groups,
                        position_pins,
                    )
                return ordered

            total_capacity = sum(s.capacity for s in sections)
            # If sections cover all positions, use positional assignment
            # (the narrative describes the intended order by position)
            if total_capacity >= len(tracks):
                ordered: list[TrackMetadata] = []
                idx = 0
                for section in sections:
                    section_tracks = tracks[idx : idx + section.capacity]
                    if len(section_tracks) > 1:
                        section_ordered = _optimize_within_section(
                            section_tracks, weights, arc
                        )
                        ordered.extend(section_ordered)
                    else:
                        ordered.extend(section_tracks)
                    idx += section.capacity
                # Any remaining tracks (if sections didn't cover all)
                ordered.extend(tracks[idx:])
                return _finish_narrative(ordered)
            else:
                # Partial coverage: use fitness-based assignment
                assignments = assign_tracks_to_sections(tracks, sections, goal=arc)
                ordered = []
                for section in sections:
                    section_tracks = assignments.get(section.name, [])
                    if len(section_tracks) > 1:
                        section_ordered = _optimize_within_section(
                            section_tracks, weights, arc
                        )
                        ordered.extend(section_ordered)
                    else:
                        ordered.extend(section_tracks)
                ordered.extend(assignments.get("_flex", []))
                return _finish_narrative(ordered)

    # Infer intent early for narrative arc
    from tuneshift.sequencer.intent import infer_intent

    intent = infer_intent(tracks, narrative=narrative) if arc == "narrative" else None

    # Collect moment track IDs and determine their target positions
    moment_track_ids = [p.track_id for p in (pins or []) if p.pin_type == "moment"]
    if not moment_track_ids and intent:
        moment_track_ids = intent.climax_candidates

    moment_positions = _place_moments(tracks, moment_track_ids, track_count)
    # Explicit position pins win over soft moment targets (SEQ-C6): never let a
    # moment overwrite an explicit index, and never place a moment for a track
    # that is already explicitly positioned elsewhere (which would duplicate it).
    explicitly_pinned_tracks = set(position_pins.values())
    for moment_idx, moment_tid in moment_positions.items():
        if moment_idx in position_pins:
            continue
        if moment_tid in explicitly_pinned_tracks:
            continue
        position_pins[moment_idx] = moment_tid

    # Position pins at index 0 override opener; at last index override closer
    if 0 in position_pins:
        pinned_opener_id = position_pins.pop(0)
    if (track_count - 1) in position_pins:
        pinned_closer_id = position_pins.pop(track_count - 1)

    # Auto opener/closer must not steal a track that belongs to an anchor group;
    # picking a group member as an endpoint would break the block's contiguity.
    anchor_member_ids: set[int] = set()
    for group_track_ids in adjacency_groups.values():
        anchor_member_ids.update(group_track_ids)
    opener, closer, remaining = _select_endpoints(
        tracks,
        track_map,
        pinned_opener_id,
        pinned_closer_id,
        arc,
        exclude_from_auto=set(position_pins.values()) | anchor_member_ids,
    )

    # Remove opener/closer from position_pins to prevent duplication
    position_pins = {
        pos: tid
        for pos, tid in position_pins.items()
        if tid != opener.track_id and tid != closer.track_id
    }

    # Remove position-pinned tracks from the free pool (they'll be inserted after)
    position_pinned_ids = set(position_pins.values())
    remaining = [t for t in remaining if t.track_id not in position_pinned_ids]

    free_tracks, anchor_blocks = _prepare_free_pool(
        remaining,
        track_map,
        adjacency_groups,
        opener,
        closer,
    )

    sequence = _greedy_build(
        opener,
        closer,
        free_tracks,
        anchor_blocks,
        track_count - len(position_pins),
        weights,
        arc,
        bold_jump_chance,
        narrative_mode,
        context_window,
        penalty_overrides,
        random.Random(seed if seed is not None else 0),
        intent,
    )

    # Insert position-pinned tracks at their target indices, keeping anchor
    # blocks atomic (SEQ-C4): a target index that would land inside a contiguous
    # anchor block is shifted to the nearest block boundary so the block is not
    # split. Ranges are recomputed per insertion because each insert shifts them.
    block_member_sets = [{t.track_id for t in block} for block in anchor_blocks]
    for target_idx in sorted(position_pins.keys()):
        tid = position_pins[target_idx]
        if tid in track_map:
            idx = min(target_idx, len(sequence))
            idx = _adjust_index_for_blocks(sequence, idx, block_member_sets)
            sequence.insert(idx, track_map[tid])

    # Post-optimization: 2-opt and artist distribution, protecting pinned positions
    pinned_positions = _get_pinned_positions(
        sequence, pinned_opener_id, pinned_closer_id, adjacency_groups
    )
    # Also protect position-pinned indices
    for target_idx in position_pins:
        if target_idx < len(sequence):
            pinned_positions.add(target_idx)
    sequence = _two_opt(
        sequence,
        weights,
        arc,
        max_iterations=100,
        protected=pinned_positions,
        penalty_overrides=penalty_overrides,
        intent=intent,
        narrative_mode=narrative_mode,
        context_window=context_window,
    )
    sequence = distribute_artists(
        sequence, min_separation=artist_min_separation, protected=pinned_positions
    )
    return sequence


def _adjust_index_for_blocks(
    sequence: list[TrackMetadata],
    idx: int,
    block_member_sets: list[set[int]],
) -> int:
    """Shift an insertion index so it will not split a contiguous anchor block.

    If inserting at ``idx`` would land strictly between two members of the same
    anchor block (``sequence[idx-1]`` and ``sequence[idx]`` share a block), move
    the index to the nearest boundary of that block. Endpoint indices (0 and
    ``len(sequence)``) can never split a block, so they are returned unchanged.
    """
    if idx <= 0 or idx >= len(sequence):
        return idx
    prev_id = sequence[idx - 1].track_id
    cur_id = sequence[idx].track_id
    for members in block_member_sets:
        if prev_id in members and cur_id in members:
            start = idx
            while start > 0 and sequence[start - 1].track_id in members:
                start -= 1
            end = idx
            while end < len(sequence) and sequence[end].track_id in members:
                end += 1
            return start if (idx - start) <= (end - idx) else end
    return idx


def _reorder_with_pins(
    ordered: list[TrackMetadata],
    opener_id: int | None,
    closer_id: int | None,
    adjacency_groups: dict[str, list[int]],
    position_pins: dict[int, int],
) -> list[TrackMetadata]:
    """Overlay explicit placement pins onto a base (narrative) ordering (SEQ-C2).

    The narrative section sequencer produces ``ordered`` from the description.
    This folds the user's explicit pins into that arc so they are honored while
    the un-pinned tracks keep their narrative order as closely as possible:

    * opener/closer pins fix the first/last slot,
    * position pins fix an absolute index (position beats opener/closer at the
      same slot, matching the >=3 engine's precedence),
    * anchor blocks stay contiguous, in ``group_order``, placed at the narrative
      position of their earliest member.

    Pins are assumed already conflict-free (``_validate_pin_conflicts`` runs in
    ``_resolve_pins``), so the four pin categories are disjoint per track. If an
    anchor block cannot be placed contiguously around the fixed pins, a
    ``ValueError`` is raised rather than silently splitting it.
    """
    total = len(ordered)
    if total == 0:
        return ordered
    by_id = {track.track_id: track for track in ordered}

    # Anchor blocks, in group order, members de-duplicated across groups.
    blocks: list[list[int]] = []
    anchored: set[int] = set()
    for group_track_ids in adjacency_groups.values():
        block = [tid for tid in group_track_ids if tid in by_id and tid not in anchored]
        if block:
            blocks.append(block)
            anchored.update(block)

    # Fixed absolute slots: opener/closer first, then position pins override
    # (an explicit position pin wins over an opener/closer at the same slot).
    fixed: dict[int, int] = {}
    if opener_id is not None and opener_id in by_id:
        fixed[0] = opener_id
    if closer_id is not None and closer_id in by_id:
        fixed[total - 1] = closer_id
    for idx, tid in position_pins.items():
        if tid in by_id:
            fixed[max(0, min(idx, total - 1))] = tid
    fixed_tracks = set(fixed.values())

    # Floating runs in narrative order: each anchor block is one atomic run
    # (emitted at its first-appearing member); every other non-fixed track is a
    # single-element run. Fixed tracks are pulled out for absolute placement.
    floating: list[list[int]] = []
    emitted_block: set[int] = set()
    block_of: dict[int, int] = {
        tid: b_i for b_i, block in enumerate(blocks) for tid in block
    }
    for track in ordered:
        tid = track.track_id
        if tid in fixed_tracks:
            continue
        if tid in anchored:
            b_i = block_of[tid]
            if b_i in emitted_block:
                continue
            floating.append(list(blocks[b_i]))
            emitted_block.add(b_i)
        else:
            floating.append([tid])

    result: list[int | None] = [None] * total
    for idx, tid in fixed.items():
        result[idx] = tid

    empty = [i for i in range(total) if result[i] is None]
    empty_set = set(empty)
    for run in floating:
        length = len(run)
        if length == 1:
            slot = empty.pop(0)
            empty_set.discard(slot)
            result[slot] = run[0]
            continue
        # Anchor block: find the leftmost window of `length` consecutive empties.
        start = None
        for candidate in empty:
            if all((candidate + offset) in empty_set for offset in range(length)):
                start = candidate
                break
        if start is None:
            member_titles = ", ".join(by_id[t].title for t in run)
            raise ValueError(
                "Cannot honor adjacency pin under the narrative arc: the anchor "
                f"block ({member_titles}) has no contiguous room around the fixed "
                "pins. Remove a conflicting position/opener/closer pin and retry."
            )
        for offset, tid in enumerate(run):
            result[start + offset] = tid
            empty_set.discard(start + offset)
        empty = [i for i in empty if i in empty_set]

    return [by_id[tid] for tid in result]  # type: ignore[index]


def _get_pinned_positions(
    sequence: list[TrackMetadata],
    opener_id: int | None,
    closer_id: int | None,
    adjacency_groups: dict[str, list[int]],
) -> set[int]:
    """Return set of indices that must not be moved."""
    protected: set[int] = set()
    if opener_id is not None:
        protected.add(0)
    if closer_id is not None:
        protected.add(len(sequence) - 1)
    # Protect adjacency group positions
    all_group_ids = set()
    for group_track_ids in adjacency_groups.values():
        for tid in group_track_ids:
            all_group_ids.add(tid)
    for i, track in enumerate(sequence):
        if track.track_id in all_group_ids:
            protected.add(i)
    return protected


def sequence_playlist(
    db: Database,
    playlist_id: int,
    arc: str = "wave",
    profile: str = "default",
    weights: dict[str, float] | None = None,
    availability_platform: str = "tidal",
    seed: int | None = None,
) -> list[int]:
    """Sequence playlist tracks using the database as authoritative source.

    Loads the track list from DB. Tracks without energy/valence metadata
    are appended at the end (never dropped).

    Tracks that are unavailable on ``availability_platform`` (Tidal, the
    availability source of truth, by default) are excluded from the arc
    optimization and appended at the end alongside metadata-less tracks: an
    unplayable track should not distort the energy flow between the tracks that
    will actually play. They are never dropped, and playlists that have never
    been reconciled are unaffected (no audits => nothing excluded).

    If weights is provided, it overrides the profile's default weights.
    """
    track_ids = db.get_playlist_track_ids(playlist_id)
    if len(track_ids) <= 1:
        return list(track_ids)

    unavailable_ids = set(
        db.get_unavailable_track_ids(playlist_id, availability_platform)
    )
    sequenceable_ids = [tid for tid in track_ids if tid not in unavailable_ids]

    profile_config = get_profile(profile)
    resolved_arc = arc or profile_config.arc
    metadata_map = get_track_metadata_map(db, sequenceable_ids)
    metadata_tracks = [
        metadata_map[track_id]
        for track_id in sequenceable_ids
        if track_id in metadata_map
    ]

    # Everything not placed by the optimizer (unavailable + metadata-less)
    # tails the result in original playlist order, never dropped.
    def _tail(placed: set[int]) -> list[int]:
        return [tid for tid in track_ids if tid not in placed]

    if not metadata_tracks:
        return list(sequenceable_ids) + [
            tid for tid in track_ids if tid in unavailable_ids
        ]

    if len(metadata_tracks) == 1:
        placed = {metadata_tracks[0].track_id}
        return [metadata_tracks[0].track_id, *_tail(placed)]

    from tuneshift.models import PlaylistPin

    pins: list[PlaylistPin] = db.get_pins(playlist_id)

    # Load playlist narrative for narrative arc sequencing
    narrative = db.get_narrative(playlist_id) if resolved_arc == "narrative" else None

    # Use provided weights or fall back to profile's weights
    resolved_weights = weights if weights is not None else profile_config.weights

    ordered_tracks = optimize_sequence(
        metadata_tracks,
        resolved_weights,
        arc=resolved_arc,
        artist_min_separation=profile_config.artist_min_separation,
        bold_jump_chance=profile_config.bold_jump_chance,
        narrative_mode=profile_config.narrative_mode,
        context_window=profile_config.context_window,
        penalty_overrides=profile_config.penalty_overrides,
        pins=pins,
        narrative=narrative,
        seed=seed if seed is not None else playlist_id,
    )

    optimized = [track.track_id for track in ordered_tracks]
    tail = _tail(set(optimized))
    result = optimized + tail

    deferred = len(tail)
    if deferred:
        import sys

        print(
            f"  Note: {deferred} track(s) (no sequencer metadata or unavailable on "
            f"{availability_platform}) appended at end",
            file=sys.stderr,
        )

    return result


def _two_opt(
    sequence: list[TrackMetadata],
    weights: dict[str, float],
    arc: str = "free",
    *,
    max_iterations: int = 100,
    protected: set[int] | None = None,
    penalty_overrides: dict[str, float] | None = None,
    intent: "PlaylistIntent | None" = None,
    narrative_mode: str = "river",
    context_window: int = 5,
) -> list[TrackMetadata]:
    """Swap-neighborhood local search against the global objective (SEQ-A1/A2).

    Accepts a swap only when it does not reduce ``sequence_score`` (arc fit,
    context modifiers, and artist spacing included), so local search optimizes
    the same objective the greedy builder targeted instead of a myopic
    pairwise-continuity score.
    """
    track_count = len(sequence)
    if track_count <= 3:
        return sequence
    protected = protected or set()

    result = list(sequence)
    no_improvement_count = 0

    def _objective(seq: list[TrackMetadata]) -> float:
        return sequence_score(
            seq,
            weights,
            arc,
            penalty_overrides=penalty_overrides,
            intent=intent,
            narrative_mode=narrative_mode,
            context_window=context_window,
        )

    current_objective = _objective(result)

    for _ in range(max_iterations):
        improved = False
        for left_index in range(1, track_count - 2):
            if left_index in protected:
                continue
            for right_index in range(left_index + 2, track_count - 1):
                if right_index in protected:
                    continue

                result[left_index], result[right_index] = (
                    result[right_index],
                    result[left_index],
                )
                new_objective = _objective(result)

                if new_objective > current_objective + 1e-6:
                    current_objective = new_objective
                    improved = True
                else:
                    result[left_index], result[right_index] = (
                        result[right_index],
                        result[left_index],
                    )

        if improved:
            no_improvement_count = 0
        else:
            no_improvement_count += 1
            if no_improvement_count >= 10:
                break

    return result


def _optimize_within_section(
    tracks: list[TrackMetadata],
    weights: dict | None,
    arc: str,  # noqa: ARG001 - reserved arc param; signature parity
) -> list[TrackMetadata]:
    """Optimize track order within a single narrative section using sonic scoring."""
    if len(tracks) <= 2:
        return tracks

    from tuneshift.sequencer.scoring import resolve_weights, score_pair

    resolved_weights = resolve_weights(weights, None, None)

    # Greedy nearest-neighbor within section
    remaining = list(tracks)
    result = [remaining.pop(0)]
    while remaining:
        last = result[-1]
        best_idx = 0
        best_score = -1.0
        for i, candidate in enumerate(remaining):
            s = score_pair(last, candidate, resolved_weights)
            if s > best_score:
                best_score = s
                best_idx = i
        result.append(remaining.pop(best_idx))
    return result


def _score_track_section_fitness(
    track: TrackMetadata,
    section: NarrativeSection,
) -> float:
    """Score how well a track fits a narrative section."""
    score = 0.0

    # Intensity match
    track_intensity = (
        track.emotional_intensity if track.emotional_intensity is not None else 0.5
    )
    intensity_match = 1.0 - abs(track_intensity - section.implied_intensity)
    score += 0.5 * intensity_match

    # Stance match
    if section.implied_stance and track.narrator_stance:
        if track.narrator_stance == section.implied_stance:
            score += 0.3
        elif (
            track.narrator_stance in ("angry", "defiant", "fierce")
            and section.implied_stance == "defiant"
        ):
            score += 0.2

    # Lyrical/theme relevance to section description
    if track.themes and section.description:
        desc_words = set(section.description.lower().split())
        track_themes = set(t.lower() for t in track.themes)
        if desc_words & track_themes:
            score += 0.2

    return min(1.0, score)


def assign_tracks_to_sections(
    tracks: list[TrackMetadata],
    sections: list[NarrativeSection],
    goal: str,  # noqa: ARG001 - signature parity
) -> dict[str, list[TrackMetadata]]:
    """Assign tracks to narrative sections using greedy best-fit algorithm.

    Returns dict mapping section name -> list of tracks assigned.
    Unassigned tracks go to "_flex" key.
    """
    if not sections:
        return {"_flex": list(tracks)}

    # Score all (track, section) pairs
    scores: list[tuple[float, TrackMetadata, NarrativeSection]] = []
    for track in tracks:
        for section in sections:
            fitness = _score_track_section_fitness(track, section)
            scores.append((fitness, track, section))

    # Sort by fitness descending (best fits first)
    scores.sort(key=lambda x: x[0], reverse=True)

    # Greedy assignment
    assignments: dict[str, list[TrackMetadata]] = {s.name: [] for s in sections}
    assignments["_flex"] = []
    assigned_tracks: set[int] = set()
    section_counts: dict[str, int] = {s.name: 0 for s in sections}
    section_caps: dict[str, int] = {s.name: s.capacity for s in sections}

    for _fitness, track, section in scores:
        if track.track_id in assigned_tracks:
            continue
        if section_counts[section.name] >= section_caps[section.name]:
            continue
        assignments[section.name].append(track)
        section_counts[section.name] += 1
        assigned_tracks.add(track.track_id)

    # Unassigned tracks go to flex pool
    for track in tracks:
        if track.track_id not in assigned_tracks:
            assignments["_flex"].append(track)

    return assignments
