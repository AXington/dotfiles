"""YouTube Music platform client."""

import os
import stat
from pathlib import Path
from typing import Any

import ytmusicapi
from ytmusicapi import YTMusic

from tuneshift.models import AlbumResult, ArtistResult, PlaylistInfo, TrackResult
from tuneshift.platforms.auth import validate_no_symlink
from tuneshift.platforms.rate_limiter import RateLimiter

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"
_TOKEN_FILE = _TOKEN_DIR / "ytmusic.json"

# 1Password item for custom OAuth credentials
_OP_ITEM_TITLE = "YouTube Data API - TuneShift"


def _get_ytm_credentials() -> tuple[str, str]:
    """Retrieve YT Music OAuth client_id and client_secret.

    Priority: env vars > 1Password. No hardcoded fallback.
    """
    import os
    import subprocess

    client_id = os.environ.get("YTM_CLIENT_ID")
    client_secret = os.environ.get("YTM_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    # Try 1Password CLI
    try:
        result = subprocess.run(
            [
                "op",
                "item",
                "get",
                _OP_ITEM_TITLE,
                "--fields",
                "credential,Section_Additional.client_secret",
                "--reveal",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    raise RuntimeError(
        "YouTube OAuth credentials not found. Set YTM_CLIENT_ID and "
        "YTM_CLIENT_SECRET env vars, or configure 1Password CLI."
    )


def _get_oauth_credentials():
    """Build OAuthCredentials for ytmusicapi >= 1.10."""
    client_id, client_secret = _get_ytm_credentials()
    try:
        from ytmusicapi import OAuthCredentials

        return OAuthCredentials(client_id=client_id, client_secret=client_secret)
    except ImportError:
        try:
            from ytmusicapi.auth.oauth import OAuthCredentials

            return OAuthCredentials(client_id=client_id, client_secret=client_secret)
        except (ImportError, TypeError):
            return None


class YTMusicClient:
    """YouTube Music streaming platform client."""

    def __init__(self, token_path: Path | None = None) -> None:
        self._token_path = token_path or _TOKEN_FILE
        self._yt: YTMusic | None = None
        self._rate_limiter = RateLimiter(max_per_second=2.0)

    @property
    def platform_name(self) -> str:
        return "ytmusic"

    def login(self) -> bool:
        """Run the available YT Music authentication flow and load the saved session."""
        self._prepare_token_path()
        self._run_setup()
        self._fix_token_perms()
        return self.load_session()

    def load_session(self) -> bool:
        """Load a saved YT Music auth file from disk."""
        validate_no_symlink(self._token_path)
        if not self._token_path.exists():
            return False
        try:
            import json

            token_data = json.loads(self._token_path.read_text())
            self._access_token = token_data.get("access_token")
            self._refresh_token = token_data.get("refresh_token")
            if not self._access_token:
                return False
            # Unauthenticated YTMusic instance for search only
            self._yt = YTMusic()
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            print(
                f"  ytmusic: session load failed ({type(exc).__name__}: {exc})",
                file=__import__("sys").stderr,
            )
            return False
        self._fix_token_perms()
        return True

    def _auth_headers(self) -> dict[str, str]:
        """Headers for YouTube Data API v3 requests."""
        self._maybe_refresh_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _maybe_refresh_token(self) -> None:
        """Refresh OAuth token if expired."""
        import json
        import time

        token_data = json.loads(self._token_path.read_text())
        expires_at = token_data.get("expires_at", 0)
        if time.time() < expires_at - 60:
            return
        client_id, client_secret = _get_ytm_credentials()
        import requests as req

        resp = req.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            new_data = resp.json()
            self._access_token = new_data["access_token"]
            token_data["access_token"] = new_data["access_token"]
            token_data["expires_at"] = int(time.time()) + new_data.get(
                "expires_in", 3600
            )
            self._token_path.write_text(json.dumps(token_data))
            self._fix_token_perms()

    def _data_api(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Make a YouTube Data API v3 call."""
        import requests as req

        self._rate_limiter.wait()
        url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
        headers = self._auth_headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        resp = getattr(req, method)(url, params=params, json=json_body, headers=headers)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    def search_track(self, query: str, limit: int = 10) -> list[TrackResult]:
        """Search YT Music songs by text (uses unauthenticated ytmusicapi)."""
        ytmusic = self._ensure_session()
        items = self._call_api(
            lambda: ytmusic.search(query, filter="songs", limit=limit)
        )
        return [self._to_result(item) for item in items if item.get("videoId")]

    def search_isrc(self, isrc: str) -> TrackResult | None:  # noqa: ARG002 - required by platform search interface
        """YT Music does not support direct ISRC lookup."""
        return None

    def search_album(self, query: str, limit: int = 5) -> list[AlbumResult]:
        """Search for albums on YouTube Music."""
        ytmusic = self._ensure_session()
        items = self._call_api(
            lambda: ytmusic.search(query, filter="albums", limit=limit)
        )
        results: list[AlbumResult] = []
        for item in items:
            browse_id = item.get("browseId", "")
            if not browse_id:
                continue
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else ""
            results.append(
                AlbumResult(
                    platform_id=browse_id,
                    title=item.get("title", ""),
                    artist=artist_name,
                    track_count=0,
                    release_year=_parse_year(item.get("year")),
                )
            )
        return results

    def get_album_tracks(self, album_id: str) -> list[TrackResult]:
        """Get all tracks from a YouTube Music album."""
        ytmusic = self._ensure_session()
        album = self._call_api(lambda: ytmusic.get_album(album_id))
        tracks = album.get("tracks", [])
        return [self._to_result(t) for t in tracks if t.get("videoId")]

    def search_artist(self, query: str, limit: int = 3) -> list[ArtistResult]:
        """Search for artists on YouTube Music."""
        ytmusic = self._ensure_session()
        items = self._call_api(
            lambda: ytmusic.search(query, filter="artists", limit=limit)
        )
        results: list[ArtistResult] = []
        for item in items:
            browse_id = item.get("browseId", "")
            if not browse_id:
                continue
            results.append(
                ArtistResult(
                    platform_id=browse_id,
                    name=item.get("artist", item.get("title", "")),
                )
            )
        return results

    def get_artist_albums(self, artist_id: str, limit: int = 20) -> list[AlbumResult]:
        """Get albums for a YouTube Music artist."""
        ytmusic = self._ensure_session()
        artist_data = self._call_api(lambda: ytmusic.get_artist(artist_id))
        albums_section = artist_data.get("albums", {})
        albums = albums_section.get("results", [])[:limit]
        results: list[AlbumResult] = []
        for album in albums:
            browse_id = album.get("browseId", "")
            if not browse_id:
                continue
            results.append(
                AlbumResult(
                    platform_id=browse_id,
                    title=album.get("title", ""),
                    artist=artist_data.get("name", ""),
                    track_count=0,
                    release_year=_parse_year(album.get("year")),
                )
            )
        return results

    def get_track(self, track_id: str) -> TrackResult | None:
        """Fetch a single track by video ID. Returns None if not found."""
        ytmusic = self._ensure_session()
        try:
            song = self._call_api(lambda: ytmusic.get_song(track_id))
        except Exception:
            return None
        details = song.get("videoDetails", {})
        if not details:
            return None
        return TrackResult(
            platform_id=track_id,
            title=details.get("title", ""),
            artist=details.get("author", ""),
            album="",
            duration_seconds=int(details.get("lengthSeconds", 0)) or None,
        )

    def get_playlist(self, playlist_id: str) -> PlaylistInfo | None:
        """Return playlist metadata via Data API v3."""
        try:
            data = self._data_api(
                "get",
                "playlists",
                params={"part": "snippet,contentDetails", "id": playlist_id},
            )
        except (OSError, RuntimeError, KeyError, ValueError):
            return None
        items = data.get("items", [])
        if not items:
            return None
        item = items[0]
        return PlaylistInfo(
            platform_id=playlist_id,
            name=item["snippet"]["title"],
            num_tracks=item["contentDetails"].get("itemCount", 0),
        )

    def get_playlist_tracks(self, playlist_id: str) -> list[TrackResult]:
        """Return all tracks in the playlist via Data API v3."""
        results: list[TrackResult] = []
        params: dict[str, Any] = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        while True:
            data = self._data_api("get", "playlistItems", params=params)
            for item in data.get("items", []):
                snippet = item["snippet"]
                video_id = snippet.get("resourceId", {}).get("videoId", "")
                if video_id:
                    results.append(
                        TrackResult(
                            platform_id=video_id,
                            title=snippet.get("title", ""),
                            artist=snippet.get(
                                "videoOwnerChannelTitle", ""
                            ).removesuffix(" - Topic"),
                            album="",
                            duration_seconds=0,
                            isrc=None,
                        )
                    )
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            params["pageToken"] = next_page
        return results

    def create_playlist(self, name: str, description: str = "") -> PlaylistInfo:
        """Create a YT Music playlist via Data API v3."""
        data = self._data_api(
            "post",
            "playlists",
            params={"part": "snippet,status"},
            json_body={
                "snippet": {"title": name, "description": description or " "},
                "status": {"privacyStatus": "private"},
            },
        )
        playlist_id = data["id"]
        return PlaylistInfo(platform_id=playlist_id, name=name, num_tracks=0)

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Add tracks (video IDs) to a YT Music playlist via Data API v3."""
        import requests as req

        added = 0
        for video_id in track_ids:
            try:
                self._data_api(
                    "post",
                    "playlistItems",
                    params={"part": "snippet"},
                    json_body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": video_id,
                            },
                        },
                    },
                )
                added += 1
            except req.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (404, 409):
                    # 409 = duplicate, 404 = video unavailable; skip
                    continue
                raise
        return added

    def remove_tracks_by_positions(self, playlist_id: str, positions: list[int]) -> int:
        """Remove playlist entries by zero-based positions via Data API v3."""
        if not positions:
            return 0
        # Fetch all playlist item IDs
        item_ids: list[str] = []
        params: dict[str, Any] = {
            "part": "id",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        while True:
            data = self._data_api("get", "playlistItems", params=params)
            for item in data.get("items", []):
                item_ids.append(item["id"])
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            params["pageToken"] = next_page
        # Delete by position (reverse order to preserve indices)
        removed = 0
        for pos in sorted(positions, reverse=True):
            if 0 <= pos < len(item_ids):
                self._data_api("delete", "playlistItems", params={"id": item_ids[pos]})
                removed += 1
        return removed

    def replace_playlist_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Clear the playlist and re-add tracks in order."""
        import requests as req

        # Remove all existing items
        params: dict[str, Any] = {
            "part": "id",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        try:
            while True:
                data = self._data_api("get", "playlistItems", params=params)
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    try:
                        self._data_api(
                            "delete", "playlistItems", params={"id": item["id"]}
                        )
                    except req.HTTPError as exc:
                        status = (
                            exc.response.status_code if exc.response is not None else 0
                        )
                        if status == 404:
                            continue
                        raise
                if not data.get("nextPageToken"):
                    break
        except req.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status != 404:
                raise
            # 404 = playlist empty or newly created; proceed to add
        # Add new tracks
        self.add_tracks(playlist_id, track_ids)

    def find_playlist_by_name(self, name: str) -> PlaylistInfo | None:
        """Find a playlist by name in the user's library via Data API v3."""
        params: dict[str, Any] = {
            "part": "snippet,contentDetails",
            "mine": "true",
            "maxResults": 50,
        }
        while True:
            data = self._data_api("get", "playlists", params=params)
            for item in data.get("items", []):
                if item["snippet"]["title"] == name:
                    return PlaylistInfo(
                        platform_id=item["id"],
                        name=name,
                        num_tracks=item["contentDetails"].get("itemCount", 0),
                    )
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            params["pageToken"] = next_page
        return None

    def _fix_token_perms(self) -> None:
        if self._token_path.exists():
            os.chmod(self._token_path, stat.S_IRUSR | stat.S_IWUSR)

    def _prepare_token_path(self) -> None:
        validate_no_symlink(self._token_path)
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._token_path.parent, stat.S_IRWXU)

    def _run_setup(self) -> None:
        self._rate_limiter.wait()
        client_id, client_secret = _get_ytm_credentials()
        if hasattr(ytmusicapi, "setup_oauth"):
            import inspect

            sig = inspect.signature(ytmusicapi.setup_oauth)
            if "client_id" in sig.parameters:
                ytmusicapi.setup_oauth(
                    client_id=client_id,
                    client_secret=client_secret,
                    filepath=str(self._token_path),
                    open_browser=True,
                )
            else:
                ytmusicapi.setup_oauth(
                    filepath=str(self._token_path), open_browser=True
                )
            return
        if hasattr(YTMusic, "setup"):
            YTMusic.setup(filepath=str(self._token_path))
            return
        ytmusicapi.setup(filepath=str(self._token_path))

    def _call_api(self, fn: Any) -> Any:
        self._rate_limiter.wait()
        return fn()

    def _ensure_session(self) -> YTMusic:
        if self._yt is None:
            raise RuntimeError("Not logged in. Run: tuneshift login ytmusic")
        return self._yt

    @staticmethod
    def _to_result(item: dict[str, Any]) -> TrackResult:
        artists = item.get("artists", [])
        artist_name = artists[0].get("name", "") if artists else ""
        album = item.get("album") or {}
        if isinstance(album, dict):
            album_name = album.get("name", "")
        elif isinstance(album, str):
            album_name = album
        else:
            album_name = ""
        return TrackResult(
            platform_id=str(item.get("videoId", "")),
            title=str(item.get("title", "")),
            artist=artist_name,
            album=album_name,
            duration_seconds=_parse_duration_seconds(item),
            isrc=None,
        )


def _extract_playlist_id(created: str | dict[str, Any]) -> str:
    if isinstance(created, str):
        return created
    for key in ("id", "playlistId"):
        value = created.get(key)
        if value:
            return str(value)
    raise RuntimeError("YT Music did not return a playlist ID")


def _parse_track_count(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            return int(digits)
    return 0


def _parse_duration_seconds(item: dict[str, Any]) -> int | None:
    duration_seconds = item.get("duration_seconds")
    if isinstance(duration_seconds, int):
        return duration_seconds
    duration_text = item.get("duration")
    if not isinstance(duration_text, str) or not duration_text:
        return None
    parts = duration_text.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = (total * 60) + int(part)
    return total


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
