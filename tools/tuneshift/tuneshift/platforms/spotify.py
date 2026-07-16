"""Spotify platform client."""

import os
import socket
import stat
from pathlib import Path
from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyPKCE

from tuneshift.models import AlbumResult, ArtistResult, PlaylistInfo, TrackResult
from tuneshift.platforms.auth import validate_no_symlink
from tuneshift.platforms.rate_limiter import RateLimiter

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"
_TOKEN_FILE = _TOKEN_DIR / "spotify.json"
_SCOPES = [
    "playlist-read-private",
    "playlist-modify-private",
    "playlist-modify-public",
]

# 1Password item for Spotify API credentials
_OP_ITEM_TITLE = "Spotify API - TuneShift"


def _get_spotify_client_id() -> str:
    """Retrieve Spotify client_id.

    Priority: env var > 1Password. No hardcoded fallback.
    """
    import subprocess

    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    if client_id:
        return client_id

    # Try 1Password CLI
    try:
        result = subprocess.run(
            ["op", "item", "get", _OP_ITEM_TITLE, "--fields", "credential", "--reveal"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    raise RuntimeError(
        f"No Spotify client_id found. Set SPOTIPY_CLIENT_ID env var, "
        f"or configure 1Password CLI with item '{_OP_ITEM_TITLE}'."
    )


class SpotifyClient:
    """Spotify streaming platform client."""

    def __init__(
        self, token_path: Path | None = None, client_id: str | None = None
    ) -> None:
        self._token_path = token_path or _TOKEN_FILE
        self._client_id = client_id or _get_spotify_client_id()
        self._sp: spotipy.Spotify | None = None
        self._rate_limiter = RateLimiter(max_per_second=5.0)

    @property
    def platform_name(self) -> str:
        return "spotify"

    def login(self) -> bool:
        """Authenticate with Spotify using the PKCE browser flow."""
        self._prepare_token_path()
        auth = self._build_auth(
            redirect_uri="http://127.0.0.1:8888/callback",
            open_browser=True,
        )
        token_info = self._call_api(lambda: auth.get_access_token(check_cache=False))
        if not token_info:
            return False
        self._sp = spotipy.Spotify(auth_manager=auth)
        self._fix_token_perms()
        return True

    def load_session(self) -> bool:
        """Load a cached Spotify token if one is available."""
        validate_no_symlink(self._token_path)
        if not self._token_path.exists():
            return False
        auth = self._build_auth(
            redirect_uri="http://127.0.0.1:8888/callback",
            open_browser=False,
        )
        token_info = self._call_api(auth.get_cached_token)
        if not token_info:
            return False
        self._sp = spotipy.Spotify(auth_manager=auth)
        self._fix_token_perms()
        return True

    def search_track(self, query: str, limit: int = 10) -> list[TrackResult]:
        """Search Spotify tracks by free text.

        ``market="from_token"`` scopes results to the authenticated user's
        market so Spotify returns ``is_playable`` (and applies track relinking),
        letting us record real availability instead of discarding it.
        """
        spotify = self._ensure_session()
        response = self._call_api(
            lambda: spotify.search(
                q=query, type="track", limit=limit, market="from_token"
            )
        )
        items = response.get("tracks", {}).get("items", [])
        return [self._track_to_result(item) for item in items if item.get("id")]

    def search_isrc(self, isrc: str) -> TrackResult | None:
        """Search Spotify by ISRC."""
        spotify = self._ensure_session()
        response = self._call_api(
            lambda: spotify.search(
                q=f"isrc:{isrc}", type="track", limit=1, market="from_token"
            )
        )
        items = response.get("tracks", {}).get("items", [])
        if not items:
            return None
        return self._track_to_result(items[0])

    @staticmethod
    def _year_from_release_date(release_date: str | None) -> int | None:
        if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
            return int(release_date[:4])
        return None

    def _album_to_result(self, album: dict[str, Any]) -> AlbumResult:
        artists = album.get("artists", []) or []
        return AlbumResult(
            platform_id=str(album.get("id", "")),
            title=album.get("name", ""),
            artist=artists[0].get("name", "") if artists else "",
            track_count=int(album.get("total_tracks") or 0),
            release_year=self._year_from_release_date(album.get("release_date")),
        )

    def search_album(self, query: str, limit: int = 5) -> list[AlbumResult]:
        """Search Spotify albums by free text."""
        spotify = self._ensure_session()
        response = self._call_api(
            lambda: spotify.search(q=query, type="album", limit=limit)
        )
        items = response.get("albums", {}).get("items", [])
        return [self._album_to_result(a) for a in items if a.get("id")]

    def get_album_tracks(self, album_id: str) -> list[TrackResult]:
        """Return all tracks on a Spotify album."""
        spotify = self._ensure_session()
        album = self._call_api(lambda: spotify.album(album_id))
        album_name = album.get("name", "")
        results: list[TrackResult] = []
        page = album.get("tracks", {}) or {}
        while True:
            for item in page.get("items", []):
                if not item or not item.get("id"):
                    continue
                artists = item.get("artists", []) or []
                duration_ms = item.get("duration_ms")
                results.append(
                    TrackResult(
                        platform_id=str(item.get("id", "")),
                        title=item.get("name", ""),
                        artist=artists[0].get("name", "") if artists else "",
                        album=album_name,
                        duration_seconds=(
                            int(duration_ms // 1000)
                            if isinstance(duration_ms, int)
                            else None
                        ),
                        isrc=None,
                    )
                )
            if not page.get("next"):
                break
            page = self._call_api(lambda p=page: spotify.next(p))
        return results

    def search_artist(self, query: str, limit: int = 3) -> list[ArtistResult]:
        """Search Spotify artists, returning enriched results."""
        spotify = self._ensure_session()
        response = self._call_api(
            lambda: spotify.search(q=query, type="artist", limit=limit)
        )
        items = response.get("artists", {}).get("items", [])
        results: list[ArtistResult] = []
        for a in items:
            if not a.get("id"):
                continue
            followers = a.get("followers") or {}
            results.append(
                ArtistResult(
                    platform_id=str(a.get("id", "")),
                    name=a.get("name", ""),
                    popularity=a.get("popularity"),
                    genres=list(a.get("genres") or []),
                    followers=followers.get("total")
                    if isinstance(followers, dict)
                    else None,
                )
            )
        return results

    def get_artist_albums(self, artist_id: str, limit: int = 20) -> list[AlbumResult]:
        """Return an artist's albums (album + single release types)."""
        spotify = self._ensure_session()
        response = self._call_api(
            lambda: spotify.artist_albums(
                artist_id, album_type="album,single", limit=limit
            )
        )
        items = response.get("items", [])
        return [self._album_to_result(a) for a in items if a.get("id")]

    def get_playlist(self, playlist_id: str) -> PlaylistInfo | None:
        """Return playlist metadata or None if it is not accessible."""
        spotify = self._ensure_session()
        try:
            playlist = self._call_api(
                lambda: spotify.playlist(playlist_id, fields="id,name,tracks.total")
            )
        except SpotifyException as exc:
            if exc.http_status == 404:
                return None
            raise
        return PlaylistInfo(
            platform_id=str(playlist["id"]),
            name=playlist.get("name", ""),
            num_tracks=int(playlist.get("tracks", {}).get("total", 0)),
        )

    def get_playlist_tracks(self, playlist_id: str) -> list[TrackResult]:
        """Return all tracks in a Spotify playlist."""
        spotify = self._ensure_session()
        results: list[TrackResult] = []
        offset = 0
        while True:
            page = self._call_api(
                lambda: spotify.playlist_tracks(
                    playlist_id,
                    offset=offset,
                    limit=100,
                    fields="items(track(id,name,artists,album,duration_ms,external_ids)),total",
                )
            )
            for item in page.get("items", []):
                track = item.get("track")
                if track and track.get("id"):
                    results.append(self._track_to_result(track))
            total = int(page.get("total", 0))
            offset += 100
            if offset >= total:
                break
        return results

    def create_playlist(self, name: str, description: str = "") -> PlaylistInfo:
        """Create a private Spotify playlist."""
        spotify = self._ensure_session()
        user = self._call_api(spotify.current_user)
        playlist = self._call_api(
            lambda: spotify.user_playlist_create(
                user["id"],
                name,
                public=False,
                description=description,
            )
        )
        return PlaylistInfo(
            platform_id=str(playlist["id"]),
            name=playlist.get("name", name),
            num_tracks=0,
        )

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Add tracks to a playlist in batches of 100."""
        spotify = self._ensure_session()
        uris = [self._to_track_uri(track_id) for track_id in track_ids]
        for start in range(0, len(uris), 100):
            batch = uris[start : start + 100]
            self._call_api(
                lambda batch=batch: spotify.playlist_add_items(playlist_id, batch)
            )
        return len(track_ids)

    def remove_tracks_by_positions(self, playlist_id: str, positions: list[int]) -> int:
        """Remove tracks from a playlist by their zero-based positions."""
        spotify = self._ensure_session()
        tracks = self.get_playlist_tracks(playlist_id)
        items_to_remove = []
        for position in sorted(positions):
            if 0 <= position < len(tracks):
                items_to_remove.append(
                    {
                        "uri": self._to_track_uri(tracks[position].platform_id),
                        "positions": [position],
                    }
                )
        if items_to_remove:
            self._call_api(
                lambda: spotify.playlist_remove_specific_occurrences_of_items(
                    playlist_id,
                    items_to_remove,
                )
            )
        return len(items_to_remove)

    def replace_playlist_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Replace playlist contents, preserving order."""
        spotify = self._ensure_session()
        uris = [self._to_track_uri(track_id) for track_id in track_ids]
        self._call_api(lambda: spotify.playlist_replace_items(playlist_id, uris[:100]))
        for start in range(100, len(uris), 100):
            batch = uris[start : start + 100]
            self._call_api(
                lambda batch=batch: spotify.playlist_add_items(playlist_id, batch)
            )

    def find_playlist_by_name(self, name: str) -> PlaylistInfo | None:
        """Find the first current-user playlist with the provided name."""
        spotify = self._ensure_session()
        offset = 0
        while True:
            page = self._call_api(
                lambda: spotify.current_user_playlists(limit=50, offset=offset)
            )
            items = page.get("items", [])
            for playlist in items:
                if playlist.get("name") == name:
                    return PlaylistInfo(
                        platform_id=str(playlist["id"]),
                        name=playlist.get("name", ""),
                        num_tracks=int(playlist.get("tracks", {}).get("total", 0)),
                    )
            offset += 50
            if offset >= int(page.get("total", 0)):
                break
        return None

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _fix_token_perms(self) -> None:
        if self._token_path.exists():
            os.chmod(self._token_path, stat.S_IRUSR | stat.S_IWUSR)

    def _prepare_token_path(self) -> None:
        validate_no_symlink(self._token_path)
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._token_path.parent, stat.S_IRWXU)

    def _build_auth(self, redirect_uri: str, open_browser: bool) -> SpotifyPKCE:
        return SpotifyPKCE(
            client_id=self._client_id,
            redirect_uri=redirect_uri,
            scope=" ".join(_SCOPES),
            cache_path=str(self._token_path),
            open_browser=open_browser,
        )

    def _call_api(self, fn: Any) -> Any:
        self._rate_limiter.wait()
        return fn()

    def _ensure_session(self) -> spotipy.Spotify:
        if self._sp is None:
            raise RuntimeError("Not logged in. Run: tuneshift login spotify")
        return self._sp

    @staticmethod
    def _track_to_result(track: dict[str, Any]) -> TrackResult:
        artists = track.get("artists", [])
        artist_name = artists[0].get("name", "") if artists else ""
        album = track.get("album") or {}
        external_ids = track.get("external_ids") or {}
        duration_ms = track.get("duration_ms")
        return TrackResult(
            platform_id=str(track.get("id", "")),
            title=track.get("name", ""),
            artist=artist_name,
            album=album.get("name", "") if isinstance(album, dict) else "",
            duration_seconds=int(duration_ms // 1000)
            if isinstance(duration_ms, int)
            else None,
            isrc=external_ids.get("isrc") if isinstance(external_ids, dict) else None,
            available=SpotifyClient._extract_availability(track),
        )

    @staticmethod
    def _extract_availability(track: dict[str, Any]) -> bool | None:
        """Derive a playability verdict from a Spotify track object.

        Prefers ``is_playable`` (present when ``market=`` was passed). Falls back
        to ``available_markets``: an explicitly empty list means blocked
        everywhere in-catalog; an absent list means "unknown" (None), never a
        guess. This is the signal tuneshift previously discarded.
        """
        if "is_playable" in track:
            return bool(track["is_playable"])
        markets = track.get("available_markets")
        if isinstance(markets, list):
            return len(markets) > 0
        return None

    @staticmethod
    def _to_track_uri(track_id: str) -> str:
        if ":" in track_id:
            return track_id
        return f"spotify:track:{track_id}"
