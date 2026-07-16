"""Status command: show playlist info and sync state."""

import sys

from tuneshift.db import Database


def handle_status(args, db: Database) -> int:
    """Show playlist status information."""
    if args.playlist:
        playlist = db.find_playlist_by_name(args.playlist)
        if not playlist:
            print(f"Playlist not found: {args.playlist}", file=sys.stderr)
            return 1
        _show_playlist_status(db, playlist)
    else:
        # Show all playlists
        playlists = db.list_playlists()
        if not playlists:
            print(
                "No playlists. Use 'tuneshift ingest' or 'tuneshift add' to create one."
            )
            return 0
        for pl in playlists:
            _show_playlist_status(db, pl)
            print()
    return 0


def _show_playlist_status(db, playlist) -> None:
    """Print status for a single playlist."""
    tracks = db.get_playlist_tracks(playlist.id)
    platforms = db.get_linked_platforms(playlist.id)

    print(f"  {playlist.name}")
    print(f"    Tracks: {len(tracks)}")
    if platforms:
        print("    Platforms:")
        for platform in platforms:
            last_synced = db.get_last_synced(playlist.id, platform)
            state = (
                f"last synced {last_synced}"
                if last_synced
                else "push pending (never synced)"
            )
            print(f"      {platform}: {state}")
    else:
        print("    Platforms: (none linked)")


def handle_list(args, db: Database) -> int:  # noqa: ARG001 - required by CLI subcommand handler signature
    """List all playlists."""
    playlists = db.list_playlists()
    if not playlists:
        print("No playlists.")
        return 0
    for pl in playlists:
        tracks = db.get_playlist_tracks(pl.id)
        platforms = db.get_linked_platforms(pl.id)
        platform_str = f" [{', '.join(platforms)}]" if platforms else ""
        print(f"  {pl.name} ({len(tracks)} tracks){platform_str}")
    return 0
