"""Curate command: trim, analyze, or fill playlists."""

from tuneshift.curation.context import PlaylistContext
from tuneshift.curation.curator import curate_analyze, curate_trim
from tuneshift.db import Database
from tuneshift.sequencer.metadata import get_track_metadata_map


def handle_curate(args, db: Database) -> int:
    """Handle curation operations."""
    playlists = db.list_playlists()
    matches = [p for p in playlists if p.name == args.playlist]
    if not matches:
        print(f'Playlist "{args.playlist}" not found.')
        return 1

    pid = matches[0].id
    track_ids = db.get_playlist_track_ids(pid)
    if not track_ids:
        print(f'Playlist "{args.playlist}" is empty.')
        return 1

    metadata_map = get_track_metadata_map(db, track_ids)
    tracks = [metadata_map[tid] for tid in track_ids if tid in metadata_map]

    ctx = PlaylistContext(
        goal=db.get_goal(pid) or "",
        narrative_sections=[],
        mood_profile=None,
        all_tracks=tracks,
    )

    if args.mode == "analyze":
        report = curate_analyze(tracks, ctx)
        print(f'Analysis for "{args.playlist}" ({len(tracks)} tracks):\n')
        print("Strongest tracks:")
        for entry in report.get("strongest", [])[:5]:
            print(
                f"  {entry['title']} - {entry['artist']} (score: {entry['average']:.2f})"  # noqa: E501
            )
        print("\nWeakest tracks:")
        for entry in report.get("weakest", [])[:5]:
            print(
                f"  {entry['title']} - {entry['artist']} (score: {entry['average']:.2f})"  # noqa: E501
            )
        return 0

    if args.mode == "trim":
        constraints = {}
        if hasattr(args, "target_tracks") and args.target_tracks:
            constraints["track_count"] = {
                "target": args.target_tracks,
                "tolerance": 2,
                "hard_limit": getattr(args, "hard_limit", None)
                or args.target_tracks + 2,
            }
        stored_constraints = db.get_constraints(pid)
        if stored_constraints:
            constraints.update(stored_constraints)

        result = curate_trim(tracks, ctx, constraints)

        if args.dry_run:
            print(
                f"Dry run: would keep {len(result.keep)}, cut {len(result.cut)} tracks:"
            )
            for track in result.cut:
                print(f"  CUT: {track.title} - {track.artist}")
        else:
            # Apply the trim
            new_order = [t.track_id for t in result.keep]
            db.set_playlist_tracks(pid, new_order)
            print(
                f'Trimmed "{args.playlist}": kept {len(result.keep)}, removed {len(result.cut)} tracks.'  # noqa: E501
            )
        return 0

    print(f"Unknown mode: {args.mode}")
    return 1
