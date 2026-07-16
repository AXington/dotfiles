"""Batch operations: plan/apply model for bulk playlist changes."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tuneshift.db import Database

_PLAN_DIR = Path.home() / ".local" / "share" / "tuneshift" / "plans"

# Regex for extracting featured artists from track titles
_FEAT_EXTRACT_RE = None


def _get_feat_re():
    """Lazy-load the featured artist extraction regex."""
    global _FEAT_EXTRACT_RE
    if _FEAT_EXTRACT_RE is None:
        import re

        _FEAT_EXTRACT_RE = re.compile(
            r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]",
            re.IGNORECASE,
        )
    return _FEAT_EXTRACT_RE


def extract_featured_artists(title: str) -> list[str]:
    """Extract featured artist names from a track title."""
    match = _get_feat_re().search(title)
    if not match:
        return []
    raw = match.group(1)
    # Split on common delimiters: ", ", " & ", " and "
    import re

    parts = re.split(r"\s*(?:,|&|and)\s*", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def split_artist_credits(artist: str) -> list[str]:
    """Split a multi-artist credit into individual artist names.

    Handles: "Drake, 21 Savage", "Jack & Diane", "A and B", "X x Y"
    """
    import re

    parts = re.split(r"\s*(?:,\s+|&|\band\b|\bx\b)\s*", artist, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def check_track_against_bans(db: Database, title: str, artist: str) -> str | None:
    """Check if any credited artist on a track is banned.

    Checks primary artist (split on multi-credit delimiters) and featured
    artists extracted from the title. Returns the banned name if found, None otherwise.
    """
    # Check primary artist segments
    for segment in split_artist_credits(artist):
        if db.is_artist_banned(segment):
            return segment
    # Check featured artists in title
    for featured in extract_featured_artists(title):
        if db.is_artist_banned(featured):
            return featured
    return None


@dataclass
class PlanOperation:
    """A single planned change to a playlist."""

    action: str  # "rm", "add", "keep", "create_playlist", "move_to_playlist", "assign_section", "set_narrative"
    track_title: str = ""
    track_artist: str = ""
    track_id: int | None = None
    reason: str = ""
    position: int | None = None
    previous_position: int | None = None
    previous_section: str | None = None
    target_name: str | None = None  # for split/merge/section targets
    section_name: str | None = None


@dataclass
class BatchPlan:
    """A set of planned changes to a playlist."""

    playlist_name: str
    playlist_id: int
    operations: list[PlanOperation] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def removals(self) -> list[PlanOperation]:
        return [op for op in self.operations if op.action == "rm"]

    @property
    def additions(self) -> list[PlanOperation]:
        return [op for op in self.operations if op.action == "add"]

    @property
    def keeps(self) -> list[PlanOperation]:
        return [op for op in self.operations if op.action == "keep"]

    def save(self) -> Path:
        """Save plan to disk."""
        _PLAN_DIR.mkdir(parents=True, exist_ok=True)
        plan_file = _PLAN_DIR / "current.json"
        data = {
            "playlist": self.playlist_name,
            "playlist_id": self.playlist_id,
            "created": self.created_at,
            "operations": [
                {
                    "action": op.action,
                    "track": op.track_title,
                    "artist": op.track_artist,
                    "track_id": op.track_id,
                    "reason": op.reason,
                }
                for op in self.operations
            ],
        }
        plan_file.write_text(json.dumps(data, indent=2))
        return plan_file

    @classmethod
    def load(cls) -> BatchPlan | None:
        """Load the current plan from disk."""
        plan_file = _PLAN_DIR / "current.json"
        if not plan_file.exists():
            return None
        data = json.loads(plan_file.read_text())
        plan = cls(
            playlist_name=data["playlist"],
            playlist_id=data["playlist_id"],
            created_at=data["created"],
        )
        for op in data["operations"]:
            plan.operations.append(
                PlanOperation(
                    action=op["action"],
                    track_title=op["track"],
                    track_artist=op["artist"],
                    track_id=op.get("track_id"),
                    reason=op.get("reason", ""),
                )
            )
        return plan

    @staticmethod
    def discard() -> bool:
        """Remove the current plan file."""
        plan_file = _PLAN_DIR / "current.json"
        if plan_file.exists():
            plan_file.unlink()
            return True
        return False


def plan_dedupe(db: Database, playlist_id: int, cap: int) -> list[PlanOperation]:
    """Plan deduplication: flag artists with more than cap tracks."""
    tracks = db.get_playlist_tracks(playlist_id)
    by_artist: dict[str, list] = {}
    for i, t in enumerate(tracks):
        by_artist.setdefault(t.artist, []).append((i, t))

    ops: list[PlanOperation] = []
    for artist, entries in by_artist.items():
        if len(entries) <= cap:
            continue
        # Keep the first N tracks (by current position), remove the rest
        entries.sort(key=lambda e: e[0])
        for idx, (pos, track) in enumerate(entries):
            if idx < cap:
                ops.append(
                    PlanOperation(
                        action="keep",
                        track_title=track.title,
                        track_artist=track.artist,
                        track_id=track.id,
                        reason=f"dedupe cap={cap}, keeping #{idx + 1} of {len(entries)}",
                        position=pos,
                    )
                )
            else:
                ops.append(
                    PlanOperation(
                        action="rm",
                        track_title=track.title,
                        track_artist=track.artist,
                        track_id=track.id,
                        reason=f"dedupe cap={cap}, {artist} has {len(entries)} tracks",
                        position=pos,
                    )
                )
    return ops


def plan_rm_artist(
    db: Database, playlist_id: int, artist_name: str
) -> list[PlanOperation]:
    """Plan removal of all tracks by an artist (including featured)."""
    tracks = db.get_playlist_tracks(playlist_id)
    target_lower = artist_name.lower()
    ops: list[PlanOperation] = []

    for i, t in enumerate(tracks):
        is_primary = t.artist.lower() == target_lower
        featured = extract_featured_artists(t.title)
        is_featured = any(f.lower() == target_lower for f in featured)

        if is_primary or is_featured:
            credit = "primary artist" if is_primary else "featured in title"
            ops.append(
                PlanOperation(
                    action="rm",
                    track_title=t.title,
                    track_artist=t.artist,
                    track_id=t.id,
                    reason=f"artist removal: {artist_name} ({credit})",
                    position=i,
                )
            )
    return ops


def plan_review_fixes(
    db: Database, playlist_id: int, llm_judge=None
) -> list[PlanOperation]:
    """Plan fixes based on review findings (hard violations = rm, soft = warn).

    Uses the same rule routing as ``review``: artist-tag and era rules
    deterministically, thematic rules via ``llm_judge`` when supplied. Accepted
    (track, rule) pairs are suppressed, so an accepted track is never proposed
    for removal.
    """
    from tuneshift.commands.compose_cmd import _build_artist_lookup, _get_concept
    from tuneshift.composer.reviewer import review_playlist
    from tuneshift.sequencer.metadata import track_to_metadata

    concept = _get_concept(db, playlist_id)
    if concept is None:
        return []

    tracks_raw = db.get_playlist_tracks(playlist_id)
    tracks = [track_to_metadata(t) for t in tracks_raw]
    artist_lookup = _build_artist_lookup(db, playlist_id)

    findings = review_playlist(
        tracks,
        concept=concept,
        artist_lookup=artist_lookup,
        year_lookup=db.get_release_years_for_playlist(playlist_id),
        llm_judge=llm_judge,
        accepted=db.get_concept_acceptances(playlist_id),
    )

    ops: list[PlanOperation] = []
    seen_track_ids: set[int] = set()

    for finding in findings:
        if finding.severity < 0.8:
            continue
        # Extract track info from finding description
        import re

        match = re.search(r'"([^"]+)" by (.+?) - Rule:', finding.description)
        if not match:
            continue
        title = match.group(1)
        artist = match.group(2)
        # Find the track
        for i, t in enumerate(tracks_raw):
            if t.title == title and t.artist == artist and t.id not in seen_track_ids:
                ops.append(
                    PlanOperation(
                        action="rm",
                        track_title=t.title,
                        track_artist=t.artist,
                        track_id=t.id,
                        reason=finding.description,
                        position=i,
                    )
                )
                seen_track_ids.add(t.id)
                break

    return ops


def plan_sweep_banned(
    db: Database, playlist_id: int | None = None
) -> dict[int, list[PlanOperation]]:
    """Sweep one or all playlists for banned artists.

    Returns a dict of playlist_id -> list of removal operations.
    """
    if playlist_id is not None:
        playlist_ids = [playlist_id]
    else:
        playlists = db.list_playlists()
        playlist_ids = [p.id for p in playlists]

    results: dict[int, list[PlanOperation]] = {}
    for pid in playlist_ids:
        tracks = db.get_playlist_tracks(pid)
        ops: list[PlanOperation] = []
        for i, t in enumerate(tracks):
            banned_name = check_track_against_bans(db, t.title, t.artist)
            if banned_name:
                ops.append(
                    PlanOperation(
                        action="rm",
                        track_title=t.title,
                        track_artist=t.artist,
                        track_id=t.id,
                        reason=f"banned artist: {banned_name}",
                        position=i,
                        previous_position=i,
                    )
                )
        if ops:
            results[pid] = ops
    return results


def match_filter(track, filter_str: str, db: Database | None = None) -> bool:
    """Check if a track matches a filter expression.

    Filter types:
      artist:Name - match artist (case-insensitive)
      vibe:keyword - match track vibes
      theme:keyword - match track themes
      energy:<0.4 - numeric comparison on energy
      plain text - substring match on title
    """
    import re as _re

    from tuneshift.sequencer.metadata import track_to_metadata

    meta = track_to_metadata(track)

    if filter_str.startswith("artist:"):
        target = filter_str[7:].strip().casefold()
        return target in track.artist.casefold()
    elif filter_str.startswith("vibe:"):
        target = filter_str[5:].strip().casefold()
        return any(target in v.casefold() for v in meta.vibes)
    elif filter_str.startswith("theme:"):
        target = filter_str[6:].strip().casefold()
        return any(target in t.casefold() for t in meta.themes)
    elif filter_str.startswith("energy:"):
        expr = filter_str[7:].strip()
        track_energy = meta.energy or meta.emotional_intensity
        if track_energy is None:
            return False
        match = _re.match(r"([<>]=?)\s*([\d.]+)", expr)
        if not match:
            return False
        op, val = match.group(1), float(match.group(2))
        if op == "<":
            return track_energy < val
        elif op == "<=":
            return track_energy <= val
        elif op == ">":
            return track_energy > val
        elif op == ">=":
            return track_energy >= val
        return False
    else:
        # Plain text: substring match on title
        return filter_str.casefold() in track.title.casefold()


def apply_filters(tracks: list, filters: list[str], db: Database | None = None) -> list:
    """Apply AND-combined filters within each filter string (comma-separated),
    OR-combined across multiple filter strings."""
    if not filters:
        return []

    matched = set()
    for filter_group in filters:
        # Within a filter group, comma = AND
        sub_filters = [f.strip() for f in filter_group.split(",")]
        for i, track in enumerate(tracks):
            if all(match_filter(track, sf, db) for sf in sub_filters):
                matched.add(i)

    return [tracks[i] for i in sorted(matched)]


def plan_split(
    db: Database, playlist_id: int, new_name: str, filters: list[str]
) -> list[PlanOperation]:
    """Plan splitting tracks matching filters into a new playlist."""
    tracks = db.get_playlist_tracks(playlist_id)
    matching = apply_filters(tracks, filters, db)

    ops: list[PlanOperation] = []

    if not matching:
        return ops

    # Create playlist operation
    ops.append(
        PlanOperation(
            action="create_playlist",
            target_name=new_name,
            reason=f"split target for filter: {', '.join(filters)}",
        )
    )

    # Move matching tracks
    for track in matching:
        pos = next((i for i, t in enumerate(tracks) if t.id == track.id), 0)
        ops.append(
            PlanOperation(
                action="move_to_playlist",
                track_title=track.title,
                track_artist=track.artist,
                track_id=track.id,
                target_name=new_name,
                position=pos,
                previous_position=pos,
                reason=f"matches filter: {', '.join(filters)}",
            )
        )

    return ops


def plan_merge(
    db: Database, source_ids: list[int], into_id: int
) -> list[PlanOperation]:
    """Plan merging source playlists into a target, deduplicating."""
    target_tracks = db.get_playlist_tracks(into_id)
    target_track_ids = {t.id for t in target_tracks}

    ops: list[PlanOperation] = []

    for source_id in source_ids:
        if source_id == into_id:
            continue
        source_tracks = db.get_playlist_tracks(source_id)
        for track in source_tracks:
            if track.id not in target_track_ids:
                ops.append(
                    PlanOperation(
                        action="add",
                        track_title=track.title,
                        track_artist=track.artist,
                        track_id=track.id,
                        reason="merge: unique track from source",
                    )
                )
                target_track_ids.add(track.id)

    return ops


def apply_split(db: Database, plan: BatchPlan) -> tuple[int, int]:
    """Apply a split plan: create new playlist, move tracks."""
    moved = 0
    new_playlist_id = None

    for op in plan.operations:
        if op.action == "create_playlist" and op.target_name:
            existing = db.find_playlist_by_name(op.target_name)
            if existing:
                new_playlist_id = existing.id
            else:
                new_playlist_id = db.create_playlist(op.target_name)
        elif op.action == "move_to_playlist" and op.track_id and new_playlist_id:
            # Add to new playlist
            new_tracks = db.get_playlist_tracks(new_playlist_id)
            next_pos = len(new_tracks)
            db.add_track_to_playlist(new_playlist_id, op.track_id, next_pos)
            # Remove from source
            db.remove_track_from_playlist(plan.playlist_id, op.track_id)
            moved += 1

    return moved, 0


def parse_plan_file(content: str) -> list[PlanOperation]:
    """Parse a plan file in simple text format.

    Format:
      - Title - Artist          # remove
      + Title - Artist          # add
      = Title - Artist -> pos:7       # move to position
      = Title - Artist -> sec:WRATH   # move to section
      = Title - Artist -> sec:WRATH:3 # move to position within section
    """
    ops: list[PlanOperation] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("- "):
            parts = line[2:].rsplit(" - ", 1)
            if len(parts) == 2:
                ops.append(
                    PlanOperation(
                        action="rm",
                        track_title=parts[0].strip(),
                        track_artist=parts[1].strip(),
                    )
                )
            else:
                ops.append(PlanOperation(action="rm", track_title=line[2:].strip()))
        elif line.startswith("+ "):
            parts = line[2:].rsplit(" - ", 1)
            if len(parts) == 2:
                ops.append(
                    PlanOperation(
                        action="add",
                        track_title=parts[0].strip(),
                        track_artist=parts[1].strip(),
                    )
                )
            else:
                ops.append(PlanOperation(action="add", track_title=line[2:].strip()))
        elif line.startswith("= "):
            # Move operation: = Title - Artist -> target
            if " -> " not in line:
                continue
            left, target = line[2:].rsplit(" -> ", 1)
            parts = left.rsplit(" - ", 1)
            title = parts[0].strip() if parts else left.strip()
            artist = parts[1].strip() if len(parts) == 2 else ""
            target = target.strip()

            if target.startswith("pos:"):
                pos = int(target[4:])
                ops.append(
                    PlanOperation(
                        action="assign_section",
                        track_title=title,
                        track_artist=artist,
                        position=pos,
                        reason=f"move to position {pos}",
                    )
                )
            elif target.startswith("sec:"):
                sec_parts = target[4:].split(":", 1)
                section = sec_parts[0]
                sec_pos = int(sec_parts[1]) if len(sec_parts) > 1 else None
                ops.append(
                    PlanOperation(
                        action="assign_section",
                        track_title=title,
                        track_artist=artist,
                        section_name=section,
                        position=sec_pos,
                        reason=f"move to section {section}"
                        + (f" position {sec_pos}" if sec_pos else ""),
                    )
                )
    return ops


def apply_plan(db: Database, plan: BatchPlan) -> tuple[int, int]:
    """Apply a plan: execute ALL local DB changes first, then sync.

    DB changes are atomic (all happen or none). Platform sync happens after
    and failures don't affect local state. History records only what actually
    executed.

    Returns (removals_applied, additions_applied).
    """
    removed = 0
    added = 0
    executed_ops: list[dict] = []

    # Phase 1: Execute ALL local DB changes (no platform sync yet)
    for op in plan.removals:
        if op.track_id is None:
            continue
        tracks = db.get_playlist_tracks(plan.playlist_id)
        track = next((t for t in tracks if t.id == op.track_id), None)
        if track is None:
            continue
        position = next((i for i, t in enumerate(tracks) if t.id == op.track_id), 0)
        db.remove_track_from_playlist(plan.playlist_id, track.id)
        removed += 1
        executed_ops.append(
            {
                "action": "rm",
                "track": op.track_title,
                "artist": op.track_artist,
                "track_id": op.track_id,
                "reason": op.reason,
                "position": position,
                "previous_position": position,
                "previous_section": op.previous_section,
            }
        )

    failed_additions: list[str] = []
    for op in plan.additions:
        if not op.track_title:
            continue
        tracks_found = db.find_tracks_by_title_artist(op.track_title, op.track_artist)
        if tracks_found:
            track = tracks_found[0]
            existing = db.get_playlist_tracks(plan.playlist_id)
            if any(t.id == track.id for t in existing):
                failed_additions.append(
                    f'"{op.track_title}" by {op.track_artist} (already in playlist)'
                )
                continue
            next_pos = len(existing)
            db.conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                (plan.playlist_id, track.id, next_pos),
            )
            db.conn.commit()
            added += 1
            executed_ops.append(
                {
                    "action": "add",
                    "track": op.track_title,
                    "artist": op.track_artist,
                    "track_id": track.id,
                    "reason": op.reason,
                    "position": next_pos,
                    "previous_position": None,
                    "previous_section": None,
                }
            )
        else:
            failed_additions.append(
                f'"{op.track_title}" by {op.track_artist} (not found in library)'
            )

    if failed_additions:
        print(f"  Failed additions ({len(failed_additions)}):")
        for f in failed_additions:
            print(f"    - {f}")

    # Handle split operations (create_playlist + move_to_playlist)
    new_playlist_id = None
    moved = 0
    for op in plan.operations:
        if op.action == "create_playlist" and op.target_name:
            existing = db.find_playlist_by_name(op.target_name)
            if existing:
                new_playlist_id = existing.id
            else:
                new_playlist_id = db.create_playlist(op.target_name)
            executed_ops.append(
                {
                    "action": "create_playlist",
                    "track": "",
                    "artist": "",
                    "track_id": None,
                    "reason": op.reason,
                    "position": None,
                    "previous_position": None,
                    "previous_section": None,
                    "target_name": op.target_name,
                }
            )
        elif op.action == "move_to_playlist" and op.track_id and new_playlist_id:
            new_tracks = db.get_playlist_tracks(new_playlist_id)
            next_pos = len(new_tracks)
            db.add_track_to_playlist(new_playlist_id, op.track_id, next_pos)
            db.remove_track_from_playlist(plan.playlist_id, op.track_id)
            moved += 1
            executed_ops.append(
                {
                    "action": "move_to_playlist",
                    "track": op.track_title,
                    "artist": op.track_artist,
                    "track_id": op.track_id,
                    "reason": op.reason,
                    "position": op.position,
                    "previous_position": op.previous_position,
                    "previous_section": None,
                    "target_name": op.target_name,
                }
            )

    # Handle set_narrative (for --structure)
    for op in plan.operations:
        if op.action == "set_narrative" and op.target_name:
            # target_name holds the narrative text for set_narrative ops
            old_narrative = db.get_narrative(plan.playlist_id)
            db.set_narrative(plan.playlist_id, op.target_name)
            executed_ops.append(
                {
                    "action": "set_narrative",
                    "track": "",
                    "artist": "",
                    "track_id": None,
                    "reason": op.reason,
                    "position": None,
                    "previous_position": None,
                    "previous_section": old_narrative,
                    "target_name": op.target_name,
                }
            )

    # Phase 2: Record ONLY what actually executed in history
    if executed_ops:
        db.record_batch(
            plan.playlist_id,
            json.dumps(
                {
                    "playlist": plan.playlist_name,
                    "created": plan.created_at,
                    "operations": executed_ops,
                }
            ),
        )

    # Phase 3: Auto-reorder if enabled
    playlist_row = db.conn.execute(
        "SELECT auto_reorder, reorder_arc FROM playlists WHERE id = ?",
        (plan.playlist_id,),
    ).fetchone()
    if playlist_row and playlist_row[0]:
        from tuneshift.sequencer.optimizer import sequence_playlist

        arc = playlist_row[1] or "wave"
        sequence_playlist(db, plan.playlist_id, arc=arc)

    # Phase 4: Report sync instructions
    if removed or added:
        print(
            f'  Run `tuneshift sync "{plan.playlist_name}" <platform>` to push changes.'
        )

    return removed, added


def render_plan(plan: BatchPlan) -> str:
    """Render a plan as human-readable text."""
    lines: list[str] = []
    lines.append(f'Plan for "{plan.playlist_name}" (created {plan.created_at})')
    lines.append("")

    if plan.removals:
        lines.append(f"REMOVE ({len(plan.removals)}):")
        for op in plan.removals:
            lines.append(f'  - "{op.track_title}" by {op.track_artist}')
            lines.append(f"    Reason: {op.reason}")
        lines.append("")

    moves = [op for op in plan.operations if op.action == "move_to_playlist"]
    if moves:
        target = moves[0].target_name or "?"
        lines.append(f'MOVE TO "{target}" ({len(moves)}):')
        for op in moves:
            lines.append(f'  - "{op.track_title}" by {op.track_artist}')
        lines.append("")

    if plan.keeps:
        lines.append(f"KEEP ({len(plan.keeps)}):")
        for op in plan.keeps:
            lines.append(f'  - "{op.track_title}" by {op.track_artist}')
            lines.append(f"    Reason: {op.reason}")
        lines.append("")

    if plan.additions:
        lines.append(f"ADD ({len(plan.additions)}):")
        for op in plan.additions:
            lines.append(f'  - "{op.track_title}" by {op.track_artist}')
            lines.append(f"    Reason: {op.reason}")
        lines.append("")

    summary = []
    if plan.removals:
        summary.append(f"{len(plan.removals)} removal(s)")
    if moves:
        summary.append(f"{len(moves)} move(s)")
    if plan.additions:
        summary.append(f"{len(plan.additions)} addition(s)")
    if plan.keeps:
        summary.append(f"{len(plan.keeps)} keep(s)")

    section_assigns = [op for op in plan.operations if op.action == "assign_section"]
    if section_assigns:
        by_section: dict[str, list[PlanOperation]] = {}
        for op in section_assigns:
            by_section.setdefault(op.section_name or "?", []).append(op)
        lines.append(f"STRUCTURE ({len(section_assigns)} assignments):")
        for section_name, section_ops in by_section.items():
            lines.append(f"  [{section_name}] ({len(section_ops)} tracks):")
            for op in section_ops[:5]:
                lines.append(f"    - {op.track_title} - {op.track_artist}")
            if len(section_ops) > 5:
                lines.append(f"    ... +{len(section_ops) - 5} more")
        lines.append("")
        summary.append(f"{len(section_assigns)} section assignment(s)")

    lines.append(f"Summary: {', '.join(summary) if summary else 'no changes'}")

    return "\n".join(lines)


def _interactive_dedupe(
    db: Database, playlist_id: int, cap: int
) -> list[PlanOperation]:
    """Walk through dedupe decisions one artist at a time."""
    tracks = db.get_playlist_tracks(playlist_id)
    by_artist: dict[str, list] = {}
    for i, t in enumerate(tracks):
        by_artist.setdefault(t.artist, []).append((i, t))

    ops: list[PlanOperation] = []
    for artist, entries in sorted(by_artist.items()):
        if len(entries) <= cap:
            continue
        entries.sort(key=lambda e: e[0])

        print(f"\n{artist} has {len(entries)} tracks (cap: {cap}). Keep which?")
        for idx, (pos, track) in enumerate(entries):
            print(f"  {idx + 1}. {track.title}")

        choices_raw = input(
            f"Keep (1-{len(entries)}, comma-separated, or 'all'/'none'): "
        ).strip()
        if choices_raw.lower() == "all":
            continue
        if choices_raw.lower() == "none":
            keep_indices: set[int] = set()
        else:
            try:
                keep_indices = {int(c.strip()) - 1 for c in choices_raw.split(",")}
            except ValueError:
                print("  Invalid input, keeping all.")
                continue

        for idx, (pos, track) in enumerate(entries):
            if idx in keep_indices:
                ops.append(
                    PlanOperation(
                        action="keep",
                        track_title=track.title,
                        track_artist=track.artist,
                        track_id=track.id,
                        reason=f"dedupe cap={cap}, curator choice",
                        position=pos,
                    )
                )
            else:
                ops.append(
                    PlanOperation(
                        action="rm",
                        track_title=track.title,
                        track_artist=track.artist,
                        track_id=track.id,
                        reason=f"dedupe cap={cap}, {artist} has {len(entries)} tracks",
                        position=pos,
                    )
                )
    return ops


def undo_batch(db: Database, history_id: int | None = None) -> bool:
    """Undo a specific batch plan by reversing its operations.

    If history_id is None, undoes the most recent non-reverted plan.
    Returns True if successful.
    """
    if history_id is None:
        row = db.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history "
            "WHERE reverted_at IS NULL ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
    else:
        row = db.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history WHERE id = ?",
            (history_id,),
        ).fetchone()

    if row is None:
        return False

    hid, playlist_id, plan_json_str = row[0], row[1], row[2]
    plan_data = json.loads(plan_json_str)

    # Reverse each operation
    for op in plan_data.get("operations", []):
        if op["action"] == "rm" and op.get("track_id"):
            # Re-add the track at its previous position
            pos = op.get("previous_position", op.get("position"))
            existing = db.get_playlist_tracks(playlist_id)
            insert_pos = min(pos, len(existing)) if pos is not None else len(existing)
            # Shift positions down to make room (from end to avoid PK conflicts)
            max_pos = (
                db.conn.execute(
                    "SELECT MAX(position) FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()[0]
                or 0
            )
            for shift_pos in range(max_pos, insert_pos - 1, -1):
                db.conn.execute(
                    "UPDATE playlist_tracks SET position = ? "
                    "WHERE playlist_id = ? AND position = ?",
                    (shift_pos + 1, playlist_id, shift_pos),
                )
            db.conn.execute(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) "
                "VALUES (?, ?, ?)",
                (playlist_id, op["track_id"], insert_pos),
            )
        elif op["action"] == "add" and op.get("track_id"):
            # Remove the track that was added
            db.conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, op["track_id"]),
            )
            # Re-compact positions
            tracks = db.get_playlist_tracks(playlist_id)
            for i, t in enumerate(tracks):
                db.conn.execute(
                    "UPDATE playlist_tracks SET position = ? "
                    "WHERE playlist_id = ? AND track_id = ?",
                    (i, playlist_id, t.id),
                )

    db.conn.commit()
    db.mark_batch_reverted(hid)
    return True


def handle_batch(args, db: Database) -> int:
    """Handle batch operations: plan, show, apply, discard, undo, history."""
    import sys

    # Show current plan
    if getattr(args, "show_plan", False):
        plan = BatchPlan.load()
        if plan is None:
            print(
                "No plan exists. Create one with: tuneshift batch <playlist> --<operation> --plan"
            )
            return 1
        print(render_plan(plan))
        return 0

    # Discard current plan
    if getattr(args, "discard", False):
        if BatchPlan.discard():
            print("Plan discarded.")
        else:
            print("No plan to discard.")
        return 0

    # History
    if getattr(args, "history", False):
        playlist_name = args.playlist or args.history
        playlist = (
            db.find_playlist_by_name(playlist_name)
            if isinstance(playlist_name, str)
            else None
        )
        if playlist is None:
            # Show all history
            rows = db.conn.execute(
                "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
                "FROM batch_history ORDER BY applied_at DESC LIMIT 20"
            ).fetchall()
        else:
            rows = db.conn.execute(
                "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
                "FROM batch_history WHERE playlist_id = ? ORDER BY applied_at DESC",
                (playlist.id,),
            ).fetchall()

        if not rows:
            print("No batch history found.")
            return 0

        for row in rows:
            plan_data = json.loads(row[4])
            status = "REVERTED" if row[3] else "active"
            op_count = len(plan_data.get("operations", []))
            rm_count = sum(
                1 for o in plan_data.get("operations", []) if o["action"] == "rm"
            )
            print(
                f"  #{row[0]} [{status}] {row[2]} - {plan_data.get('playlist', '?')} "
                f"({op_count} ops, {rm_count} removals)"
            )
        return 0

    # Undo
    if getattr(args, "undo", False):
        undo_id = getattr(args, "id", None)
        if undo_batch(db, undo_id):
            print(f"Undone: plan #{undo_id or 'last'} reversed.")
        else:
            print("Nothing to undo.", file=sys.stderr)
            return 1
        return 0

    # Apply current plan
    if getattr(args, "apply", False):
        plan = BatchPlan.load()
        if plan is None:
            print("No plan to apply. Create one first.", file=sys.stderr)
            return 1
        print(render_plan(plan))
        print()
        confirm = input("Apply this plan? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Cancelled.")
            return 0
        removed, added = apply_plan(db, plan)
        print(f"\nApplied: {removed} removed, {added} added")
        BatchPlan.discard()
        return 0

    # Sweep banned (works with or without playlist)
    if getattr(args, "sweep_banned", False):
        playlist = db.find_playlist_by_name(args.playlist) if args.playlist else None
        results = plan_sweep_banned(db, playlist.id if playlist else None)
        if not results:
            print("No banned artists found.")
            return 0

        # For single playlist, create one plan
        if playlist:
            ops = results.get(playlist.id, [])
            plan = BatchPlan(
                playlist_name=playlist.name, playlist_id=playlist.id, operations=ops
            )
            print(render_plan(plan))
            if getattr(args, "plan", False):
                plan.save()
                print("\nPlan saved. Apply with: tuneshift batch --apply")
            return 0

        # Multi-playlist: show summary
        total_ops = sum(len(ops) for ops in results.values())
        print(
            f"Banned artist sweep: {total_ops} tracks across {len(results)} playlists"
        )
        for pid, ops in results.items():
            pl_name = db.conn.execute(
                "SELECT name FROM playlists WHERE id = ?", (pid,)
            ).fetchone()[0]
            print(f"  {pl_name}: {len(ops)} tracks")
            for op in ops:
                print(f'    - "{op.track_title}" by {op.track_artist} ({op.reason})')
        if getattr(args, "plan", False):
            # Save the first playlist's plan (multi-playlist sweep applies sequentially)
            first_pid = next(iter(results))
            first_name = db.conn.execute(
                "SELECT name FROM playlists WHERE id = ?", (first_pid,)
            ).fetchone()[0]
            plan = BatchPlan(
                playlist_name=first_name,
                playlist_id=first_pid,
                operations=results[first_pid],
            )
            plan.save()
            print(
                f'\nSaved plan for "{first_name}". Apply sequentially with: tuneshift batch --apply'
            )
        return 0

    # Generate a plan (requires playlist name for most operations)
    if not args.playlist:
        print("Playlist name required for plan generation.", file=sys.stderr)
        return 1

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    # Check mutual exclusivity
    if getattr(args, "interactive", False) and getattr(args, "from_stdin", False):
        print("--interactive and --from-stdin are mutually exclusive.", file=sys.stderr)
        return 1

    ops: list[PlanOperation] = []

    # Multi-rm/add from CLI flags
    for rm_title in getattr(args, "rm", None) or []:
        parts = rm_title.rsplit(" - ", 1)
        title = parts[0].strip()
        artist = parts[1].strip() if len(parts) == 2 else ""
        # Find matching track
        tracks = db.get_playlist_tracks(playlist.id)
        for i, t in enumerate(tracks):
            if (
                t.title.casefold() == title.casefold()
                or title.casefold() in t.title.casefold()
            ):
                if not artist or t.artist.casefold() == artist.casefold():
                    ops.append(
                        PlanOperation(
                            action="rm",
                            track_title=t.title,
                            track_artist=t.artist,
                            track_id=t.id,
                            position=i,
                            previous_position=i,
                            reason="CLI --rm",
                        )
                    )
                    break

    for add_spec in getattr(args, "add", None) or []:
        parts = add_spec.rsplit(" - ", 1)
        title = parts[0].strip()
        artist = parts[1].strip() if len(parts) == 2 else ""
        ops.append(
            PlanOperation(
                action="add", track_title=title, track_artist=artist, reason="CLI --add"
            )
        )

    # Plan file input
    plan_file = getattr(args, "plan_file", None)
    if plan_file:
        content = Path(plan_file).read_text()
        ops.extend(parse_plan_file(content))

    # Stdin input
    if getattr(args, "from_stdin", False):
        import sys as _sys

        if not _sys.stdin.isatty():
            content = _sys.stdin.read()
            ops.extend(parse_plan_file(content))

    # Existing operations
    if getattr(args, "dedupe", False):
        cap = getattr(args, "cap", 1)
        if getattr(args, "interactive", False):
            ops.extend(_interactive_dedupe(db, playlist.id, cap))
        else:
            ops.extend(plan_dedupe(db, playlist.id, cap))

    if getattr(args, "rm_artist", None):
        ops.extend(plan_rm_artist(db, playlist.id, args.rm_artist))

    # Build the concept judge at most once per command; both review-findings and
    # a non-fresh rebuild reuse it (rebuild --fresh clears without judging).
    concept_judge = None
    needs_judge = getattr(args, "review_findings", False) or (
        getattr(args, "rebuild", False) and not getattr(args, "fresh", False)
    )
    if needs_judge:
        from tuneshift.composer.concept_llm import make_concept_judge

        concept_judge = make_concept_judge()

    if getattr(args, "review_findings", False):
        ops.extend(plan_review_fixes(db, playlist.id, llm_judge=concept_judge))

    # Split operation
    split_name = getattr(args, "split", None)
    if split_name:
        filters = getattr(args, "filter", None) or []
        if not filters:
            print(
                "--split requires --filter to specify which tracks to move.",
                file=sys.stderr,
            )
            return 1
        ops.extend(plan_split(db, playlist.id, split_name, filters))

    # Rebuild
    if getattr(args, "rebuild", False):
        count = getattr(args, "count", 50)
        fresh = getattr(args, "fresh", False)
        ops.extend(
            plan_rebuild(
                db,
                playlist.id,
                count,
                fresh=fresh,
                llm_judge=None if fresh else concept_judge,
            )
        )

    # Retroactive narrative structuring
    if getattr(args, "structure", False):
        narrative_file = getattr(args, "narrative_file", None)
        if narrative_file:
            ops.extend(plan_structure_from_file(db, playlist.id, narrative_file))
        else:
            # LLM mode: propose sections
            from tuneshift.sequencer.classifier import TrackClassifier

            classifier = TrackClassifier()
            if not classifier.available:
                print(
                    "--structure without --narrative-file requires an LLM backend.",
                    file=sys.stderr,
                )
                print(
                    "Configure with: tuneshift config anthropic-key <key>",
                    file=sys.stderr,
                )
                print(
                    "Or provide sections: --structure --narrative-file arc.txt",
                    file=sys.stderr,
                )
                return 1
            structure_ops = plan_structure_llm(db, playlist.id, classifier)
            if structure_ops is None:
                return 1
            ops.extend(structure_ops)

    if not ops:
        print("No changes needed.")
        return 0

    # Resolve track_ids for ops that only have title/artist (from plan files/stdin/CLI)
    tracks = db.get_playlist_tracks(playlist.id)
    for op in ops:
        if op.track_id is None and op.action == "rm" and op.track_title:
            title_lower = op.track_title.casefold()
            for i, t in enumerate(tracks):
                if title_lower in t.title.casefold():
                    if (
                        not op.track_artist
                        or op.track_artist.casefold() in t.artist.casefold()
                    ):
                        op.track_id = t.id
                        op.position = i
                        op.previous_position = i
                        break

    plan = BatchPlan(
        playlist_name=playlist.name,
        playlist_id=playlist.id,
        operations=ops,
    )

    print(render_plan(plan))

    if getattr(args, "plan", False):
        plan_path = plan.save()
        print(f"\nPlan saved to {plan_path}")
        print("Review with: tuneshift batch --show-plan")
        print("Apply with:  tuneshift batch --apply")
        print("Discard:     tuneshift batch --discard")
    else:
        print()
        confirm = input("Apply now? [y/N] ").strip().lower()
        if confirm in ("y", "yes"):
            removed, added = apply_plan(db, plan)
            print(f"\nApplied: {removed} removed, {added} added")
        else:
            plan_path = plan.save()
            print(f"Plan saved to {plan_path}. Apply with: tuneshift batch --apply")

    return 0


def handle_merge(args, db: Database) -> int:
    """Handle the merge command: combine multiple playlists into one."""
    source_names = args.sources
    into_name = args.into

    # Resolve all playlists
    source_playlists = []
    for name in source_names:
        p = db.find_playlist_by_name(name)
        if not p:
            print(f"Playlist not found: {name}", file=sys.stderr)
            return 1
        source_playlists.append(p)

    into_playlist = db.find_playlist_by_name(into_name)
    if not into_playlist:
        # Create target
        into_id = db.create_playlist(into_name)
        print(f"Created target playlist: {into_name}")
    else:
        into_id = into_playlist.id

    source_ids = [p.id for p in source_playlists]
    ops = plan_merge(db, source_ids, into_id)

    if not ops:
        print("No unique tracks to merge (all duplicates).")
        return 0

    plan = BatchPlan(playlist_name=into_name, playlist_id=into_id, operations=ops)
    print(render_plan(plan))

    if getattr(args, "plan", False):
        plan.save()
        print("\nPlan saved. Apply with: tuneshift batch --apply")
        return 0

    confirm = input("\nApply merge? [y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        plan.save()
        print("Plan saved. Apply with: tuneshift batch --apply")
        return 0

    removed, added = apply_plan(db, plan)
    print(f'\nMerged: {added} tracks added to "{into_name}"')

    if getattr(args, "delete_sources", False):
        for p in source_playlists:
            if p.id != into_id:
                db.conn.execute("DELETE FROM playlists WHERE id = ?", (p.id,))
                print(f"  Deleted source playlist: {p.name}")
        db.conn.commit()

    return 0


def plan_rebuild(
    db: Database, playlist_id: int, count: int, fresh: bool = False, llm_judge=None
) -> list[PlanOperation]:
    """Plan a concept-driven rebuild: review + fill from library.

    1. Evaluate existing tracks against concept
    2. Keep passes, remove failures
    3. Fill to target count from library (Tier 1)

    Concept evaluation uses the same rule routing as ``review`` (artist-tag +
    era deterministically, thematic via ``llm_judge`` when supplied) and
    suppresses accepted (track, rule) pairs so an accepted track is kept.
    """
    from tuneshift.commands.compose_cmd import _build_artist_lookup, _get_concept
    from tuneshift.composer.reviewer import review_playlist
    from tuneshift.sequencer.metadata import track_to_metadata

    concept = _get_concept(db, playlist_id)
    tracks_raw = db.get_playlist_tracks(playlist_id)
    tracks = [track_to_metadata(t) for t in tracks_raw]
    artist_lookup = _build_artist_lookup(db, playlist_id)

    ops: list[PlanOperation] = []

    if fresh:
        # Remove everything
        for i, t in enumerate(tracks_raw):
            ops.append(
                PlanOperation(
                    action="rm",
                    track_title=t.title,
                    track_artist=t.artist,
                    track_id=t.id,
                    position=i,
                    previous_position=i,
                    reason="rebuild --fresh: clearing playlist",
                )
            )
        keep_ids: set[int] = set()
    else:
        # Review and keep/remove based on concept
        findings = (
            review_playlist(
                tracks,
                concept=concept,
                artist_lookup=artist_lookup,
                year_lookup=db.get_release_years_for_playlist(playlist_id),
                llm_judge=llm_judge,
                accepted=db.get_concept_acceptances(playlist_id),
            )
            if concept
            else []
        )
        violation_ids: set[int] = set()

        for finding in findings:
            if finding.severity >= 0.8:
                import re as _re

                match = _re.search(r'"([^"]+)" by (.+?) - Rule:', finding.description)
                if match:
                    title, artist = match.group(1), match.group(2)
                    for i, t in enumerate(tracks_raw):
                        if t.title == title and t.artist == artist:
                            ops.append(
                                PlanOperation(
                                    action="rm",
                                    track_title=t.title,
                                    track_artist=t.artist,
                                    track_id=t.id,
                                    position=i,
                                    previous_position=i,
                                    reason=finding.description,
                                )
                            )
                            violation_ids.add(t.id)
                            break

        keep_ids = {t.id for t in tracks_raw} - violation_ids

    # Fill from library (Tier 1)
    current_count = len(keep_ids)
    needed = max(0, count - current_count)

    if needed > 0 and concept:
        # Search library for tracks that fit the concept
        keywords = []
        if concept.theme:
            keywords.extend(concept.theme.split(","))
        keywords.extend(concept.genres)

        candidates = db.search_tracks_by_metadata(keywords=keywords, limit=needed * 3)

        # Filter: not already in playlist, passes concept rules
        playlist_track_ids = {t.id for t in tracks_raw}
        added_count = 0
        for candidate in candidates:
            if candidate.id in playlist_track_ids or candidate.id in keep_ids:
                continue
            # Check banned
            banned = check_track_against_bans(db, candidate.title, candidate.artist)
            if banned:
                continue
            # Check concept hard rules (artist identity)
            if concept.hard_rules:
                artist = db.get_artist_by_name(candidate.artist)
                if artist:
                    from tuneshift.composer.reviewer import _check_rule_against_artist

                    passes = all(
                        _check_rule_against_artist(rule, artist) is not False
                        for rule in concept.hard_rules
                    )
                    if not passes:
                        continue

            ops.append(
                PlanOperation(
                    action="add",
                    track_title=candidate.title,
                    track_artist=candidate.artist,
                    track_id=candidate.id,
                    reason=f"rebuild fill: library match (concept: {concept.theme})",
                )
            )
            added_count += 1
            if added_count >= needed:
                break

        if added_count < needed:
            shortfall = needed - added_count
            print(
                f"  Note: filled {current_count + added_count}/{count} "
                f"({shortfall} unfilled: insufficient matching tracks in library)"
            )

    return ops


def plan_structure_from_file(
    db: Database, playlist_id: int, narrative_file: str
) -> list[PlanOperation]:
    """Plan track assignment to sections from a user-provided narrative file.

    Assigns tracks by fitness (energy/mood/stance alignment).
    """
    from tuneshift.composer.matcher import match_tracks_to_sections
    from tuneshift.composer.parser import parse_enhanced_narrative
    from tuneshift.sequencer.metadata import track_to_metadata

    content = Path(narrative_file).read_text()
    tracks_raw = db.get_playlist_tracks(playlist_id)
    tracks = [track_to_metadata(t) for t in tracks_raw]
    tracklist = [t.title for t in tracks]

    sections = parse_enhanced_narrative(content, tracklist=tracklist)
    if not sections:
        print("  No sections could be parsed from the narrative file.", file=sys.stderr)
        return []

    assignments = match_tracks_to_sections(tracks, sections, concept=None)

    ops: list[PlanOperation] = []

    # Set narrative operation
    ops.append(
        PlanOperation(
            action="set_narrative",
            reason="retroactive structuring from file",
            target_name=content.strip(),
        )
    )

    # Assign section operations (for rendering)
    for section in sections:
        section_tracks = assignments.assignments.get(section.name, [])
        for track in section_tracks:
            ops.append(
                PlanOperation(
                    action="assign_section",
                    track_title=track.title,
                    track_artist=track.artist,
                    track_id=track.track_id,
                    section_name=section.name,
                    reason=f"assigned to {section.name} by fitness",
                )
            )

    return ops


_STRUCTURE_PROMPT = """You are a music playlist curator. Given a tracklist, propose a narrative arc structure.

TRACKLIST ({track_count} tracks):
{tracklist}

{concept_context}

Create a narrative arc with 4-7 sections. Use this EXACT format (one section per line):

SECTION_NAME (start-end): Description of mood/energy/theme. Mention key tracks in (parentheses).

Rules:
- Section names are ALL CAPS with underscores (e.g., OPENER, BUILD, PEAK, COMEDOWN, CLOSER)
- Position ranges must be 1-based and cover all {track_count} tracks with no gaps
- Descriptions should capture the energy, mood, and flow of that section
- Mention 2-3 key tracks per section in parentheses

Example output:
OPENER (1-3): High energy invitation to the party. (Track A), (Track B).
BUILD (4-12): Rising intensity, the groove settles in. (Track C), (Track D).
PEAK (13-20): Maximum energy, the main event. (Track E), (Track F).
COMEDOWN (21-25): Cooling down. (Track G).
CLOSER (26-28): Final emotional statement. (Track H).

Respond with ONLY the section definitions, nothing else."""


def plan_structure_llm(db, playlist_id: int, classifier) -> list[PlanOperation] | None:
    """Use LLM to propose narrative sections for a playlist."""
    from tuneshift.commands.compose_cmd import _get_concept
    from tuneshift.composer.matcher import match_tracks_to_sections
    from tuneshift.composer.parser import parse_enhanced_narrative
    from tuneshift.sequencer.metadata import track_to_metadata

    tracks_raw = db.get_playlist_tracks(playlist_id)
    tracks = [track_to_metadata(t) for t in tracks_raw]
    track_count = len(tracks_raw)

    # Build tracklist string
    tracklist_lines = []
    for i, t in enumerate(tracks_raw):
        tracklist_lines.append(f"  {i + 1}. {t.title} - {t.artist}")
    tracklist_str = "\n".join(tracklist_lines)

    # Concept context
    concept = _get_concept(db, playlist_id)
    concept_context = ""
    if concept:
        concept_context = f"PLAYLIST CONCEPT: {concept.theme}"
        if concept.hard_rules:
            concept_context += f"\nRules: {', '.join(concept.hard_rules)}"

    prompt = _STRUCTURE_PROMPT.format(
        track_count=track_count,
        tracklist=tracklist_str,
        concept_context=concept_context,
    )

    print(f"  Generating narrative structure via {classifier.backend_info}...")
    try:
        response = classifier._backend.complete(
            prompt, classifier._model, max_tokens=2000
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"  LLM error: {exc}", file=sys.stderr)
        return None

    # Parse the response as a narrative
    tracklist_names = [t.title for t in tracks]
    sections = parse_enhanced_narrative(response, tracklist=tracklist_names)

    if not sections:
        print("  LLM response could not be parsed as sections.", file=sys.stderr)
        print("  Raw response:", file=sys.stderr)
        print(f"  {response[:500]}", file=sys.stderr)
        print("  Try --narrative-file with manually written sections.", file=sys.stderr)
        return None

    print(f"  Proposed {len(sections)} sections:")
    for s in sections:
        print(
            f"    {s.name} ({s.start_position}-{s.end_position}): {s.description[:60]}..."
        )

    # Assign tracks to sections by fitness
    assignments = match_tracks_to_sections(tracks, sections, concept=concept)

    ops: list[PlanOperation] = []

    # Set narrative
    ops.append(
        PlanOperation(
            action="set_narrative",
            reason="LLM-proposed narrative structure",
            target_name=response.strip(),
        )
    )

    # Section assignments
    for section in sections:
        section_tracks = assignments.assignments.get(section.name, [])
        for track in section_tracks:
            ops.append(
                PlanOperation(
                    action="assign_section",
                    track_title=track.title,
                    track_artist=track.artist,
                    track_id=track.track_id,
                    section_name=section.name,
                    reason=f"LLM assigned to {section.name}",
                )
            )

    return ops
