"""Narrative command: set or display the intended narrative arc for a playlist."""

import sys
from pathlib import Path

from tuneshift.db import Database


def handle_narrative(args, db: Database) -> int:
    """Set, show, or clear the playlist narrative."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    if getattr(args, "clear", False):
        db.set_narrative(playlist.id, None)
        print(f'Cleared narrative for "{playlist.name}"')
        return 0

    # Read from file if specified
    if getattr(args, "file", None):
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {args.file}", file=sys.stderr)
            return 1
        text = path.read_text().strip()
        db.set_narrative(playlist.id, text)
        print(f'Set narrative for "{playlist.name}" from {args.file}')
        return 0

    # Set from positional text
    if getattr(args, "text", None):
        db.set_narrative(playlist.id, args.text)
        print(f'Set narrative for "{playlist.name}"')
        return 0

    # Show current narrative
    narrative = db.get_narrative(playlist.id)
    if narrative:
        print(f'Narrative for "{playlist.name}":\n')
        print(narrative)
    else:
        print(f'No narrative set for "{playlist.name}". Use:')
        print(f'  tuneshift narrative "{playlist.name}" "Your narrative here..."')
        print(f'  tuneshift narrative "{playlist.name}" -f narrative.txt')

    return 0
