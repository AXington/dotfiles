"""Tests for the order command and CLI wiring."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from tuneshift.cli import main
from tuneshift.commands import order_cmd
from tuneshift.db import Database
from tuneshift.models import Track


def test_order_command_reorders_playlist(tmp_db: Path, capsys) -> None:
    """The order command rewrites playlist positions using the sequencer."""
    db = Database(tmp_db)
    first_id = db.insert_track(
        Track(title="High", artist="A", energy=0.9, valence=0.8, duration_seconds=200)
    )
    second_id = db.insert_track(
        Track(title="Low", artist="B", energy=0.2, valence=0.2, duration_seconds=210)
    )
    third_id = db.insert_track(
        Track(title="Mid", artist="C", energy=0.5, valence=0.5, duration_seconds=220)
    )
    playlist_id = db.create_playlist("Test Playlist")
    db.set_playlist_tracks(playlist_id, [first_id, second_id, third_id])
    db.close()

    exit_code = main(
        ["--db", str(tmp_db), "order", "Test Playlist", "--arc", "ascending"]
    )

    assert exit_code == 0
    reordered_db = Database(tmp_db)
    reordered_tracks = reordered_db.get_playlist_tracks(playlist_id)
    reordered_db.close()
    assert [track.id for track in reordered_tracks] == [second_id, third_id, first_id]
    assert (
        'Reordered "Test Playlist" (3 tracks, arc=ascending)' in capsys.readouterr().out
    )


class _FakeDB:
    """Minimal db stand-in for _push_order_to_platforms."""

    def get_linked_platforms(self, playlist_id):
        return ["tidal"]

    def get_playlist_tracks(self, playlist_id):
        return [SimpleNamespace(id=1)]

    def get_platform_playlist_id(self, playlist_id, platform):
        return "pl-123"

    def get_platform_mappings_for_tracks(self, track_ids, platform):
        return {1: SimpleNamespace(platform_track_id="t-1")}


class _FailingClient:
    def load_session(self):
        return True

    def replace_playlist_tracks(self, playlist_id, track_ids):
        raise OSError("connection reset by peer")


def test_push_order_platform_error_is_logged_and_surfaced(
    monkeypatch: pytest.MonkeyPatch, caplog, capsys
) -> None:
    """A platform push failure must be logged and surfaced, never swallowed."""
    monkeypatch.setattr(
        "tuneshift.commands.ingest_cmd._load_client", lambda name: _FailingClient()
    )
    playlist = SimpleNamespace(id=42)

    with caplog.at_level(logging.WARNING, logger="tuneshift.commands.order_cmd"):
        failures = order_cmd._push_order_to_platforms(_FakeDB(), playlist)

    assert failures is True
    assert any("failed" in record.message.lower() for record in caplog.records)
    assert "sync failed" in capsys.readouterr().err


def test_push_order_heterogeneous_platform_error_is_surfaced(
    monkeypatch: pytest.MonkeyPatch, caplog, capsys
) -> None:
    """Platform SDKs raise heterogeneous exception types (e.g. tidalapi's
    TidalAPIError subclasses Exception, not OSError; a malformed response can
    KeyError). All must degrade this one platform and surface, never crash the
    whole order push."""

    class _PlatformApiError(Exception):
        """Stand-in for a platform SDK error that is not an OSError subclass."""

    class _BuggyClient(_FailingClient):
        def replace_playlist_tracks(self, playlist_id, track_ids):
            raise _PlatformApiError("upstream 500 from platform API")

    monkeypatch.setattr(
        "tuneshift.commands.ingest_cmd._load_client", lambda name: _BuggyClient()
    )
    playlist = SimpleNamespace(id=42)

    with caplog.at_level(logging.WARNING, logger="tuneshift.commands.order_cmd"):
        failures = order_cmd._push_order_to_platforms(_FakeDB(), playlist)

    assert failures is True
    assert any("failed" in record.message.lower() for record in caplog.records)
    assert "sync failed" in capsys.readouterr().err
