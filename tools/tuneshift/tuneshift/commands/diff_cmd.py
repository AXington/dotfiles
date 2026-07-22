"""Diff command: show canonical vs platform state without syncing."""

from __future__ import annotations

import sys
from typing import Any

from tuneshift.db import Database


def _resolve_diff_platforms(args: Any, db: Database, playlist: Any) -> list[str] | None:
    """Return the platforms to diff, or None if none are available."""
    if args.platform:
        return [args.platform]
    platforms = db.get_linked_platforms(playlist.id)
    if not platforms:
        print("No platforms linked.", file=sys.stderr)
        return None
    return platforms


def _fetch_live_ids(
    db: Database, client: Any, playlist_id: int, platform_name: str
) -> set[str]:
    """Fetch the set of platform track ids currently on the live playlist."""
    platform_playlist_id = db.get_platform_playlist_id(playlist_id, platform_name)
    if not platform_playlist_id:
        return set()
    try:
        return {
            pt.platform_id for pt in client.get_playlist_tracks(platform_playlist_id)
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not fetch live {platform_name} state: {exc})")
        return set()


def _classify_diff_tracks(
    tracks: list, cached_mappings: dict, live_ids: set[str]
) -> tuple[list[str], list[str], list[str], int]:
    """Bucket tracks into (to_add, unavailable, divergent, in_sync).

    BUG-6a: presence is decided by whether a track's mapped platform id is
    actually on the LIVE platform playlist, not by the user_approved (lock)
    flag. Auto-matched tracks are user_approved=0 yet fully synced, so gating
    on the lock made a synced playlist look entirely unpushed.
    """
    to_add: list[str] = []
    unavailable: list[str] = []
    divergent: list[str] = []
    in_sync = 0

    for track in tracks:
        mapping = cached_mappings.get(track.id)
        if mapping and mapping.status == "unavailable":
            unavailable.append(f"  ? {track.title} - {track.artist}")
        elif mapping and mapping.is_divergent:
            divergent.append(f"  ~ {track.title} -> {mapping.divergence_note}")
        elif (
            mapping
            and mapping.platform_track_id
            and mapping.platform_track_id in live_ids
        ):
            in_sync += 1
        elif mapping and mapping.platform_track_id:
            to_add.append(f"  + {track.title} - {track.artist}")
        else:
            to_add.append(f"  * {track.title} - {track.artist} (needs reconciliation)")

    return to_add, unavailable, divergent, in_sync


def _render_diff(
    platform_name: str,
    to_add: list[str],
    unavailable: list[str],
    divergent: list[str],
    in_sync: int,
) -> None:
    """Print the classified diff buckets for one platform."""
    if in_sync:
        print(f"\n  In sync ({in_sync} already on {platform_name}).")

    if to_add:
        print(f"\n  Would push ({len(to_add)} tracks):")
        for line in to_add[:20]:
            print(line)
        if len(to_add) > 20:
            print(f"  ... and {len(to_add) - 20} more")

    if divergent:
        print(f"\n  Divergent ({len(divergent)}):")
        for line in divergent:
            print(line)

    if unavailable:
        print(f"\n  Unavailable ({len(unavailable)}):")
        for line in unavailable:
            print(line)

    if not to_add and not divergent and not unavailable:
        print("  In sync.")


def _diff_one_platform(
    db: Database, playlist: Any, tracks: list, platform_name: str
) -> None:
    """Compute and render the diff for a single platform."""
    from tuneshift.commands.ingest_cmd import _load_client

    client = _load_client(platform_name)
    if not client:
        print(f"Unknown platform: {platform_name}", file=sys.stderr)
        return
    if not client.load_session():
        print(f"Not logged in to {platform_name}.", file=sys.stderr)
        return

    print(f'\n--- Diff: "{playlist.name}" vs {platform_name} ---')

    cached_mappings = db.get_platform_mappings_for_tracks(
        [t.id for t in tracks], platform_name
    )
    live_ids = _fetch_live_ids(db, client, playlist.id, platform_name)
    to_add, unavailable, divergent, in_sync = _classify_diff_tracks(
        tracks, cached_mappings, live_ids
    )
    _render_diff(platform_name, to_add, unavailable, divergent, in_sync)


def handle_diff(args, db: Database) -> int:
    """Show what would change on sync, without actually syncing."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    platforms = _resolve_diff_platforms(args, db, playlist)
    if platforms is None:
        return 1

    tracks = db.get_playlist_tracks(playlist.id)
    if not tracks:
        print(f'Playlist "{playlist.name}" is empty.')
        return 0

    for platform_name in platforms:
        _diff_one_platform(db, playlist, tracks, platform_name)

    return 0
