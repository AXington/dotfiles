"""BUG-6b: a successful sync --apply must record last_synced, incl. new playlists."""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tuneshift.commands.sync_cmd import handle_sync
from tuneshift.db import Database
from tuneshift.models import PlaylistInfo, Track
from tuneshift.reconcile import ReconcileResult


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "test.db"


def _mock_client(remote_pl_id: str) -> MagicMock:
    client = MagicMock()
    client.platform_name = "tidal"
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id=remote_pl_id, name="New Playlist", num_tracks=0,
    )
    # An absent remote is an EMPTY READABLE playlist, not one whose read
    # raises. Raising here modelled the read-fails/write-succeeds asymmetry
    # that SYNC-WIPE needs, so the suite normalised the very state it
    # should have caught.
    client.get_playlist_tracks.return_value = []
    client.replace_playlist_tracks.return_value = None
    return client


def _args(**overrides):
    base = dict(
        playlist="New Playlist", platform="tidal", all=False,
        reconcile=False, apply=True, interactive=False,
    )
    base.update(overrides)
    return Namespace(**base)


@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_new_playlist_sync_records_last_synced(mock_load, mock_reconcile, tmp_db):
    db = Database(tmp_db)
    pid = db.create_playlist("New Playlist")  # NOT pre-linked
    for i in range(2):
        tid = db.insert_track(Track(title=f"Track {i}", artist=f"Artist {i}"))
        db.add_track_to_playlist(pid, tid, position=i)

    mock_load.return_value = _mock_client("tidal-pl-new")
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="tidal-track-1", score=95, confidence="high",
    )

    assert handle_sync(_args(), db) == 0
    assert db.get_last_synced(pid, "tidal") is not None


def test_relinking_preserves_last_synced(tmp_db):
    # BUG-6b: re-linking an already-synced playlist must not reset its sync
    # timestamp (INSERT OR REPLACE used to delete the row and null last_synced_at).
    db = Database(tmp_db)
    pid = db.create_playlist("P")
    db.link_platform_playlist(pid, "tidal", "R1")
    db.mark_playlist_synced(pid, "tidal")
    stamped = db.get_last_synced(pid, "tidal")
    assert stamped is not None

    db.link_platform_playlist(pid, "tidal", "R1")  # re-link (same or new id)
    assert db.get_last_synced(pid, "tidal") == stamped  # preserved


def _mock_client_with_remote(remote_pl_id, live_platform_ids):
    client = MagicMock()
    client.platform_name = "tidal"
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id=remote_pl_id, name="Atmos: Beg For You", num_tracks=len(live_platform_ids),
    )
    client.get_playlist_tracks.return_value = [
        SimpleNamespace(platform_id=i, title="x") for i in live_platform_ids
    ]
    client.replace_playlist_tracks.return_value = None
    return client


@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_confirmed_in_sync_noop_records_last_synced(mock_load, mock_reconcile, tmp_db):
    # BUG-6c: remote already holds exactly the pushed ids -> no push, but status
    # must still record the confirmed sync.
    db = Database(tmp_db)
    pid = db.create_playlist("Atmos: Beg For You")
    tid = db.insert_track(Track(title="Beg For You", artist="Charli XCX"))
    db.add_track_to_playlist(pid, tid, position=0)
    db.link_platform_playlist(pid, "tidal", "tidal-pl-x")

    mock_load.return_value = _mock_client_with_remote("tidal-pl-x", ["T1"])
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="T1", score=95, confidence="high",
    )

    assert handle_sync(_args(playlist="Atmos: Beg For You"), db) == 0
    assert db.get_last_synced(pid, "tidal") is not None


@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_all_unavailable_noop_does_not_record_last_synced(mock_load, mock_reconcile, tmp_db):
    db = Database(tmp_db)
    pid = db.create_playlist("Atmos: Beg For You")
    tid = db.insert_track(Track(title="Missing", artist="Nobody"))
    db.add_track_to_playlist(pid, tid, position=0)
    db.link_platform_playlist(pid, "tidal", "tidal-pl-x")

    mock_load.return_value = _mock_client_with_remote("tidal-pl-x", [])
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id=None, score=0, confidence="not_found",
    )

    assert handle_sync(_args(playlist="Atmos: Beg For You"), db) == 0
    assert db.get_last_synced(pid, "tidal") is None
