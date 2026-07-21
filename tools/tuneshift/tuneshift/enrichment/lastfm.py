"""Last.fm API client for track and artist tag lookups."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from tuneshift.enrichment.retry import (
    PermanentAPIError,
    RetryConfig,
    RetryStats,
    TransientAPIError,
    retry_api_call,
)
from tuneshift.platforms.rate_limiter import RateLimiter

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"
_BASE_URL = "https://ws.audioscrobbler.com/2.0/"


def _ensure_https_url(url: str) -> str:
    """Reject any non-https URL before it reaches urlopen (S310 defense)."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"Refusing to open non-https Last.fm URL: {url!r}")
    return url


# Last.fm has no rate limit headers. Soft limit ~5 req/s triggers throttling.
# Conservative fixed rate of 1.5 req/s keeps us safely under the radar.
_lastfm_limiter = RateLimiter(max_per_second=1.5, adaptive=False)

# Last.fm error codes that are transient (retryable)
_LASTFM_TRANSIENT_ERRORS = {
    8,  # Operation failed - try again
    11,  # Service offline
    16,  # Temporarily unavailable
    29,  # Rate limit exceeded
}


def _load_api_key() -> str | None:
    """Load Last.fm API key from config file or environment."""
    import os

    key = os.environ.get("LASTFM_API_KEY")
    if key:
        return key
    key_file = _TOKEN_DIR / "lastfm_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return None


def _raw_request(url: str) -> dict:
    """Perform a single Last.fm request and classify errors.

    Last.fm returns HTTP 200 with an error code in the JSON body for many
    failures, including rate limiting (error 29). We must inspect the body
    and raise the appropriate exception so retry logic can react.
    """
    req = urllib.request.Request(  # noqa: S310
        _ensure_https_url(url), headers={"User-Agent": "tuneshift/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read())

    # Last.fm signals errors in the body even with HTTP 200
    if isinstance(data, dict) and "error" in data:
        code = data.get("error")
        message = data.get("message", "Last.fm API error")
        try:
            code_int = int(code)
        except (ValueError, TypeError):
            code_int = -1
        if code_int in _LASTFM_TRANSIENT_ERRORS:
            raise TransientAPIError(
                f"Last.fm error {code_int}: {message}", status_code=429
            )
        raise PermanentAPIError(
            f"Last.fm error {code_int}: {message}", status_code=code_int
        )

    return data


def _request(
    method: str,
    params: dict,
    *,
    stats: RetryStats | None = None,
    config: RetryConfig | None = None,
) -> dict:
    """Make a rate-limited, retrying Last.fm API request."""
    api_key = _load_api_key()
    if not api_key:
        raise ValueError(
            "No Last.fm API key configured. Run: tuneshift config lastfm-key <key>"
        )

    params.update(
        {
            "method": method,
            "api_key": api_key,
            "format": "json",
        }
    )
    query = urllib.parse.urlencode(params)
    url = f"{_BASE_URL}?{query}"

    _lastfm_limiter.wait()
    return retry_api_call(_raw_request, url, config=config, stats=stats)


def get_track_tags(
    title: str, artist: str, *, stats: RetryStats | None = None
) -> list[str]:
    """Get top tags for a track from Last.fm."""
    try:
        data = _request(
            "track.getTopTags",
            {
                "track": title,
                "artist": artist,
            },
            stats=stats,
        )
        tags = data.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        return [t["name"].lower() for t in tags[:15] if int(t.get("count", 0)) > 0]
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        PermanentAPIError,
        TransientAPIError,
    ):
        return []


def get_artist_tags(artist: str, *, stats: RetryStats | None = None) -> list[str]:
    """Get top tags for an artist from Last.fm."""
    try:
        data = _request("artist.getTopTags", {"artist": artist}, stats=stats)
        tags = data.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        return [t["name"].lower() for t in tags[:15] if int(t.get("count", 0)) > 0]
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        PermanentAPIError,
        TransientAPIError,
    ):
        return []


def is_available() -> bool:
    """Check if Last.fm API key is configured."""
    return _load_api_key() is not None
