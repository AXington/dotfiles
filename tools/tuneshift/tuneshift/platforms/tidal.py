"""Tidal platform client."""

import json
import random
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import tidalapi

from tuneshift.models import AlbumResult, ArtistResult, PlaylistInfo, TrackResult
from tuneshift.platforms.auth import secure_write, validate_no_symlink
from tuneshift.platforms.rate_limiter import RateLimiter
from tuneshift.platforms.timeout import PlatformTimeout, call_with_timeout

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"
_TOKEN_FILE = _TOKEN_DIR / "tidal.json"
_LEGACY_TOKEN_FILE = (
    Path.home() / ".local" / "share" / "tidal-importer" / "session.json"
)


def _retry(fn: Callable[[], Any], max_retries: int = 3) -> Any:
    """Retry a Tidal API call with exponential backoff and a per-call timeout.

    Each attempt is bounded by a wall-clock timeout (BUG-4): a stalled TCP
    connection can otherwise hang a resolve run forever. A PlatformTimeout is
    not retried here (it is not a rate-limit signal); it propagates so the
    resolution worker can classify it as a transient network failure.
    """
    for attempt in range(max_retries + 1):
        try:
            return call_with_timeout(fn)
        except PlatformTimeout:
            raise
        except Exception as exc:
            if attempt == max_retries:
                raise
            error_text = str(exc).lower()
            if (
                "429" not in error_text
                and "rate" not in error_text
                and "connection" not in error_text
            ):
                raise
            delay = (2**attempt) + random.uniform(0.0, 0.5)
            time.sleep(delay)
    raise RuntimeError("Unreachable retry state")


def _release_date_iso(track: Any, album: Any) -> str | None:
    """Normalise a Tidal track/album release date to an ISO date string.

    tidalapi exposes ``tidal_release_date``/``release_date`` as ``datetime``
    objects (occasionally already strings). Prefer the track's own date, fall
    back to the album's. Returns ``None`` when no date is exposed so the
    DateCriterion treats the field as unextractable (NO_VERDICT) rather than
    fabricating a year.
    """
    for source, attrs in (
        (track, ("tidal_release_date", "release_date")),
        (album, ("release_date",)),
    ):
        if source is None:
            continue
        for attr in attrs:
            value = getattr(source, attr, None)
            if value is None:
                continue
            if isinstance(value, datetime):
                return value.date().isoformat()
            text = str(value).strip()
            if text:
                return text
    return None


class TidalClient:
    """Tidal streaming platform client."""

    def __init__(self, token_path: Path | None = None) -> None:
        self._token_path = token_path or _TOKEN_FILE
        self._rate_limiter = RateLimiter(max_per_second=4.0)
        self._session: tidalapi.Session | None = None
        self._login_future: Any | None = None

    @property
    def platform_name(self) -> str:
        return "tidal"

    def login(self) -> str:
        """Start the Tidal device authorization flow and return the verification URL."""
        self._session = tidalapi.Session()
        login, self._login_future = self._call_with_retry(self._session.login_oauth)
        return str(login.verification_uri_complete)

    def login_wait(self, timeout: float = 300.0) -> bool:
        """Wait for the device authorization flow to complete."""
        if self._session is None or self._login_future is None:
            raise RuntimeError("Call login() first")
        try:
            self._login_future.result(timeout=timeout)
        except (TimeoutError, OSError, RuntimeError):
            return False
        self._save_token()
        return True

    def load_session(self) -> bool:
        """Load a saved OAuth session from disk. Falls back to tidal-importer session."""  # noqa: E501
        token_path = self._token_path
        if not token_path.exists():
            # Fall back to legacy tidal-importer session
            if _LEGACY_TOKEN_FILE.exists():
                token_path = _LEGACY_TOKEN_FILE
            else:
                return False
        validate_no_symlink(token_path)
        data = json.loads(token_path.read_text(encoding="utf-8"))
        expiry_time = _parse_expiry_time(data.get("expiry_time"))
        self._session = tidalapi.Session()
        return bool(
            self._session.load_oauth_session(
                data["token_type"],
                data["access_token"],
                data.get("refresh_token"),
                expiry_time,
                is_pkce=True,
            )
        )

    def search_track(self, query: str, limit: int = 10) -> list[TrackResult]:
        """Search for tracks on Tidal."""
        self._ensure_session()

        def _search() -> list[TrackResult]:
            assert self._session is not None
            results = self._session.search(
                query, models=[tidalapi.media.Track], limit=limit
            )
            tracks = results.get("tracks", []) or []
            return [self._track_to_result(track) for track in tracks]

        return self._call_with_retry(_search)

    def search_isrc(self, isrc: str) -> TrackResult | None:
        """Search for the first result whose ISRC matches exactly."""
        results = self.search_track(isrc, limit=5)
        normalized_isrc = isrc.upper()
        for result in results:
            if result.isrc and result.isrc.upper() == normalized_isrc:
                return result
        return None

    def search_album(self, query: str, limit: int = 5) -> list["AlbumResult"]:
        """Search for albums on Tidal."""
        self._ensure_session()

        def _search() -> list[AlbumResult]:
            assert self._session is not None
            results = self._session.search(
                query, models=[tidalapi.album.Album], limit=limit
            )
            albums = results.get("albums", []) or []
            return [
                AlbumResult(
                    platform_id=str(album.id),
                    title=album.name or "",
                    artist=album.artist.name if getattr(album, "artist", None) else "",
                    track_count=int(album.num_tracks or 0),
                    release_year=getattr(album, "year", None),
                )
                for album in albums
            ]

        return self._call_with_retry(_search)

    def get_album_tracks(self, album_id: str) -> list[TrackResult]:
        """Get all tracks from a Tidal album."""
        self._ensure_session()

        def _get_tracks() -> list[TrackResult]:
            assert self._session is not None
            album = self._session.album(int(album_id))
            return [self._track_to_result(track) for track in album.tracks()]

        return self._call_with_retry(_get_tracks)

    def search_artist(self, query: str, limit: int = 3) -> list["ArtistResult"]:
        """Search for artists on Tidal."""
        self._ensure_session()

        def _search() -> list[ArtistResult]:
            assert self._session is not None
            results = self._session.search(
                query, models=[tidalapi.artist.Artist], limit=limit
            )
            artists = results.get("artists", []) or []
            return [
                ArtistResult(
                    platform_id=str(artist.id),
                    name=artist.name or "",
                    popularity=getattr(artist, "popularity", None),
                )
                for artist in artists
            ]

        return self._call_with_retry(_search)

    def get_artist_albums(self, artist_id: str, limit: int = 20) -> list["AlbumResult"]:
        """Get albums for a Tidal artist."""
        self._ensure_session()

        def _get_albums() -> list[AlbumResult]:
            assert self._session is not None
            artist = self._session.artist(int(artist_id))
            albums = artist.get_albums()[:limit]
            return [
                AlbumResult(
                    platform_id=str(album.id),
                    title=album.name or "",
                    artist=album.artist.name if getattr(album, "artist", None) else "",
                    track_count=int(album.num_tracks or 0),
                    release_year=getattr(album, "year", None),
                )
                for album in albums
            ]

        return self._call_with_retry(_get_albums)

    def get_track(self, track_id: str) -> TrackResult | None:
        """Fetch a single track by platform ID. Returns None if not found."""
        self._ensure_session()

        def _get_track() -> TrackResult | None:
            assert self._session is not None
            try:
                track = self._session.track(int(track_id))
            except Exception:  # noqa: BLE001
                return None
            return self._track_to_result(track)

        return self._call_with_retry(_get_track)

    def get_playlist(self, playlist_id: str) -> PlaylistInfo | None:
        """Return playlist metadata or None if the playlist does not exist."""
        self._ensure_session()

        def _get_playlist() -> PlaylistInfo | None:
            assert self._session is not None
            try:
                playlist = self._session.playlist(playlist_id)
            except Exception as exc:
                error_text = str(exc).lower()
                if "404" in error_text or "not found" in error_text:
                    return None
                raise
            return PlaylistInfo(
                platform_id=str(playlist.id),
                name=playlist.name or "",
                num_tracks=int(playlist.num_tracks or 0),
            )

        return self._call_with_retry(_get_playlist)

    def get_playlist_tracks(self, playlist_id: str) -> list[TrackResult]:
        """Return all tracks for a playlist in order.

        Skips unavailable/removed tracks (ObjectNotFound) and tracks with
        no usable metadata (None name). Prints warnings to stderr.
        """
        import sys

        self._ensure_session()

        def _get_tracks() -> list[TrackResult]:
            assert self._session is not None
            playlist = self._session.playlist(playlist_id)
            results: list[TrackResult] = []
            skipped = 0
            for track in playlist.tracks():
                try:
                    if track is None or getattr(track, "name", None) is None:
                        skipped += 1
                        continue
                    results.append(self._track_to_result(track))
                except Exception as exc:  # noqa: BLE001
                    track_id = getattr(track, "id", "unknown")
                    print(
                        f"  Skipping unavailable track {track_id}: {exc}",
                        file=sys.stderr,
                    )
                    skipped += 1
            if skipped:
                print(
                    f"  Warning: {skipped} unavailable track(s) skipped",
                    file=sys.stderr,
                )
            return results

        return self._call_with_retry(_get_tracks)

    def create_playlist(self, name: str, description: str = "") -> PlaylistInfo:
        """Create a new playlist for the authenticated user."""
        self._ensure_session()

        def _create_playlist() -> PlaylistInfo:
            assert self._session is not None
            playlist = self._session.user.create_playlist(name, description)
            return PlaylistInfo(
                platform_id=str(playlist.id),
                name=playlist.name or name,
                num_tracks=int(playlist.num_tracks or 0),
            )

        return self._call_with_retry(_create_playlist)

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Add tracks to a playlist and return the number requested."""
        self._ensure_session()

        def _add_tracks() -> int:
            assert self._session is not None
            playlist = self._session.playlist(playlist_id)
            playlist.add([int(track_id) for track_id in track_ids])
            return len(track_ids)

        return self._call_with_retry(_add_tracks)

    def remove_tracks_by_positions(self, playlist_id: str, positions: list[int]) -> int:
        """Remove tracks from a playlist by their zero-based positions."""
        self._ensure_session()
        if not positions:
            return 0

        def _remove_tracks() -> int:
            assert self._session is not None
            playlist = self._session.playlist(playlist_id)
            playlist.remove_by_indices(sorted(positions, reverse=True))
            return len(positions)

        return self._call_with_retry(_remove_tracks)

    def replace_playlist_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Replace the playlist contents with the provided track IDs."""
        self._ensure_session()

        def _replace_tracks() -> None:
            assert self._session is not None
            playlist = self._session.playlist(playlist_id)
            current_tracks = playlist.tracks()
            if current_tracks:
                playlist.remove_by_indices(list(range(len(current_tracks))))
            if track_ids:
                playlist.add([int(track_id) for track_id in track_ids])

        self._call_with_retry(_replace_tracks)

    def find_playlist_by_name(self, name: str) -> PlaylistInfo | None:
        """Find the first playlist owned by the user with the given name."""
        self._ensure_session()

        def _find_playlist() -> PlaylistInfo | None:
            assert self._session is not None
            for playlist in self._session.user.playlists():
                if playlist.name == name:
                    return PlaylistInfo(
                        platform_id=str(playlist.id),
                        name=playlist.name or "",
                        num_tracks=int(playlist.num_tracks or 0),
                    )
            return None

        return self._call_with_retry(_find_playlist)

    def _ensure_session(self) -> None:
        if self._session is None or not self._session.check_login():
            raise RuntimeError("Not logged in. Run: tuneshift login tidal")

    def get_track_metadata(self, platform_track_id: str) -> dict[str, Any] | None:
        """Fetch audio metadata (BPM, key, duration) for a single track."""
        self._ensure_session()

        def _fetch() -> dict[str, Any] | None:
            assert self._session is not None
            track = self._session.track(int(platform_track_id))
            meta: dict[str, Any] = {}
            if track.bpm:
                meta["tempo"] = float(track.bpm)
            if track.duration:
                meta["duration_seconds"] = int(track.duration)
            if track.key:
                meta["key"] = str(track.key)
                if hasattr(track, "key_scale") and track.key_scale:
                    meta["key_scale"] = str(track.key_scale)
            if track.isrc:
                meta["isrc"] = str(track.isrc)
            # Native version/audio metadata (spec section 4.2, BUILD-FIRST): these settle  # noqa: E501
            # the Atmos/named-mix/fidelity axes without string-parsing. Read
            # defensively - tidalapi may omit them on some tracks/versions.
            audio_modes = getattr(track, "audio_modes", None)
            if audio_modes:
                meta["audio_modes"] = list(audio_modes)
            audio_quality = getattr(track, "audio_quality", None)
            if audio_quality:
                meta["audio_quality"] = str(audio_quality)
            version = getattr(track, "version", None)
            if version:
                meta["tidal_version"] = str(version)
            media_tags = getattr(track, "media_metadata_tags", None)
            if media_tags:
                meta["media_metadata_tags"] = list(media_tags)
            return meta if meta else None

        return self._call_with_retry(_fetch)

    def _call_with_retry(self, fn: Callable[[], Any]) -> Any:
        self._rate_limiter.wait()
        return _retry(fn)

    def _save_token(self) -> None:
        if self._session is None:
            return
        data = {
            "token_type": self._session.token_type,
            "access_token": self._session.access_token,
            "refresh_token": self._session.refresh_token,
            "expiry_time": self._session.expiry_time.isoformat()
            if self._session.expiry_time
            else None,
        }
        secure_write(self._token_path, json.dumps(data))

    @staticmethod
    def _track_to_result(track: Any) -> TrackResult:
        # tidalapi parses allowStreaming -> `available` and exposes tier flags.
        # Capture them instead of discarding: None means "unknown", only an
        # explicit False denotes blocked-in-market.
        available = getattr(track, "available", None)
        premium = getattr(track, "premium_streaming_only", None)
        pay = getattr(track, "pay_to_stream", None)
        tier_restricted = premium is True or pay is True
        # Native version/audio metadata (spec section 4.2, BUILD-FIRST): the SEARCH
        # path is the candidate source for matching, so preserve the same fields
        # get_track_metadata captures - otherwise the Atmos/named-mix/fidelity
        # axes are blind on every candidate. Read defensively; tidalapi omits
        # them on some tracks/versions.
        album = getattr(track, "album", None)
        album_artist = None
        album_type = None
        if album is not None:
            album_artist_obj = getattr(album, "artist", None)
            if album_artist_obj is not None:
                album_artist = getattr(album_artist_obj, "name", None)
            album_type = getattr(album, "type", None)
        # Release date (M3 date axes): prefer the track's own release date, fall
        # back to the album's. tidalapi exposes datetime objects; normalise to an
        # ISO date string so the DateCriterion year-parse works uniformly.
        release_date = _release_date_iso(track, album)
        audio_modes = getattr(track, "audio_modes", None)
        media_tags = getattr(track, "media_metadata_tags", None)
        audio_quality = getattr(track, "audio_quality", None)
        version = getattr(track, "version", None)
        explicit_flag = getattr(track, "explicit", None)
        return TrackResult(
            platform_id=str(track.id),
            title=track.name or "",
            artist=track.artist.name if getattr(track, "artist", None) else "",
            album=album.name if album is not None else "",
            duration_seconds=track.duration
            if getattr(track, "duration", None)
            else None,
            isrc=getattr(track, "isrc", None),
            available=available if isinstance(available, bool) else None,
            tier_restricted=tier_restricted,
            audio_modes=list(audio_modes) if audio_modes else None,
            audio_quality=str(audio_quality) if audio_quality else None,
            explicit=explicit_flag if isinstance(explicit_flag, bool) else None,
            tidal_version=str(version) if version else None,
            media_metadata_tags=list(media_tags) if media_tags else None,
            album_artist=str(album_artist) if album_artist else None,
            album_type=str(album_type) if album_type else None,
            release_date=release_date,
        )


def _parse_expiry_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
