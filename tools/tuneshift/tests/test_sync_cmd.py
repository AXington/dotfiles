"""Tests for tuneshift sync command (routed plan/apply push, AC-P1).

``sync`` writes a reviewable plan and pushes nothing by default; ``--apply``
builds and applies the push in one step. These tests exercise both contracts.
"""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from tuneshift.commands.sync_cmd import handle_sync
from tuneshift.db import Database
from tuneshift.models import Track, PlaylistInfo
from tuneshift.reconcile import ReconcileResult


def _setup_db_with_playlist(tmp_db: Path) -> Database:
    """Create a DB with a playlist, tracks, and platform link."""
    db = Database(tmp_db)
    playlist_id = db.create_playlist("Test Playlist")
    for i in range(3):
        tid = db.insert_track(Track(title=f"Track {i}", artist=f"Artist {i}"))
        db.add_track_to_playlist(playlist_id, tid, position=i)
    db.link_platform_playlist(playlist_id, "tidal", "tidal-pl-123")
    return db


def _mock_client(platform_name: str = "tidal") -> MagicMock:
    """Create a mock platform client."""
    client = MagicMock()
    client.platform_name = platform_name
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id="tidal-pl-123", name="Test Playlist", num_tracks=3,
    )
    # Remote read isn't enumerable in tests -> prior order unknown -> never an
    # idempotent skip, so a genuine push is always planned.
    # An absent remote is an EMPTY READABLE playlist, not one whose read
    # raises. Raising here modelled the read-fails/write-succeeds asymmetry
    # that SYNC-WIPE needs, so the suite normalised the very state it
    # should have caught.
    client.get_playlist_tracks.return_value = []
    client.replace_playlist_tracks.return_value = None
    return client


def _args(**overrides: object) -> Namespace:
    base = dict(
        playlist="Test Playlist", platform="tidal", all=False,
        reconcile=False, apply=True, interactive=False,
    )
    base.update(overrides)
    return Namespace(**base)


class TestHandleSyncBasic:
    def test_no_playlist_and_no_all_returns_error(self, tmp_db: Path, capsys):
        db = Database(tmp_db)
        result = handle_sync(_args(playlist=None, platform=None), db)
        assert result == 1
        assert "Specify" in capsys.readouterr().err

    def test_playlist_not_found_returns_error(self, tmp_db: Path, capsys):
        db = Database(tmp_db)
        result = handle_sync(_args(playlist="Nonexistent", platform=None), db)
        assert result == 1
        assert "not found" in capsys.readouterr().err

    def test_no_linked_platforms_returns_error(self, tmp_db: Path, capsys):
        db = Database(tmp_db)
        db.create_playlist("Empty Playlist")
        result = handle_sync(_args(playlist="Empty Playlist", platform=None), db)
        assert result == 1
        assert "No platforms" in capsys.readouterr().err


class TestSyncPush:
    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_successful_apply_returns_zero(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )

        result = handle_sync(_args(), db)

        assert result == 0
        client.replace_playlist_tracks.assert_called_once()
        call_args = client.replace_playlist_tracks.call_args[0]
        assert call_args[0] == "tidal-pl-123"
        assert len(call_args[1]) == 3
        assert "applied" in capsys.readouterr().out

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_plan_by_default_pushes_nothing(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        """AC-P1: a plain ``sync`` writes a plan and applies nothing on its own."""
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )

        result = handle_sync(_args(apply=False), db)

        assert result == 0
        client.replace_playlist_tracks.assert_not_called()
        out = capsys.readouterr().out
        assert "wrote plan" in out
        assert "plan apply" in out

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_push_failure_returns_nonzero(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        client.replace_playlist_tracks.side_effect = RuntimeError("API error")
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )

        result = handle_sync(_args(), db)

        assert result == 1
        assert "failed" in capsys.readouterr().err

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_all_unavailable_never_wipes_remote(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        """Durability: an all-unavailable resolution is a no-op, never an empty
        push that would clear the remote playlist."""
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(confidence="not_found")

        result = handle_sync(_args(), db)

        assert result == 0
        client.replace_playlist_tracks.assert_not_called()
        assert "nothing to push" in capsys.readouterr().out.lower()

    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_not_logged_in_skips_platform(self, mock_load, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        client.load_session.return_value = False
        mock_load.return_value = client

        result = handle_sync(_args(), db)

        assert result == 0
        assert "Not logged in" in capsys.readouterr().err

    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_unknown_platform_skips(self, mock_load, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        mock_load.return_value = None

        result = handle_sync(_args(platform="badplatform"), db)

        assert result == 0
        assert "Unknown platform" in capsys.readouterr().err


class TestSyncAll:
    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_sync_all_iterates_playlists(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        pl2 = db.create_playlist("Second Playlist")
        tid = db.insert_track(Track(title="Another", artist="Someone"))
        db.add_track_to_playlist(pl2, tid, position=0)
        db.link_platform_playlist(pl2, "tidal", "tidal-pl-456")

        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )

        result = handle_sync(_args(playlist=None, platform=None, all=True), db)

        assert result == 0
        assert client.replace_playlist_tracks.call_count == 2

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_sync_all_reports_failure(self, mock_load, mock_reconcile, tmp_db: Path, capsys):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        client.replace_playlist_tracks.side_effect = RuntimeError("fail")
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="t1", score=95, confidence="high",
        )

        result = handle_sync(_args(playlist=None, platform=None, all=True), db)

        assert result == 1


class TestSyncEmptyPlaylist:
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_empty_playlist_returns_zero(self, mock_load, tmp_db: Path, capsys):
        db = Database(tmp_db)
        pl_id = db.create_playlist("Empty")
        db.link_platform_playlist(pl_id, "tidal", "tidal-empty")
        client = _mock_client()
        mock_load.return_value = client

        result = handle_sync(_args(playlist="Empty"), db)

        assert result == 0
        assert "empty" in capsys.readouterr().out.lower()
        client.replace_playlist_tracks.assert_not_called()
