"""Order command: sequence a playlist by energy arc."""

import json
import logging
import sys

from tuneshift.db import Database
from tuneshift.sequencer.weights import PRESETS

logger = logging.getLogger(__name__)


def handle_order(args, db: Database) -> int:
    """Reorder a playlist using the sequencer, then sync to platforms."""
    from tuneshift.sequencer import sequence_playlist

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    arc = getattr(args, "arc", "wave")

    # Handle auto-reorder toggle
    if getattr(args, "auto_on", False):
        db.set_auto_reorder(playlist.id, enabled=True, arc=arc)
        print(f'Auto-reorder enabled for "{playlist.name}" (arc={arc})')
    elif getattr(args, "auto_off", False):
        db.set_auto_reorder(playlist.id, enabled=False)
        print(f'Auto-reorder disabled for "{playlist.name}"')

    # If only toggling the setting, skip the actual reorder
    if getattr(args, "auto_on", False) or getattr(args, "auto_off", False):
        return 0

    tracks = db.get_playlist_tracks(playlist.id)
    if not tracks:
        print(f'Playlist "{playlist.name}" is empty.')
        return 0

    # AC8: warn before sequencing when the wave arc is asked to order tracks that
    # mostly lack energy/valence -- the arc silently falls back to 0.5 otherwise,
    # producing a flat, meaningless order the user can't diagnose.
    if arc == "wave":
        _warn_if_energy_sparse(tracks)

    # Resolve weights from CLI argument
    weights_arg = getattr(args, "weights", None)
    if weights_arg:
        if weights_arg.startswith("{"):
            try:
                explicit_weights = json.loads(weights_arg)
            except json.JSONDecodeError as e:
                print(f"Invalid JSON in --weights: {e}", file=sys.stderr)
                return 1
        elif weights_arg in PRESETS:
            explicit_weights = PRESETS[weights_arg]
        else:
            print(f'Unknown preset: "{weights_arg}"', file=sys.stderr)
            return 1
    else:
        explicit_weights = None

    reordered = sequence_playlist(db, playlist.id, arc=arc, weights=explicit_weights)

    if getattr(args, "dry_run", False):
        _print_dry_run(db, reordered, playlist.name, arc)
        return 0

    db.set_playlist_tracks(playlist.id, reordered)

    print(f'Reordered "{playlist.name}" ({len(reordered)} tracks, arc={arc})')

    # Push reordered playlist to all linked platforms
    if not getattr(args, "no_sync", False):
        had_failures = _push_order_to_platforms(db, playlist)
        if had_failures:
            return 1

    return 0


_ENERGY_COVERAGE_THRESHOLD = 0.5


def _warn_if_energy_sparse(tracks) -> None:
    """Warn (non-fatally) when >50% of tracks lack energy/valence for wave arc."""
    if not tracks:
        return
    missing = sum(1 for t in tracks if t.energy is None or t.valence is None)
    if missing / len(tracks) > _ENERGY_COVERAGE_THRESHOLD:
        pct = round(100 * missing / len(tracks))
        print(
            f"Warning: {missing}/{len(tracks)} tracks ({pct}%) lack energy/valence "
            "data; wave-arc ordering will be approximate. Populate it with "
            "`tuneshift enrich <playlist> --platform tidal` or `tuneshift resolve`, "
            "or set values manually with `tuneshift edit <id> --energy --valence`.",
            file=sys.stderr,
        )


def _print_dry_run(db: Database, track_ids: list[int], name: str, arc: str) -> None:
    """Display proposed order without making changes."""
    print(
        f'[DRY RUN] Proposed order for "{name}" ({len(track_ids)} tracks, arc={arc}):'
    )
    print()
    for i, track_id in enumerate(track_ids, 1):
        track = db.get_track(track_id)
        if track:
            print(f"  {i:3d}. {track.artist} - {track.title}")
        else:
            print(f"  {i:3d}. [unknown track id={track_id}]")
    print()
    print("No changes written. Remove --dry-run to apply.")


def _push_order_to_platforms(db: Database, playlist) -> bool:
    """Push the current DB track order to all linked platforms.

    Returns True if any platform push failed.
    """
    from tuneshift.commands.ingest_cmd import _load_client

    platforms = db.get_linked_platforms(playlist.id)
    if not platforms:
        return False

    tracks = db.get_playlist_tracks(playlist.id)
    failures = False

    for platform_name in platforms:
        client = _load_client(platform_name)
        if not client or not client.load_session():
            print(f"  {platform_name}: skipped (not logged in)")
            continue

        platform_playlist_id = db.get_platform_playlist_id(playlist.id, platform_name)
        if not platform_playlist_id:
            print(f"  {platform_name}: skipped (no linked playlist)")
            continue

        # Get platform track IDs in the new order
        platform_ids = []
        for track in tracks:
            mappings = db.get_platform_mappings_for_tracks([track.id], platform_name)
            mapping = mappings.get(track.id)
            if mapping and mapping.platform_track_id:
                platform_ids.append(mapping.platform_track_id)

        if platform_ids:
            try:
                client.replace_playlist_tracks(platform_playlist_id, platform_ids)
                print(f"  {platform_name}: synced ({len(platform_ids)} tracks)")
            except Exception as exc:  # noqa: BLE001
                # Per-platform boundary: platform SDKs raise heterogeneous
                # exception types (tidalapi TidalAPIError, requests/urllib
                # OSError, auth RuntimeError, malformed-response KeyError). One
                # platform's failure must not abort syncing the others, so
                # degrade here -- but log with context and surface to the user
                # and caller (failures=True); never swallow silently.
                logger.warning(
                    "Order push to %s failed for playlist %s: %s",
                    platform_name,
                    playlist.id,
                    exc,
                )
                print(f"  {platform_name}: sync failed ({exc})", file=sys.stderr)
                failures = True
        else:
            print(
                f"  {platform_name}: no track mappings found (run tuneshift sync first)"
            )

    return failures
