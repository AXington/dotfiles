"""Task 1.12: the playlist "synced" marker is persisted only after a
successful platform push, never before.

Regression guard against the fragile ordering where the DB claims a
playlist is synced while the push that actually mirrors it to the
platform has failed.
"""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from tuneshift.commands.add_cmd import _sync_add_to_platforms
from tuneshift.commands.sync_cmd import handle_sync
from tuneshift.db import Database
from tuneshift.models import PlaylistInfo, Track
from tuneshift.reconcile import ReconcileResult


def _setup_db_with_playlist(tmp_db: Path) -> Database:
    db = Database(tmp_db)
    playlist_id = db.create_playlist("Test Playlist")
    for i in range(3):
        tid = db.insert_track(Track(title=f"Track {i}", artist=f"Artist {i}"))
        db.add_track_to_playlist(playlist_id, tid, position=i)
    db.link_platform_playlist(playlist_id, "tidal", "tidal-pl-123")
    return db


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.platform_name = "tidal"
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id="tidal-pl-123", name="Test Playlist", num_tracks=3,
    )
    # An absent remote is an EMPTY READABLE playlist, not one whose read
    # raises. Raising here modelled the read-fails/write-succeeds asymmetry
    # that SYNC-WIPE needs, so the suite normalised the very state it
    # should have caught.
    client.get_playlist_tracks.return_value = []
    client.replace_playlist_tracks.return_value = None
    return client


def _playlist_id(db: Database) -> int:
    return db.find_playlist_by_name("Test Playlist").id


def _sync_args(**overrides: object) -> Namespace:
    base = dict(
        playlist="Test Playlist", platform="tidal", all=False,
        reconcile=False, apply=True, interactive=False,
    )
    base.update(overrides)
    return Namespace(**base)


class TestSyncPersistsOnlyAfterPush:
    def test_not_synced_before_any_push(self, tmp_db: Path):
        """A freshly linked playlist has never been synced."""
        db = _setup_db_with_playlist(tmp_db)
        assert db.get_last_synced(_playlist_id(db), "tidal") is None

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_marked_synced_after_successful_push(self, mock_load, mock_reconcile, tmp_db: Path):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )
        result = handle_sync(_sync_args(), db)

        assert result == 0
        client.replace_playlist_tracks.assert_called_once()
        assert db.get_last_synced(_playlist_id(db), "tidal") is not None

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_push_failure_leaves_state_pending(self, mock_load, mock_reconcile, tmp_db: Path):
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        client.replace_playlist_tracks.side_effect = RuntimeError("API error")
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-1", score=95, confidence="high",
        )
        result = handle_sync(_sync_args(), db)

        assert result == 1
        # Push failed, so the playlist must NOT be recorded as synced.
        assert db.get_last_synced(_playlist_id(db), "tidal") is None

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_nothing_to_push_does_not_mark_synced(self, mock_load, mock_reconcile, tmp_db: Path):
        """If every track is unavailable there is no push, so no synced state."""
        db = _setup_db_with_playlist(tmp_db)
        client = _mock_client()
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(confidence="not_found")
        result = handle_sync(_sync_args(), db)

        assert result == 0
        assert db.get_last_synced(_playlist_id(db), "tidal") is None


class TestAddPersistsOnlyAfterPush:
    @patch("tuneshift.reconcile.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_add_marks_synced_after_successful_add(self, mock_load, mock_reconcile, tmp_db: Path):
        db = _setup_db_with_playlist(tmp_db)
        pid = _playlist_id(db)
        tid = db.insert_track(Track(title="New", artist="Someone"))
        client = _mock_client()
        client.add_tracks.return_value = None
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-9", score=95, confidence="high",
        )

        failed = _sync_add_to_platforms(db, pid, tid, "New", "Someone")

        assert failed is False
        client.add_tracks.assert_called_once()
        assert db.get_last_synced(pid, "tidal") is not None

    @patch("tuneshift.reconcile.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_add_push_failure_leaves_state_pending(self, mock_load, mock_reconcile, tmp_db: Path):
        db = _setup_db_with_playlist(tmp_db)
        pid = _playlist_id(db)
        tid = db.insert_track(Track(title="New", artist="Someone"))
        client = _mock_client()
        client.add_tracks.side_effect = RuntimeError("API error")
        mock_load.return_value = client
        mock_reconcile.return_value = ReconcileResult(
            platform_track_id="tidal-track-9", score=95, confidence="high",
        )

        failed = _sync_add_to_platforms(db, pid, tid, "New", "Someone")

        assert failed is True
        assert db.get_last_synced(pid, "tidal") is None
