"""Greedy nearest-neighbor plus 2-opt sequence optimizer."""

import logging
import math
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from tuneshift.db import Database
from tuneshift.sequencer.metadata import TrackMetadata, get_track_metadata_map
from tuneshift.sequencer.modifiers import SequenceContext, score_candidate
from tuneshift.sequencer.narrative_parser import NarrativeSection
from tuneshift.sequencer.profiles import get_profile
from tuneshift.sequencer.scoring import score_pair

if TYPE_CHECKING:
    from tuneshift.sequencer.intent import PlaylistIntent

logger = logging.getLogger(__name__)


# Swap-neighborhood windowing (SEQ-A4). At or below the threshold the local
# search scans the exact full neighborhood so small/medium playlists (including
# every gold/acceptance fixture) are byte-for-byte unchanged. Above it, the
# inner look-distance is bounded so cost grows ~O(n * window) per pass instead
# of O(n^2), keeping large playlists responsive.
_SWAP_WINDOW_THRESHOLD = 500
_SWAP_WINDOW = 8
# Lookback used to warm the context for a bounded region rescore. >= the
# context window (5) and the artist-recency decay cap (9), so windowed modifiers
# and recency are reproduced exactly.
_LOCAL_LOOKBACK = 12
# Large playlists converge in a few passes; cap them so the windowed heuristic
# stays well under its wall-clock budget.
_WINDOWED_MAX_PASSES = 3


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

    scored: list[tuple[bool, float, int, TrackMetadata]] = []
    for track in tracks:
        has_energy = track.energy is not None
        energy = track.energy if track.energy is not None else 0.5
        energy_fit = 1.0 - abs(energy - target)
        valence = track.valence if track.valence is not None else 0.5
        valence_fit = 1.0 - abs(valence - 0.5) * 0.5
        fit = energy_fit * 0.7 + valence_fit * 0.3
        scored.append((has_energy, fit, -track.track_id, track))

    # Rank metadata-complete tracks first (SEQ-S3): a missing-energy track
    # defaulting to 0.5 must not out-fit a real endpoint. Ties break on fit,
    # then deterministically on the lowest track_id.
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return scored[0][3]


def select_closer(tracks: list[TrackMetadata], arc: str) -> TrackMetadata:
    """Select the best closing track for the given arc shape."""
    target = _target_energy(1.0, arc)
    if target is None:
        target = 0.3

    scored: list[tuple[bool, float, int, TrackMetadata]] = []
    for track in tracks:
        has_energy = track.energy is not None
        energy = track.energy if track.energy is not None else 0.5
        energy_fit = 1.0 - abs(energy - target)
        mode_bonus = 0.2 if track.mode == 1 else 0.0
        fit = energy_fit + mode_bonus
        scored.append((has_energy, fit, -track.track_id, track))

    # Rank metadata-complete tracks first (SEQ-S3).
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return scored[0][3]


def _has_adjacency_violation(seq: list[TrackMetadata], idx: int) -> bool:
    """True when the track at ``idx`` shares an artist with a neighbor."""
    if idx > 0 and seq[idx].artist == seq[idx - 1].artist:
        return True
    if idx < len(seq) - 1 and seq[idx].artist == seq[idx + 1].artist:
        return True
    return False


def _first_swap_violation(
    result: list[TrackMetadata], protected: set[int]
) -> int | None:
    """Index of the first movable track that clumps with its predecessor."""
    for index in range(len(result) - 1):
        if result[index].artist == result[index + 1].artist:
            candidate = index + 1
            # A pinned violator cannot be moved; skip it and keep scanning so
            # later, movable clumps are still redistributed (SEQ-C5).
            if candidate in protected:
                continue
            return candidate
    return None


def _best_redistribution_target(
    result: list[TrackMetadata],
    violation_idx: int,
    violator: TrackMetadata,
    protected: set[int],
) -> int | None:
    """Best non-violating swap partner that maximizes same-artist spacing."""
    track_count = len(result)
    best_target = None
    best_distance = -1
    artist_positions = [
        index for index in range(track_count) if result[index].artist == violator.artist
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
    return best_target


def _fallback_swap(
    result: list[TrackMetadata],
    violation_idx: int,
    violator: TrackMetadata,
    protected: set[int],
) -> bool:
    """Last resort: swap the violator with any movable different-artist track,
    scanning from the end. Returns False when none is available."""
    for target_idx in range(len(result) - 1, -1, -1):
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
        return True
    return False


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

    max_passes = max(50, min_separation * 10)
    for _ in range(max_passes):
        violation_idx = _first_swap_violation(result, protected)
        if violation_idx is None:
            break

        violator = result[violation_idx]
        best_target = _best_redistribution_target(
            result, violation_idx, violator, protected
        )
        if best_target is not None:
            result[violation_idx], result[best_target] = (
                result[best_target],
                result[violation_idx],
            )
            continue

        if not _fallback_swap(result, violation_idx, violator, protected):
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
    # Order-preserving de-duplication: two targets for the same track would
    # otherwise map it to two positions and insert it twice (SEQ-C6).
    moments = list(dict.fromkeys(moments))
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


def _pick_opener(
    tracks: list[TrackMetadata],
    track_map: dict[int, TrackMetadata],
    pinned_opener_id: int | None,
    pinned_closer_id: int | None,
    arc: str,
    excluded: set[int],
) -> TrackMetadata:
    """Resolve the opener, never returning the pinned closer."""
    if pinned_opener_id is not None and pinned_opener_id in track_map:
        return track_map[pinned_opener_id]
    # A pinned closer is hard-placed at the end. Auto-selecting it as the
    # opener as well emits that track twice and drops another entirely, so
    # it is never an opener candidate.
    pool = [t for t in tracks if t.track_id != pinned_closer_id]
    narrowed = [t for t in pool if t.track_id not in excluded]
    # Exclusions may empty the pool; relaxing them is safe, but re-admitting
    # the pinned closer is precisely the duplicating case.
    return select_opener(narrowed or pool or tracks, arc)


def _pick_closer(
    remaining: list[TrackMetadata],
    track_map: dict[int, TrackMetadata],
    pinned_closer_id: int | None,
    arc: str,
    excluded: set[int],
) -> TrackMetadata:
    """Resolve the closer from the tracks left after the opener."""
    if pinned_closer_id is not None and pinned_closer_id in track_map:
        return track_map[pinned_closer_id]
    candidates = [t for t in remaining if t.track_id not in excluded]
    return select_closer(candidates or remaining, arc)


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

    Guarantees ``opener``, ``closer``, and ``remaining`` are disjoint and
    together cover ``tracks`` exactly once. Violating that duplicated one
    track and dropped another (see the membership guard in
    ``optimize_sequence``).
    """
    excluded = exclude_from_auto or set()
    opener = _pick_opener(
        tracks, track_map, pinned_opener_id, pinned_closer_id, arc, excluded
    )
    after_opener = [t for t in tracks if t.track_id != opener.track_id]
    closer = _pick_closer(after_opener, track_map, pinned_closer_id, arc, excluded)

    if opener.track_id == closer.track_id:
        # Contradictory opener/closer pins that slipped past validation. The
        # closer keeps its slot; re-pick the opener so no track appears twice.
        alternatives = [t for t in tracks if t.track_id != closer.track_id]
        if alternatives:
            opener = select_opener(alternatives, arc)

    endpoints = {opener.track_id, closer.track_id}
    remaining = [t for t in tracks if t.track_id not in endpoints]
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

        for track_id in sorted(available):
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

        # Sort by score desc; break ties deterministically on the lowest
        # track_id so ordering never depends on set-iteration order (SEQ-D2).
        candidates.sort(key=lambda item: (item[0], -item[1].track_id), reverse=True)

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
    score_fn: Callable[[TrackMetadata, TrackMetadata, dict[str, float]], float]
    | None = None,
) -> float:
    """Single global objective for a full sequence (SEQ-A1).

    Sums, over every adjacent transition, the same arc-fit x context-modified
    pairwise score that ``_greedy_build`` optimizes position-by-position. The
    greedy builder and the local search (``_swap_search``) both optimize this one
    function, so local search can no longer improve raw pairwise continuity at
    the expense of arc fit or context (artist spacing, variety, monotony, ...).

    ``score_fn`` overrides the pairwise scorer; local search injects a memoized
    variant so repeated adjacent pairs across candidate swaps are not rescored
    from scratch (SEQ-A4). Defaults to :func:`score_pair`.

    Higher is better. Returns 0.0 for sequences shorter than two tracks.
    """
    pair_score = score_fn if score_fn is not None else score_pair
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
        base = pair_score(current, candidate, weights)
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


def _sequence_narrative_sections(
    tracks: list[TrackMetadata],
    sections: list[NarrativeSection],
    weights: dict[str, float],
    arc: str,
    pinned_opener_id: int | None,
    pinned_closer_id: int | None,
    adjacency_groups: dict[str, list[int]],
    position_pins: dict[int, int],
) -> list[TrackMetadata]:
    """Order tracks by declared narrative sections, then fold in explicit pins.

    Full-coverage narratives (section capacity >= track count) place tracks
    positionally; partial coverage uses fitness-based assignment. Either way the
    per-section order is refined by ``_optimize_within_section`` and any explicit
    pins are overlaid via ``_reorder_with_pins`` (SEQ-C2).
    """
    has_pins = bool(
        pinned_opener_id is not None
        or pinned_closer_id is not None
        or adjacency_groups
        or position_pins
    )

    def _finish(ordered: list[TrackMetadata]) -> list[TrackMetadata]:
        if has_pins:
            return _reorder_with_pins(
                ordered,
                pinned_opener_id,
                pinned_closer_id,
                adjacency_groups,
                position_pins,
            )
        return ordered

    if sum(section.capacity for section in sections) >= len(tracks):
        ordered = _fill_sections_positionally(tracks, sections, weights, arc)
    else:
        ordered = _fill_sections_by_fitness(tracks, sections, weights, arc)
    return _finish(ordered)


def _fill_sections_positionally(
    tracks: list[TrackMetadata],
    sections: list[NarrativeSection],
    weights: dict[str, float],
    arc: str,
) -> list[TrackMetadata]:
    """Slice tracks into sections by position (narrative order == input order)."""
    ordered: list[TrackMetadata] = []
    idx = 0
    for section in sections:
        section_tracks = tracks[idx : idx + section.capacity]
        ordered.extend(_order_section(section_tracks, weights, arc))
        idx += section.capacity
    ordered.extend(tracks[idx:])
    return ordered


def _fill_sections_by_fitness(
    tracks: list[TrackMetadata],
    sections: list[NarrativeSection],
    weights: dict[str, float],
    arc: str,
) -> list[TrackMetadata]:
    """Assign tracks to sections by fitness when sections do not cover all slots."""
    assignments = assign_tracks_to_sections(tracks, sections, goal=arc)
    ordered: list[TrackMetadata] = []
    for section in sections:
        ordered.extend(_order_section(assignments.get(section.name, []), weights, arc))
    ordered.extend(assignments.get("_flex", []))
    return ordered


def _order_section(
    section_tracks: list[TrackMetadata],
    weights: dict[str, float],
    arc: str,
) -> list[TrackMetadata]:
    """Refine a single section's order (no-op for 0/1-track sections)."""
    if len(section_tracks) > 1:
        return _optimize_within_section(section_tracks, weights, arc)
    return list(section_tracks)


def _apply_moment_and_index_pins(
    tracks,
    pins,
    intent,
    position_pins,
    track_count,
    pinned_opener_id,
    pinned_closer_id,
    adjacency_groups=None,
):
    """Merge soft moment targets into position_pins (explicit pins win) and
    promote index-0 / last-index pins to opener / closer overrides. Mutates
    position_pins; returns the resolved (opener_id, closer_id)."""
    moment_track_ids = [p.track_id for p in (pins or []) if p.pin_type == "moment"]
    if not moment_track_ids and intent:
        moment_track_ids = intent.climax_candidates

    moment_positions = _place_moments(tracks, moment_track_ids, track_count)
    # Explicit placement pins win over soft moment targets (SEQ-C6): never let a
    # moment overwrite an explicit index, and never place a moment for a track
    # that is already hard-placed elsewhere (which would duplicate it). A track
    # can be hard-placed by a position pin, an opener/closer pin, or membership
    # in an anchor group; a moment on any of those must be dropped, not honored.
    explicitly_pinned_tracks = set(position_pins.values())
    if pinned_opener_id is not None:
        explicitly_pinned_tracks.add(pinned_opener_id)
    if pinned_closer_id is not None:
        explicitly_pinned_tracks.add(pinned_closer_id)
    for group_track_ids in (adjacency_groups or {}).values():
        explicitly_pinned_tracks.update(group_track_ids)
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
    return pinned_opener_id, pinned_closer_id


def _insert_position_pins(sequence, position_pins, track_map, anchor_blocks):
    """Insert position-pinned tracks at their target indices, shifting each
    index off an anchor-block interior so blocks stay contiguous (SEQ-C4)."""
    block_member_sets = [{t.track_id for t in block} for block in anchor_blocks]
    for target_idx in sorted(position_pins.keys()):
        tid = position_pins[target_idx]
        if tid in track_map:
            idx = min(target_idx, len(sequence))
            idx = _adjust_index_for_blocks(sequence, idx, block_member_sets)
            sequence.insert(idx, track_map[tid])


def _protected_positions(
    sequence, pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins
):
    """Indices that 2-opt / distribution must not move: opener, closer, anchor
    members, and explicit position pins."""
    pinned_positions = _get_pinned_positions(
        sequence, pinned_opener_id, pinned_closer_id, adjacency_groups
    )
    # Also protect position-pinned indices
    for target_idx in position_pins:
        if target_idx < len(sequence):
            pinned_positions.add(target_idx)
    return pinned_positions


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
            return _sequence_narrative_sections(
                tracks,
                sections,
                weights,
                arc,
                pinned_opener_id,
                pinned_closer_id,
                adjacency_groups,
                position_pins,
            )

    # Infer intent early for narrative arc
    from tuneshift.sequencer.intent import infer_intent

    intent = infer_intent(tracks, narrative=narrative) if arc == "narrative" else None

    # Collect moment track IDs and determine their target positions
    pinned_opener_id, pinned_closer_id = _apply_moment_and_index_pins(
        tracks,
        pins,
        intent,
        position_pins,
        track_count,
        pinned_opener_id,
        pinned_closer_id,
        adjacency_groups,
    )

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
    _insert_position_pins(sequence, position_pins, track_map, anchor_blocks)

    # Post-optimization: 2-opt and artist distribution, protecting pinned positions
    pinned_positions = _protected_positions(
        sequence, pinned_opener_id, pinned_closer_id, adjacency_groups, position_pins
    )
    sequence = _swap_search(
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


def _collect_anchor_blocks(
    adjacency_groups: dict[str, list[int]],
    by_id: dict[int, TrackMetadata],
) -> tuple[list[list[int]], set[int]]:
    """Materialize anchor blocks in group order, de-duplicating members."""
    blocks: list[list[int]] = []
    anchored: set[int] = set()
    for group_track_ids in adjacency_groups.values():
        block = [tid for tid in group_track_ids if tid in by_id and tid not in anchored]
        if block:
            blocks.append(block)
            anchored.update(block)
    return blocks, anchored


def _compute_fixed_slots(
    opener_id: int | None,
    closer_id: int | None,
    position_pins: dict[int, int],
    by_id: dict[int, TrackMetadata],
    total: int,
) -> dict[int, int]:
    """Resolve absolute slots; position pins override opener/closer collisions."""
    fixed: dict[int, int] = {}
    if opener_id is not None and opener_id in by_id:
        fixed[0] = opener_id
    if closer_id is not None and closer_id in by_id:
        fixed[total - 1] = closer_id
    for idx, tid in position_pins.items():
        if tid in by_id:
            fixed[max(0, min(idx, total - 1))] = tid
    return fixed


def _collect_floating_runs(
    ordered: list[TrackMetadata],
    fixed_tracks: set[int],
    anchored: set[int],
    blocks: list[list[int]],
) -> list[list[int]]:
    """Build narrative-order runs: each anchor block is one atomic run."""
    block_of = {tid: b_i for b_i, block in enumerate(blocks) for tid in block}
    floating: list[list[int]] = []
    emitted_block: set[int] = set()
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
    return floating


def _place_pinned_runs(
    floating: list[list[int]],
    fixed: dict[int, int],
    total: int,
    by_id: dict[int, TrackMetadata],
) -> list[int]:
    """Place fixed slots, then fill floating runs into the leftmost empty gaps.

    Anchor blocks (multi-track runs) require a contiguous window of empty slots;
    if none exists around the fixed pins, raise rather than split the block.
    """
    result: list[int | None] = [None] * total
    for idx, tid in fixed.items():
        result[idx] = tid

    empty = [i for i in range(total) if result[i] is None]
    empty_set = set(empty)
    for run in floating:
        if len(run) == 1:
            slot = empty.pop(0)
            empty_set.discard(slot)
            result[slot] = run[0]
            continue
        start = _find_contiguous_start(empty, empty_set, len(run))
        if start is None:
            member_titles = ", ".join(by_id[tid].title for tid in run)
            raise ValueError(
                "Cannot honor adjacency pin under the narrative arc: the anchor "
                f"block ({member_titles}) has no contiguous room around the fixed "
                "pins. Remove a conflicting position/opener/closer pin and retry."
            )
        for offset, tid in enumerate(run):
            result[start + offset] = tid
            empty_set.discard(start + offset)
        empty = [i for i in empty if i in empty_set]

    return result  # type: ignore[return-value]


def _find_contiguous_start(
    empty: list[int], empty_set: set[int], length: int
) -> int | None:
    """Leftmost empty index with ``length`` consecutive empty slots after it."""
    for candidate in empty:
        if all((candidate + offset) in empty_set for offset in range(length)):
            return candidate
    return None


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

    blocks, anchored = _collect_anchor_blocks(adjacency_groups, by_id)
    fixed = _compute_fixed_slots(opener_id, closer_id, position_pins, by_id, total)
    floating = _collect_floating_runs(ordered, set(fixed.values()), anchored, blocks)
    result = _place_pinned_runs(floating, fixed, total, by_id)
    return [by_id[tid] for tid in result]


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
        logger.info(
            "deferred tracks appended count=%d reason=no_metadata_or_unavailable "
            "platform=%s",
            deferred,
            availability_platform,
        )

    return result


def _swap_search(
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

    # Memoize the pairwise scorer for the duration of this search (SEQ-A4).
    # Within one search the track objects are fixed and weights are constant, so
    # keying on ``(id(a), id(b))`` is safe and unique; each candidate swap only
    # changes two positions, so the vast majority of adjacent pairs recur and
    # hit the cache instead of re-running the dimension loop in score_pair.
    _pair_cache: dict[tuple[int, int], float] = {}

    def _cached_pair(a: TrackMetadata, b: TrackMetadata, w: dict[str, float]) -> float:
        key = (id(a), id(b))
        cached = _pair_cache.get(key)
        if cached is None:
            cached = score_pair(a, b, w)
            _pair_cache[key] = cached
        return cached

    def _objective(seq: list[TrackMetadata]) -> float:
        return sequence_score(
            seq,
            weights,
            arc,
            penalty_overrides=penalty_overrides,
            intent=intent,
            narrative_mode=narrative_mode,
            context_window=context_window,
            score_fn=_cached_pair,
        )

    def _region_score(seq: list[TrackMetadata], lo: int, hi: int) -> float:
        """Sum objective contributions for positions ``[max(1, lo), hi]``.

        The context is warmed by replaying the preceding ``_LOCAL_LOOKBACK``
        tracks, which is enough to reproduce every windowed modifier (recent
        tracks, energies, themes) and the artist-recency decay (capped at 9
        positions) exactly. Only the global "artist ever seen" variety bonus is
        approximated against the warm window; because the same warm-up is used
        before and after a swap, that term is consistent across the comparison,
        so the local delta faithfully ranks the swap. Used only on the large-n
        windowed path, whose output is heuristic by design.
        """
        first = max(1, lo)
        start = max(0, first - _LOCAL_LOOKBACK)
        ctx = SequenceContext(
            position=start,
            total=len(seq),
            narrative_mode=narrative_mode,
            context_window=context_window,
        )
        for warm in range(start, first):
            ctx.advance(seq[warm])

        total = 0.0
        for position in range(first, hi + 1):
            current = seq[position - 1]
            candidate = seq[position]
            base = _cached_pair(current, candidate, weights)
            arc_mult = _arc_fit_multiplier(candidate, position, len(seq), arc)
            total += score_candidate(
                candidate,
                current,
                ctx,
                base * arc_mult,
                penalty_overrides,
                intent,
            )
            ctx.advance(candidate)
        return total

    if track_count <= _SWAP_WINDOW_THRESHOLD:
        return _exact_swap_search(
            result, track_count, protected, max_iterations, _objective
        )
    return _windowed_swap_search(
        result, track_count, protected, max_iterations, _region_score
    )


def _exact_swap_search(
    result: list[TrackMetadata],
    track_count: int,
    protected: set[int],
    max_iterations: int,
    objective: Callable[[list[TrackMetadata]], float],
) -> list[TrackMetadata]:
    """Exact full-objective swap search (unchanged behavior for small inputs)."""
    current_objective = objective(result)
    no_improvement_count = 0

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
                new_objective = objective(result)

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


def _windowed_swap_search(
    result: list[TrackMetadata],
    track_count: int,
    protected: set[int],
    max_iterations: int,
    region_score: Callable[[list[TrackMetadata], int, int], float],
) -> list[TrackMetadata]:
    """Windowed local-delta swap search for large playlists (SEQ-A4).

    Each swap is bounded to a look-distance of ``_SWAP_WINDOW`` and evaluated by
    an exact bounded region rescore instead of the full O(n) objective, so cost
    is ~O(n * window) per pass. Swaps are accepted only when the local objective
    strictly improves, so the result is never worse than the greedy build.
    """
    passes = min(max_iterations, _WINDOWED_MAX_PASSES)

    for _ in range(passes):
        improved = False
        for left_index in range(1, track_count - 2):
            if left_index in protected:
                continue
            right_limit = min(left_index + 2 + _SWAP_WINDOW, track_count - 1)
            for right_index in range(left_index + 2, right_limit):
                if right_index in protected:
                    continue

                lo = left_index
                hi = min(right_index + _LOCAL_LOOKBACK, track_count - 1)
                before = region_score(result, lo, hi)

                result[left_index], result[right_index] = (
                    result[right_index],
                    result[left_index],
                )
                after = region_score(result, lo, hi)

                if after > before + 1e-6:
                    improved = True
                else:
                    result[left_index], result[right_index] = (
                        result[right_index],
                        result[left_index],
                    )

        if not improved:
            break

    return result


# Back-compat alias: the local search was historically named ``_two_opt`` even
# though its neighborhood is adjacent swaps, not 2-opt segment reversals
# (SEQ-A2). ``_swap_search`` is the accurate name; the alias keeps older
# imports working.
_two_opt = _swap_search


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
