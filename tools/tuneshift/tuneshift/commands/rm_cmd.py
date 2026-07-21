"""Remove command: remove a track from a playlist and sync to platforms."""

import sys

from tuneshift.db import Database


def handle_rm(args, db: Database) -> int:
    """Remove a track from a playlist by position or title match."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    target = args.target
    tracks = db.get_playlist_tracks(playlist.id)

    # Try title match first (even if target looks numeric, e.g., "360")
    target_lower = target.lower()
    matches = [
        (i + 1, t) for i, t in enumerate(tracks) if target_lower in t.title.lower()
    ]

    # If no title match and target is numeric, treat as position
    if not matches:
        try:
            position = int(target)
            if position < 1 or position > len(tracks):
                print(
                    f"Position {position} out of range (1-{len(tracks)})",
                    file=sys.stderr,
                )
                return 1
            track = tracks[position - 1]
            had_failure = _remove_and_sync(db, playlist, track, position)
            return 1 if had_failure else 0
        except ValueError:
            print(f'No track matching "{target}" in "{playlist.name}"', file=sys.stderr)
            return 1

    if len(matches) == 1:
        pos, track = matches[0]
        had_failure = _remove_and_sync(db, playlist, track, pos)
        return 1 if had_failure else 0

    # Multiple matches: show and ask
    print(f'Multiple matches for "{target}":')
    for pos, track in matches:
        print(f"  {pos}. {track.title} - {track.artist}")
    choice = input("Remove which position? ").strip()
    try:
        pos = int(choice)
        track = tracks[pos - 1]
        had_failure = _remove_and_sync(db, playlist, track, pos)
        return 1 if had_failure else 0
    except (ValueError, IndexError):
        print("Cancelled.", file=sys.stderr)
        return 1


def _remove_and_sync(db: Database, playlist, track, position: int) -> bool:
    """Remove from DB and sync removal to all linked platforms.

    Returns True if any platform operation failed.
    """
    from tuneshift.commands.ingest_cmd import _load_client

    # BUG-7: `position` is the 1-based ordinal in the ordered track list, not the
    # stored playlist_tracks.position value. Map it to the real stored position so
    # we delete ONLY the chosen row: the same track_id may appear at several
    # positions, and a track_id-wide delete would silently wipe every copy.
    ordered = db.conn.execute(
        "SELECT position FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
        (playlist.id,),
    ).fetchall()
    stored_position = ordered[position - 1]["position"]
    db.remove_playlist_track_by_position(playlist.id, stored_position)
    print(
        f'Removed "{track.title} - {track.artist}" (position {position}) from "{playlist.name}"'  # noqa: E501
    )

    # Auto-reorder if enabled
    row = db.conn.execute(
        "SELECT auto_reorder, reorder_arc FROM playlists WHERE id = ?",
        (playlist.id,),
    ).fetchone()
    if row and row[0]:
        from tuneshift.sequencer.optimizer import sequence_playlist

        arc = row[1] or "wave"
        sequence_playlist(db, playlist.id, arc=arc)

    # Sync removal to linked platforms
    failures = False
    platforms = db.get_linked_platforms(playlist.id)
    for platform_name in platforms:
        client = _load_client(platform_name)
        if not client or not client.load_session():
            print(f"  {platform_name}: skipped (not logged in)")
            continue

        platform_playlist_id = db.get_platform_playlist_id(playlist.id, platform_name)
        if not platform_playlist_id:
            print(f"  {platform_name}: skipped (no linked playlist)")
            continue

        # Find the track on the platform by position and remove it
        try:
            platform_tracks = client.get_playlist_tracks(platform_playlist_id)
            # Find by matching title (position may differ due to prior divergence)
            target_lower = track.title.lower()
            matches = [
                i
                for i, pt in enumerate(platform_tracks)
                if target_lower in pt.title.lower()
            ]
            if matches:
                # BUG-7: remove only ONE platform occurrence to mirror the single
                # local removal. Both copies share the same platform id, so which
                # one is removed does not matter; removing all would wipe the track.
                client.remove_tracks_by_positions(platform_playlist_id, matches[:1])
                print(f"  {platform_name}: removed")
            else:
                print(f"  {platform_name}: track not found on platform")
        except Exception as exc:  # noqa: BLE001
            print(f"  {platform_name}: sync failed ({exc})", file=sys.stderr)
            failures = True

    return failures
