# shellcheck shell=bash
"""Import-text command: load playlist from a text file into the DB."""

import re
import sys
from pathlib import Path

from tuneshift.db import Database
from tuneshift.models import Track

_TRACK_RE = re.compile(
    r"^\s*\d+\.\s+"
    r"(?P<artist>.+?)\s+-\s+(?P<title>.+?)"
    r"(?:\s+\[(?P<album>.+?)\])?\s*$"
)
_HEADER_PLAYLIST_ID_RE = re.compile(r"#\s*Tidal playlist ID:\s*(.+)")


def handle_import_text(args, db: Database) -> int:
    """Import a playlist from a text file into the canonical database."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    lines = file_path.read_text(encoding="utf-8").splitlines()
    playlist_name, tidal_id, tracks = _parse_playlist_file(lines)

    if not playlist_name:
        print("Could not determine playlist name from file.", file=sys.stderr)
        return 1

    if not tracks:
        print(f"No tracks found in {file_path.name}.", file=sys.stderr)
        return 1

    # Override name if provided
    if getattr(args, "name", None):
        playlist_name = args.name

    # Create or find playlist
    existing = db.find_playlist_by_name(playlist_name)
    if existing:
        playlist_id = existing.id
        if not getattr(args, "force", False):
            print(
                f'Playlist "{playlist_name}" already exists ({len(db.get_playlist_tracks(playlist_id))} tracks).'  # noqa: E501
            )
            print("Use --force to overwrite.")
            return 1
        # Clear existing tracks
        db.clear_playlist_tracks(playlist_id)
    else:
        playlist_id = db.create_playlist(playlist_name)

    # Link Tidal playlist ID if found in header
    if tidal_id:
        db.link_platform_playlist(playlist_id, "tidal", tidal_id)

    # Add tracks
    new_count = 0
    for position, (title, artist, album) in enumerate(tracks, 1):
        existing_track = db.find_track(title, artist, album)
        if existing_track:
            track_id = existing_track.id
        else:
            track = Track(title=title, artist=artist, album=album)
            track_id = db.add_track(track)
            new_count += 1
        db.add_track_to_playlist(playlist_id, track_id, position)
        # Library-first (AC-D7): enqueue async resolution/enrichment; never
        # block the import on network. Idempotent per track.
        db.enqueue_resolution(track_id)

    print(f'Imported "{playlist_name}": {len(tracks)} tracks ({new_count} new)')
    return 0


def _parse_playlist_file(
    lines: list[str],
) -> tuple[str | None, str | None, list[tuple[str, str, str | None]]]:
    """Parse a playlist text file. Returns (name, tidal_id, [(title, artist, album)])."""  # noqa: E501
    name = None
    tidal_id = None
    tracks: list[tuple[str, str, str | None]] = []

    for line in lines:
        # Header lines
        if line.startswith("#"):
            stripped = line.lstrip("# ").strip()
            if (
                name is None
                and stripped
                and not stripped[0].isdigit()
                and "track" not in stripped.lower()
                and "playlist" not in stripped.lower()
            ):
                name = stripped
            m = _HEADER_PLAYLIST_ID_RE.match(line)
            if m:
                tidal_id = m.group(1).strip()
            continue

        # Track lines
        m = _TRACK_RE.match(line)
        if m:
            tracks.append((m.group("title"), m.group("artist"), m.group("album")))

    return name, tidal_id, tracks
