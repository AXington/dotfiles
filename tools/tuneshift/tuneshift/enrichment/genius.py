"""Genius API client for lyrics lookup."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from tuneshift.enrichment.retry import RetryConfig, RetryStats, retry_api_call
from tuneshift.platforms.rate_limiter import RateLimiter

_TOKEN_DIR = Path.home() / ".local" / "share" / "tuneshift"
_BASE_URL = "https://api.genius.com"


def _ensure_https_url(url: str) -> str:
    """Reject any non-https URL before it reaches urlopen (S310 defense).

    Genius API and lyric-page URLs are always https; a search result that came
    back with any other scheme is treated as hostile and refused.
    """
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"Refusing to open non-https Genius URL: {url!r}")
    return url


# Genius provides X-RateLimit-* headers. Adaptive mode reads them and paces
# accordingly. Baseline 1 req/s (community best practice: 0.5-2s between calls).
_genius_limiter = RateLimiter(max_per_second=1.0, adaptive=True)


def _load_access_token() -> str | None:
    """Load Genius access token from config."""
    import os

    token = os.environ.get("GENIUS_ACCESS_TOKEN")
    if token:
        return token
    creds_file = _TOKEN_DIR / "genius.json"
    if creds_file.exists():
        data = json.loads(creds_file.read_text())
        return data.get("access_token")
    return None


def _raw_get(req: urllib.request.Request, *, decode_json: bool = True):
    """Perform a single HTTP GET, feeding rate limit headers to the limiter."""
    # req is always built from an _ensure_https_url-validated URL below.
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        headers = dict(resp.headers)
        _genius_limiter.update_from_headers(headers)
        body = resp.read()
    if decode_json:
        return json.loads(body)
    return body.decode("utf-8", errors="replace")


def _request(
    path: str,
    params: dict | None = None,
    *,
    stats: RetryStats | None = None,
    config: RetryConfig | None = None,
) -> dict:
    """Make a rate-limited, retrying Genius API request."""
    token = _load_access_token()
    if not token:
        raise ValueError("No Genius access token configured.")

    url = f"{_BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(  # noqa: S310
        _ensure_https_url(url),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "tuneshift/1.0",
        },
    )
    _genius_limiter.wait()
    return retry_api_call(_raw_get, req, config=config, stats=stats)


def search_song(
    title: str, artist: str, *, stats: RetryStats | None = None
) -> str | None:
    """Search for a song and return its Genius URL (for lyrics scraping)."""
    try:
        data = _request("/search", {"q": f"{title} {artist}"}, stats=stats)
        hits = data.get("response", {}).get("hits", [])
        if not hits:
            return None
        # Find best match by artist
        artist_lower = artist.lower()
        for hit in hits[:5]:
            result = hit.get("result", {})
            primary = result.get("primary_artist", {}).get("name", "")
            if artist_lower in primary.lower() or primary.lower() in artist_lower:
                return result.get("url")
        # Fallback: first result
        return hits[0].get("result", {}).get("url")
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def get_lyrics(
    title: str, artist: str, *, stats: RetryStats | None = None
) -> str | None:
    """Get lyrics for a track by searching Genius and scraping the page.

    Returns the full lyrics text."""
    url = search_song(title, artist, stats=stats)
    if not url:
        return None

    try:
        req = urllib.request.Request(  # noqa: S310
            _ensure_https_url(url), headers={"User-Agent": "tuneshift/1.0"}
        )
        # Page scraping is heavier than API calls: pace it too.
        _genius_limiter.wait()
        html = retry_api_call(_raw_get, req, decode_json=False, stats=stats)

        # Extract lyrics from Genius HTML
        # Genius stores lyrics in <div data-lyrics-container="true"> elements
        containers = re.findall(
            r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>',
            html,
            re.DOTALL,
        )
        if not containers:
            return None

        raw = " ".join(containers)
        # Strip HTML tags
        text = re.sub(r"<br\s*/?>", "\n", raw)
        text = re.sub(r"<[^>]+>", "", text)
        # Decode HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&#x27;", "'").replace("&quot;", '"')
        text = text.strip()

        return text if text else None

    except (OSError, ValueError):
        return None


def is_available() -> bool:
    """Check if Genius access token is configured."""
    return _load_access_token() is not None
