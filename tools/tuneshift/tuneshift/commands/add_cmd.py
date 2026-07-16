"""Add command: add a track to the library and place it on a playlist.

Library-first (AC-D7): resolution/enrichment and any remote push are deferred to
the async resolution worker; the interactive add path never blocks on network.
"""

import sys

from tuneshift.db import Database
from tuneshift.models import Track


def handle_add(args, db: Database) -> int:
    """Add a track to a playlist."""
    # Check banned artists BEFORE doing anything
    from tuneshift.commands.batch_cmd import check_track_against_bans

    banned = check_track_against_bans(db, args.title, args.artist)
    if banned:
        print(
            f'Blocked: "{args.title}" by {args.artist} '
            f'includes banned artist "{banned}".',
            file=sys.stderr,
        )
        return 1

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        playlist_id = db.create_playlist(args.playlist)
        print(f"Created playlist: {args.playlist}")
    else:
        playlist_id = playlist.id

    # Find or create canonical track
    existing = db.find_track(args.title, args.artist, getattr(args, "album", None))
    if existing:
        track_id = existing.id
    else:
        track = Track(
            title=args.title,
            artist=args.artist,
            album=getattr(args, "album", None),
        )
        track_id = db.add_track(track)

    # Handle --replace flag
    replace_target = getattr(args, "replace", None)
    position = None
    if replace_target:
        tracks = db.get_playlist_tracks(playlist_id)
        target_lower = replace_target.lower()
        old_matches = [t for t in tracks if target_lower in t.title.lower()]
        if not old_matches:
            print(f"Replace target not found: {replace_target}", file=sys.stderr)
            return 1
        old_track = old_matches[0]
        row = db.conn.execute(
            "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, old_track.id),
        ).fetchone()
        position = row[0] if row else None
        db.transfer_pins(playlist_id, old_track.id, track_id)
        db.remove_track_from_playlist(playlist_id, old_track.id)

        # After removal, positions are renumbered. Get current track list and insert at old position
        track_ids = db.get_playlist_track_ids(playlist_id)
        track_ids.insert(position, track_id)
        db.set_playlist_tracks(playlist_id, track_ids)
        print(f'Replacing "{old_track.title}" with "{args.title}"')
    else:
        tracks = db.get_playlist_tracks(playlist_id)
        position = len(tracks) + 1
        db.add_track_to_playlist(playlist_id, track_id, position)

    print(
        f'Added "{args.title}" by {args.artist} to "{args.playlist}" at position {position}'
    )

    # Library-first (AC-D7): enqueue async resolution + enrichment instead of
    # blocking the interactive add path on MusicBrainz/LLM/remote calls. The
    # resolution worker resolves candidates and enriches out-of-band; the track
    # is `pending` until then. No inline remote push (that migrates to
    # plan/apply in Chunk 4 Task 4.6, which also routes local placement).
    db.enqueue_resolution(track_id)

    # Auto-reorder if enabled (local compute, non-blocking)
    _auto_reorder(db, playlist_id)

    return 0


def _sync_add_to_platforms(
    db: Database, playlist_id: int, track_id: int, title: str, artist: str
) -> bool:
    """Reconcile and add the track on all linked platforms.

    Returns True if any platform operation failed.

    NOTE (library-first, AC-D7): no longer called inline from ``handle_add`` —
    the add path is non-blocking and never pushes remotely. This remote-push
    logic is retained here (and covered by ``test_sync_persist_order``) because
    Chunk 4 Task 4.6 relocates it into the plan/apply pipeline; it is not dead
    code, it is awaiting that relocation.
    """
    from tuneshift.commands.ingest_cmd import _load_client
    from tuneshift.reconcile import reconcile_track

    platforms = db.get_linked_platforms(playlist_id)
    if not platforms:
        return False

    failures = False
    for platform_name in platforms:
        client = _load_client(platform_name)
        if not client or not client.load_session():
            print(f"  {platform_name}: skipped (not logged in)")
            continue

        platform_playlist_id = db.get_platform_playlist_id(playlist_id, platform_name)
        if not platform_playlist_id:
            print(f"  {platform_name}: skipped (no linked playlist)")
            continue

        # Reconcile the track
        result = reconcile_track(
            db, track_id, client, force=False, playlist_id=playlist_id
        )
        db.save_match_audit(
            track_id, platform_name, result.audit, playlist_id=playlist_id
        )
        if result.platform_track_id:
            try:
                client.add_tracks(platform_playlist_id, [result.platform_track_id])
                print(f"  {platform_name}: added")
                db.mark_playlist_synced(playlist_id, platform_name)
            except Exception as exc:
                print(f"  {platform_name}: failed ({exc})", file=sys.stderr)
                failures = True
        else:
            print(f'  {platform_name}: could not find "{title}" by {artist}')

    return failures


def _auto_reorder(db: Database, playlist_id: int) -> None:
    """Trigger auto-reorder if the playlist has it enabled."""
    row = db.conn.execute(
        "SELECT auto_reorder, reorder_arc FROM playlists WHERE id = ?",
        (playlist_id,),
    ).fetchone()
    if row and row[0]:
        from tuneshift.sequencer.optimizer import sequence_playlist

        arc = row[1] or "wave"
        sequence_playlist(db, playlist_id, arc=arc)
