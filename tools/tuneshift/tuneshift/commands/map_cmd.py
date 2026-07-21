"""Map command: manually link a track to a platform ID.

``map`` writes the GLOBAL platform mapping directly (with optional ``--verify``
metadata capture) and ``unmap`` deletes it. For a REVIEWABLE, journaled, and
reversible identity lock, at global OR per-playlist scope, prefer the routed
``lock`` / ``unlock`` commands (see ``commands/lock_cmd.py``), which produce a
plan by default (section 7.1 mutation routing, AC-L1/AC-P1). ``map``/``unmap`` remain
the immediate manual path (``map`` additionally captures platform metadata via
``--verify``).
"""

import logging
import sys

from tuneshift.db import Database

logger = logging.getLogger(__name__)


def _load_client(platform: str):
    """Load a platform client by name."""
    if platform == "tidal":
        from tuneshift.platforms.tidal import TidalClient

        return TidalClient()
    if platform == "ytmusic":
        from tuneshift.platforms.ytmusic import YTMusicClient

        return YTMusicClient()
    return None


def handle_map(args, db: Database) -> int:
    """Store a user-approved platform mapping for a track.

    Track selection is either by canonical id (``--track-id``) or by
    playlist name + title substring. ``--dry-run`` performs every lookup
    and prints the intended result but never writes to the database.
    """
    dry_run = getattr(args, "dry_run", False)

    track = _resolve_target_track(args, db)
    if track is None:
        return 1

    platform, platform_id = _extract_platform_args(args)
    if not platform:
        print("Specify --tidal or --ytmusic with a track ID", file=sys.stderr)
        return 1

    platform_title = None
    platform_artist = None
    platform_album = None
    verified_result = None

    if args.verify:
        client = _load_client(platform)
        if not client or not client.load_session():
            print(
                f"Not logged in to {platform}. Run: tuneshift login {platform}",
                file=sys.stderr,
            )
            return 1

        result = client.get_track(platform_id)
        if not result:
            print(f"Track ID {platform_id} not found on {platform}", file=sys.stderr)
            return 1

        verified_result = result
        platform_title = result.title
        platform_artist = result.artist
        platform_album = result.album
        duration = f" ({result.duration_seconds}s)" if result.duration_seconds else ""
        print(f"Found: {result.title} - {result.artist} [{result.album}]{duration}")

    if dry_run:
        print(f'[dry-run] Would map "{track.title}" -> {platform}:{platform_id}')
        return 0

    db.set_platform_mapping(
        track_id=track.id,
        platform=platform,
        platform_track_id=platform_id,
        user_approved=True,
        platform_title=platform_title,
        platform_artist=platform_artist,
        platform_album=platform_album,
        match_score=100,
    )
    print(f'Mapped "{track.title}" -> {platform}:{platform_id}')

    # FEAT-5: a user-approved exact map is authoritative identity. Clear any
    # prior quarantine (on both the track and the resolution queue) and mark the
    # track resolved+VERIFIED so it stops showing as quarantined/unresolved in
    # `resolve --status` and coverage. Hydrate identity fields from the verified
    # result when available (fill-NULL; never clobbers a prior value).
    db.approve_resolution(track.id)
    db.hydrate_identity_metadata(
        track.id,
        isrc=verified_result.isrc if verified_result else None,
        duration_seconds=verified_result.duration_seconds if verified_result else None,
        album=verified_result.album if verified_result else None,
        confidence_tier="VERIFIED",
        confidence_score=1.0,
        source="map",
    )

    # AC10: a new Tidal mapping auto-captures Atmos/catalog metadata + derives
    # the atmos-available tag. Reuse the --verify client if present; otherwise
    # load one best-effort (a fresh manual map should still capture). No login
    # or a non-Tidal platform simply skips -- the mapping itself is unaffected.
    # refresh=True because this mapping may replace a prior one, so any cached
    # metadata belongs to the old id and must be refetched, not reused.
    if platform == "tidal":
        from tuneshift.library.enrichment import capture_tidal_catalog

        try:
            catalog_client = client if args.verify else _load_client(platform)
            if catalog_client is not None and catalog_client.load_session():
                tags = capture_tidal_catalog(
                    db,
                    track.id,
                    platform,
                    platform_id,
                    client=catalog_client,
                    refresh=True,
                )
                if "atmos-available" in tags:
                    print("  Captured catalog metadata (Atmos available)")
        except Exception:  # noqa: BLE001
            logger.warning("catalog capture after mapping failed", exc_info=True)
    return 0


def _resolve_target_track(args, db: Database):
    """Resolve the canonical track from --track-id or playlist + title.

    Returns the Track, or None (after printing an error) if it cannot be
    resolved.
    """
    track_id = getattr(args, "track_id", None)
    if track_id is not None:
        track = db.get_track(track_id)
        if not track:
            print(f"Track id not found: {track_id}", file=sys.stderr)
            return None
        return track

    if not getattr(args, "playlist", None) or not getattr(args, "title", None):
        print("Specify --track-id, or a playlist name and title", file=sys.stderr)
        return None

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return None

    track = _find_track_by_title(db, playlist.id, args.title)
    if not track:
        print(f"Track not found: {args.title}", file=sys.stderr)
        return None
    return track


def handle_unmap(args, db: Database) -> int:
    """Remove a platform mapping, forcing re-reconcile on next sync."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    track = _find_track_by_title(db, playlist.id, args.title)
    if not track:
        print(f"Track not found: {args.title}", file=sys.stderr)
        return 1

    platform = _extract_unmap_platform(args)
    if not platform:
        print("Specify --tidal or --ytmusic", file=sys.stderr)
        return 1

    db.delete_platform_mapping(track.id, platform)
    print(f'Unmapped "{track.title}" from {platform}')
    return 0


def _find_track_by_title(db: Database, playlist_id: int, title: str):
    """Find a track in a playlist by title substring match."""
    tracks = db.get_playlist_tracks(playlist_id)
    title_lower = title.lower()
    matches = [t for t in tracks if title_lower in t.title.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [t for t in matches if t.title.lower() == title_lower]
        if len(exact) == 1:
            return exact[0]
        print(f'Multiple matches for "{title}":', file=sys.stderr)
        for t in matches:
            print(f"  - {t.title} - {t.artist}", file=sys.stderr)
        return None
    return None


def _extract_platform_args(args) -> tuple[str | None, str | None]:
    """Extract platform name and ID from args."""
    if getattr(args, "tidal", None):
        return "tidal", args.tidal
    if getattr(args, "ytmusic", None):
        return "ytmusic", args.ytmusic
    return None, None


def _extract_unmap_platform(args) -> str | None:
    """Extract platform name for unmap."""
    if getattr(args, "tidal", False):
        return "tidal"
    if getattr(args, "ytmusic", False):
        return "ytmusic"
    return None
