"""Tests for Last.fm and Genius rate-limit aware clients."""

from __future__ import annotations

import json

import pytest

from tuneshift.enrichment import lastfm
from tuneshift.enrichment.retry import PermanentAPIError, TransientAPIError


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_lastfm_error_29_raises_transient(monkeypatch):
    """Last.fm returns HTTP 200 with error 29 in the body for rate limiting."""
    body = json.dumps({"error": 29, "message": "Rate limit exceeded"}).encode()
    monkeypatch.setattr(lastfm.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))

    with pytest.raises(TransientAPIError):
        lastfm._raw_request("https://x")


def test_lastfm_error_6_raises_permanent(monkeypatch):
    """Error 6 (invalid params) is permanent, not retryable."""
    body = json.dumps({"error": 6, "message": "Invalid parameters"}).encode()
    monkeypatch.setattr(lastfm.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))

    with pytest.raises(PermanentAPIError):
        lastfm._raw_request("https://x")


def test_lastfm_success_returns_data(monkeypatch):
    body = json.dumps({"toptags": {"tag": [{"name": "Pop", "count": 100}]}}).encode()
    monkeypatch.setattr(lastfm.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))

    data = lastfm._raw_request("https://x")
    assert "toptags" in data


def test_lastfm_get_track_tags_swallows_after_retry(monkeypatch):
    """Rate limit error 29 should not crash get_track_tags; returns empty."""
    monkeypatch.setattr(lastfm, "_load_api_key", lambda: "fake_key")
    # Make the limiter instant
    monkeypatch.setattr(lastfm._lastfm_limiter, "wait", lambda: None)

    body = json.dumps({"error": 29, "message": "Rate limit"}).encode()
    monkeypatch.setattr(lastfm.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))

    # max_retries default 3 but all fail -> returns [] (no crash)
    import tuneshift.enrichment.retry as retry_mod
    # Patch sleep to avoid delays
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)

    tags = lastfm.get_track_tags("Song", "Artist")
    assert tags == []


def test_lastfm_get_track_tags_success(monkeypatch):
    monkeypatch.setattr(lastfm, "_load_api_key", lambda: "fake_key")
    monkeypatch.setattr(lastfm._lastfm_limiter, "wait", lambda: None)

    body = json.dumps({
        "toptags": {"tag": [
            {"name": "Disco", "count": 100},
            {"name": "Funk", "count": 50},
            {"name": "Zero", "count": 0},
        ]}
    }).encode()
    monkeypatch.setattr(lastfm.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))

    tags = lastfm.get_track_tags("Song", "Artist")
    assert "disco" in tags
    assert "funk" in tags
    assert "zero" not in tags  # count 0 filtered out
