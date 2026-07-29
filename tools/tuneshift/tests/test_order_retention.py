"""Order + intentional-duplicate retention through the DB and through sync push.

Many of Alice's libraries are long, deliberately ordered playlists that may
repeat a track on purpose (e.g. a motif that opens and closes a set). Matching
must never reorder them and must never silently de-duplicate. These tests lock
that contract at two layers:

- the DB layer (``set_playlist_tracks`` / ``get_playlist_tracks``), which is the
  source of truth for order and multiplicity, and
- the sync push, which must emit platform IDs in playlist position order,
  duplicates included, and do so identically on repeated runs (idempotent).
"""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from tuneshift.commands.sync_cmd import handle_sync
from tuneshift.db import Database
from tuneshift.models import PlaylistInfo, Track
from tuneshift.reconcile import ReconcileResult


class TestDbOrderRetention:
    def test_duplicate_track_preserved_at_multiple_positions(self, tmp_db: Path):
        db = Database(tmp_db)
        pl = db.create_playlist("Motif")
        a = db.insert_track(Track(title="Intro", artist="X"))
        b = db.insert_track(Track(title="Middle", artist="Y"))
        # A appears first and last on purpose.
        db.set_playlist_tracks(pl, [a, b, a])

        got = db.get_playlist_track_ids(pl)
        assert got == [a, b, a]

        tracks = db.get_playlist_tracks(pl)
        assert [t.title for t in tracks] == ["Intro", "Middle", "Intro"]

    def test_set_playlist_tracks_idempotent_across_resyncs(self, tmp_db: Path):
        db = Database(tmp_db)
        pl = db.create_playlist("Stable")
        ids = [db.insert_track(Track(title=f"T{i}", artist="A")) for i in range(5)]
        order = [ids[2], ids[0], ids[4], ids[0], ids[1]]  # includes a duplicate

        for _ in range(3):
            db.set_playlist_tracks(pl, order)
            assert db.get_playlist_track_ids(pl) == order

    def test_order_is_not_sorted_or_normalized(self, tmp_db: Path):
        db = Database(tmp_db)
        pl = db.create_playlist("Deliberate")
        ids = [db.insert_track(Track(title=f"T{i}", artist="A")) for i in range(4)]
        scrambled = [ids[3], ids[1], ids[2], ids[0]]
        db.set_playlist_tracks(pl, scrambled)
        assert db.get_playlist_track_ids(pl) == scrambled


def _linked_playlist(tmp_db: Path, order_titles: list[str]) -> tuple[Database, dict]:
    """Build a linked playlist; return db and title->platform_id map."""
    db = Database(tmp_db)
    pl = db.create_playlist("Ordered")
    title_to_tid: dict[str, int] = {}
    ordered_tids: list[int] = []
    for title in order_titles:
        if title not in title_to_tid:
            title_to_tid[title] = db.insert_track(Track(title=title, artist="A"))
        ordered_tids.append(title_to_tid[title])
    db.set_playlist_tracks(pl, ordered_tids)
    db.link_platform_playlist(pl, "tidal", "tidal-pl-123")
    # Distinct platform id per distinct title.
    title_to_pid = {t: f"pid-{t}" for t in title_to_tid}
    return db, title_to_pid


def _client() -> MagicMock:
    client = MagicMock()
    client.platform_name = "tidal"
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id="tidal-pl-123", name="Ordered", num_tracks=0,
    )
    # An absent remote is an EMPTY READABLE playlist, not one whose read
    # raises. Raising here modelled the read-fails/write-succeeds asymmetry
    # that SYNC-WIPE needs, so the suite normalised the very state it
    # should have caught.
    client.get_playlist_tracks.return_value = []
    client.replace_playlist_tracks.return_value = None
    return client


def _order_args() -> Namespace:
    return Namespace(
        playlist="Ordered", platform="tidal", all=False,
        reconcile=False, apply=True, interactive=False,
    )


class TestSyncPushOrderRetention:
    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_push_preserves_order_and_duplicates(
        self, mock_load, mock_reconcile, tmp_db: Path, capsys
    ):
        order = ["Intro", "Middle", "Intro", "End"]
        db, title_to_pid = _linked_playlist(tmp_db, order)
        client = _client()
        mock_load.return_value = client

        def resolve(_db, track_id, _client, **_kw):
            track = _db.get_track(track_id)
            return ReconcileResult(
                platform_track_id=title_to_pid[track.title],
                score=95,
                confidence="high",
            )

        mock_reconcile.side_effect = resolve

        assert handle_sync(_order_args(), db) == 0

        pushed = client.replace_playlist_tracks.call_args[0][1]
        assert pushed == ["pid-Intro", "pid-Middle", "pid-Intro", "pid-End"]

    @patch("tuneshift.planapply.sync.reconcile_track")
    @patch("tuneshift.commands.ingest_cmd._load_client")
    def test_repeated_sync_is_idempotent(
        self, mock_load, mock_reconcile, tmp_db: Path, capsys
    ):
        order = ["A", "B", "A", "C", "B"]
        db, title_to_pid = _linked_playlist(tmp_db, order)

        def resolve(_db, track_id, _client, **_kw):
            track = _db.get_track(track_id)
            return ReconcileResult(
                platform_track_id=title_to_pid[track.title],
                score=95,
                confidence="high",
            )

        mock_reconcile.side_effect = resolve

        pushes = []
        for _ in range(3):
            client = _client()
            mock_load.return_value = client
            assert handle_sync(_order_args(), db) == 0
            pushes.append(client.replace_playlist_tracks.call_args[0][1])

        expected = ["pid-A", "pid-B", "pid-A", "pid-C", "pid-B"]
        assert all(p == expected for p in pushes)
