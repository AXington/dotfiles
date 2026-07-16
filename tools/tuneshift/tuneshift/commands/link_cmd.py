"""Link command: auto-discover and link platform playlist IDs by name matching."""

import re
import sys

from tuneshift.db import Database

# Extract playlist ID from common URL formats
_URL_PATTERNS = {
    "spotify": re.compile(
        r"(?:open\.spotify\.com/playlist/|spotify:playlist:)([a-zA-Z0-9]+)"
    ),
    "tidal": re.compile(r"(?:tidal\.com/playlist/|tidal://playlist/)([a-f0-9-]+)"),
    "ytmusic": re.compile(r"(?:music\.youtube\.com/playlist\?list=)([-\w]+)"),
}


def _extract_playlist_id(platform: str, value: str) -> str:
    """Extract playlist ID from a URL or return raw value as-is."""
    pattern = _URL_PATTERNS.get(platform)
    if pattern:
        match = pattern.search(value)
        if match:
            return match.group(1)
    return value


def handle_link(args, db: Database) -> int:
    """Auto-link or manually link platform playlists."""
    platform = args.platform

    # Manual mode: link a single playlist by name + URL/ID
    playlist_name = getattr(args, "name", None)
    playlist_url = getattr(args, "url", None)
    if playlist_name and playlist_url:
        return _handle_manual_link(args, db, platform, playlist_name, playlist_url)

    # Auto mode: discover all by name matching
    client = _load_client(platform)
    if client is None:
        print(f"Unknown platform: {platform}", file=sys.stderr)
        return 1

    if not client.load_session():
        print(
            f"Not logged in to {platform}. Run: tuneshift login {platform}",
            file=sys.stderr,
        )
        return 1

    playlists = db.list_playlists()
    if not playlists:
        print("No playlists in database.")
        return 0

    linked = 0
    skipped = 0
    already = 0

    for pl in playlists:
        # Skip if already linked
        existing = db.get_platform_playlist_id(pl.id, platform)
        if existing:
            already += 1
            continue

        # Try to find by name on platform
        found = client.find_playlist_by_name(pl.name)
        if found:
            db.link_platform_playlist(pl.id, platform, found.platform_id)
            print(f"  Linked: {pl.name} -> {found.platform_id}")
            linked += 1
        else:
            if not args.quiet:
                print(f"  Not found: {pl.name}", file=sys.stderr)
            skipped += 1

    print(
        f"\nDone: {linked} linked, {already} already linked, {skipped} not found on {platform}"
    )
    return 0


def _load_client(platform_name: str):
    """Load a platform client by name."""
    if platform_name == "tidal":
        from tuneshift.platforms.tidal import TidalClient

        return TidalClient()
    elif platform_name == "spotify":
        from tuneshift.platforms.spotify import SpotifyClient

        return SpotifyClient()
    elif platform_name == "ytmusic":
        from tuneshift.platforms.ytmusic import YTMusicClient

        return YTMusicClient()
    return None


def _handle_manual_link(
    args, db: Database, platform: str, playlist_name: str, url_or_id: str
) -> int:
    """Manually link a playlist to a platform URL or ID."""
    playlist = db.find_playlist_by_name(playlist_name)
    if not playlist:
        print(f"Playlist not found: {playlist_name}", file=sys.stderr)
        return 1

    playlist_id = _extract_playlist_id(platform, url_or_id)
    db.link_platform_playlist(playlist.id, platform, playlist_id)
    print(f"Linked: {playlist.name} -> {platform}:{playlist_id}")
    return 0
