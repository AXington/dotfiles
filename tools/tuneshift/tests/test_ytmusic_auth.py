"""Regression guard: YT Music Data API sends the real bearer token.

NOTE: A prior session reported the Authorization header was hardcoded to the
literal ``******`` placeholder. That was a false alarm caused by secret
REDACTION in tool output — the real source is ``f"Bearer {self._access_token}"``
and the runtime header is correct. This test locks that correct behaviour in
place so a genuine regression (e.g. dropping the token) would be caught.
"""

from tuneshift.platforms.ytmusic import YTMusicClient


def test_auth_headers_send_real_bearer_token(monkeypatch, tmp_path):
    client = YTMusicClient(token_path=tmp_path / "ytmusic.json")
    client._access_token = "ya29.real-access-token"
    monkeypatch.setattr(client, "_maybe_refresh_token", lambda: None)

    headers = client._auth_headers()

    assert headers["Authorization"] == "Bearer ya29.real-access-token"
    assert "******" not in headers["Authorization"]


def test_session_load_failure_logs_not_prints(monkeypatch, tmp_path, caplog, capsys):
    """A malformed token must be reported via structured logging, not print.

    Guards the QUAL-M1 print->logging conversion: library-layer diagnostics go
    through the module logger (machine-parseable) rather than stdout/stderr.
    """
    monkeypatch.setattr(
        "tuneshift.platforms.ytmusic.validate_no_symlink", lambda _path: None
    )
    token = tmp_path / "ytmusic.json"
    token.write_text("{ this is not valid json ")
    client = YTMusicClient(token_path=token)

    with caplog.at_level("WARNING", logger="tuneshift.platforms.ytmusic"):
        assert client.load_session() is False

    assert any(
        "ytmusic session load failed" in record.getMessage()
        for record in caplog.records
    )
    captured = capsys.readouterr()
    assert "session load failed" not in captured.out
    assert "session load failed" not in captured.err
