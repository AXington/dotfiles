"""Every outbound HTTP call must be bounded (NET-TIMEOUT).

An unbounded request hangs the CLI or a resolve run indefinitely: requests
defaults to no timeout, and python3-discogs-client defaults both of its
timeout knobs to None. These tests pin the bound at each call site that
previously had none.
"""

from pathlib import Path

import pytest

from tuneshift.identity.sources.discogs import DiscogsSource
from tuneshift.platforms.timeout import DEFAULT_NETWORK_TIMEOUT
from tuneshift.platforms.ytmusic import YTMusicClient


def test_ytmusic_data_api_passes_timeout(monkeypatch, tmp_path) -> None:
    client = YTMusicClient(token_path=tmp_path / "ytmusic.json")
    client._access_token = "token"
    monkeypatch.setattr(client, "_maybe_refresh_token", lambda: None)
    monkeypatch.setattr(client._rate_limiter, "wait", lambda: None)

    seen: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None: ...

        @staticmethod
        def json() -> dict:
            return {"ok": True}

    def _fake_get(_url, **kwargs):
        seen.update(kwargs)
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "get", _fake_get)

    assert client._data_api("get", "playlists") == {"ok": True}
    assert seen.get("timeout") == pytest.approx(DEFAULT_NETWORK_TIMEOUT)


def test_discogs_client_has_bounded_timeouts(monkeypatch, tmp_path: Path) -> None:
    token = tmp_path / "discogs_token"
    token.write_text("dummy-token\n")

    source = DiscogsSource(credentials_path=token)
    client = source._get_client()

    assert client._fetcher.connect_timeout == pytest.approx(DEFAULT_NETWORK_TIMEOUT)
    assert client._fetcher.read_timeout == pytest.approx(DEFAULT_NETWORK_TIMEOUT)


def test_discogs_timeout_honours_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TUNESHIFT_NETWORK_TIMEOUT", "7.5")
    token = tmp_path / "discogs_token"
    token.write_text("dummy-token\n")

    client = DiscogsSource(credentials_path=token)._get_client()

    assert client._fetcher.connect_timeout == pytest.approx(7.5)
