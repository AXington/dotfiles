"""A refreshed YT Music token must never touch disk world-readable (TOK-TOCTOU).

Path.write_text() creates the file at the process umask (typically 0644) and
only then is chmod'd to 0600, leaving a window in which another local user can
read a live OAuth token. secure_write() closes it: the content is written to a
private temp file that is fchmod'd to 0600 before any bytes land, then
atomically renamed into place.
"""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from tuneshift.platforms.ytmusic import YTMusicClient


@pytest.fixture(autouse=True)
def _allow_tmp_token_paths(monkeypatch):
    """secure_write() confines tokens to the real data dirs; tests use tmp_path.

    The allowlist is a production guard (it is why secure_write is stricter
    than the write_text it replaces), so bypass it rather than weaken it.
    """
    monkeypatch.setattr(
        "tuneshift.platforms.auth.validate_no_symlink", lambda _path: None
    )


def _token_file(tmp_path: Path) -> Path:
    token = tmp_path / "ytmusic.json"
    token.write_text(json.dumps({"access_token": "old", "expires_at": 0}))
    os.chmod(token, stat.S_IRUSR | stat.S_IWUSR)
    return token


def test_refresh_never_exposes_token_at_wider_perms(
    monkeypatch, tmp_path: Path
) -> None:
    """The exposure is a *window*, not the final mode, so observe mid-write.

    ytmusicapi's own setup leaves the token at the process umask (0644), and
    Path.write_text preserves an existing file's mode. The old code therefore
    rewrote a fresh token into a world-readable file and only narrowed it
    afterwards via _fix_token_perms. Spying on that call captures the mode at
    exactly the moment the new token is already on disk but not yet protected.

    secure_write never widens the file (0600 is set on the fd before any bytes
    are written, then atomically renamed), so _fix_token_perms is not called at
    all and no window is recorded.
    """
    token = tmp_path / "ytmusic.json"
    token.write_text(json.dumps({"access_token": "old", "expires_at": 0}))
    os.chmod(token, 0o644)

    client = YTMusicClient(token_path=token)
    client._refresh_token = "refresh"
    monkeypatch.setattr(
        "tuneshift.platforms.ytmusic._get_ytm_credentials", lambda: ("id", "secret")
    )

    observed: dict = {}
    original = client._fix_token_perms

    def _spy() -> None:
        if client._token_path.exists():
            observed["window"] = stat.S_IMODE(client._token_path.stat().st_mode)
        original()

    monkeypatch.setattr(client, "_fix_token_perms", _spy)

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"access_token": "fresh", "expires_in": 3600}

    import requests

    monkeypatch.setattr(requests, "post", lambda *_a, **_kw: _Resp())

    client._maybe_refresh_token()

    assert observed.get("window", 0o600) == 0o600, (
        f"token was readable at {oct(observed.get('window', 0))} "
        "after the new value was written"
    )
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert json.loads(token.read_text())["access_token"] == "fresh"
    assert client._access_token == "fresh"


def test_refresh_leaves_no_temp_files_behind(monkeypatch, tmp_path: Path) -> None:
    token = _token_file(tmp_path)
    client = YTMusicClient(token_path=token)
    client._refresh_token = "refresh"

    monkeypatch.setattr(
        "tuneshift.platforms.ytmusic._get_ytm_credentials", lambda: ("id", "secret")
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"access_token": "fresh", "expires_in": 3600}

    import requests

    monkeypatch.setattr(requests, "post", lambda *_a, **_kw: _Resp())
    client._maybe_refresh_token()

    assert [p.name for p in tmp_path.iterdir()] == ["ytmusic.json"]


def test_unexpired_token_is_not_rewritten(monkeypatch, tmp_path: Path) -> None:
    """Guards against a refresh storm: a live token must be left untouched."""
    token = tmp_path / "ytmusic.json"
    token.write_text(
        json.dumps({"access_token": "live", "expires_at": int(time.time()) + 3600})
    )
    os.chmod(token, stat.S_IRUSR | stat.S_IWUSR)

    def _boom(*_a, **_kw):
        raise AssertionError("must not hit the network for a live token")

    import requests

    monkeypatch.setattr(requests, "post", _boom)

    YTMusicClient(token_path=token)._maybe_refresh_token()
    assert json.loads(token.read_text())["access_token"] == "live"
