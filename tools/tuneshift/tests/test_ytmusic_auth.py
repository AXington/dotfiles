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


def test_session_load_failure_logs_not_prints(monkeypatch, tmp_path, caplog):
    """A malformed token must be reported via structured logging, not print.

    Guards the QUAL-M1 print->logging conversion. A bare ``print`` produces no
    ``LogRecord``; capturing a structured WARNING record from the module logger
    is the definitive proof that the diagnostic now flows through logging. (We
    deliberately do not assert on stderr: a configured stream handler may route
    logs there legitimately, which is not the same as a raw print.)
    """
    monkeypatch.setattr(
        "tuneshift.platforms.ytmusic.validate_no_symlink", lambda _path: None
    )
    token = tmp_path / "ytmusic.json"
    token.write_text("{ this is not valid json ")
    client = YTMusicClient(token_path=token)

    with caplog.at_level("WARNING", logger="tuneshift.platforms.ytmusic"):
        assert client.load_session() is False

    matching = [
        record
        for record in caplog.records
        if record.name == "tuneshift.platforms.ytmusic"
        and record.levelname == "WARNING"
        and "ytmusic session load failed" in record.getMessage()
    ]
    assert matching, "expected a structured WARNING log record, not a print"
