"""Unit tests for the ARCH-M3 named query methods on Database.

These lock the behavior contract of the query/mutation methods that replace raw
``db.conn.execute`` calls in the command/planapply/library layers, so the
routing refactor is provably behavior-preserving.
"""

from pathlib import Path

import pytest

from tuneshift.db import Database
from tuneshift.models import PlatformMapping, Track
from tuneshift.persistence.specs import TABLE_SPECS


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _track(db: Database, title: str, artist: str) -> int:
    return db.add_track(Track(title=title, artist=artist))


def test_all_track_ids(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = _track(db, "A", "x")
    b = _track(db, "B", "y")
    assert sorted(db.all_track_ids()) == sorted([a, b])


def test_all_track_ids_empty(tmp_path: Path) -> None:
    assert _db(tmp_path).all_track_ids() == []


def test_get_track_title(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _track(db, "Bohemian Rhapsody", "Queen")
    assert db.get_track_title(tid) == "Bohemian Rhapsody"
    assert db.get_track_title(999999) is None


def test_set_track_metadata_overwrites_blob(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _track(db, "A", "x")
    db.set_track_metadata(tid, {"vibes": ["calm"], "energy": 0.3})
    got = db.get_track(tid)
    assert got is not None
    assert got.metadata["vibes"] == ["calm"]
    # A second write fully replaces the blob (not a merge).
    db.set_track_metadata(tid, {"themes": ["night"]})
    got2 = db.get_track(tid)
    assert got2 is not None
    assert got2.metadata == {"themes": ["night"]}


def test_resolution_attempts_default_zero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _track(db, "A", "x")
    assert db.get_resolution_attempts(tid) == 0
    assert db.get_resolution_attempts(tid, transient=True) == 0


def test_resolution_attempts_reflects_increment(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _track(db, "A", "x")
    db.enqueue_resolution(tid)
    db.set_resolution_state(tid, "pending", increment_attempts=True)
    db.set_resolution_state(tid, "pending", increment_transient=True)
    assert db.get_resolution_attempts(tid) == 1
    assert db.get_resolution_attempts(tid, transient=True) == 1


def _playlist_with_tracks(db: Database, name: str, n: int) -> tuple[int, list[int]]:
    pid = db.create_playlist(name)
    ids = []
    for i in range(n):
        tid = _track(db, f"T{i}", "x")
        db.add_track_to_playlist(pid, tid, i)
        ids.append(tid)
    return pid, ids


def test_playlist_track_positions_and_lookup(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid, ids = _playlist_with_tracks(db, "P", 3)
    assert db.get_playlist_track_positions(pid) == [0, 1, 2]
    assert db.get_max_playlist_position(pid) == 2
    assert db.get_track_position(pid, ids[1]) == 1
    assert db.get_track_position(pid, 999999) is None


def test_max_position_empty_playlist_is_zero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("empty")
    assert db.get_max_playlist_position(pid) == 0


def test_playlist_name_and_description(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("Road Trip", "summer 2003")
    assert db.get_playlist_name(pid) == "Road Trip"
    assert db.get_playlist_name_description(pid) == ("Road Trip", "summer 2003")
    assert db.get_playlist_name(999999) is None
    assert db.get_playlist_name_description(999999) is None


def test_reorder_config(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    # Default: auto_reorder off.
    cfg = db.get_playlist_reorder_config(pid)
    assert cfg is not None
    assert cfg[0] in (0, None)
    assert db.get_playlist_reorder_config(999999) is None


def test_append_and_remove_playlist_track(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    tid = _track(db, "A", "x")
    db.append_playlist_track(pid, tid, 0)
    assert db.get_playlist_track_positions(pid) == [0]
    db.remove_playlist_track(pid, tid)
    assert db.get_playlist_track_positions(pid) == []


def test_insert_if_absent_is_noop_on_conflict(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    a = _track(db, "A", "x")
    b = _track(db, "B", "y")
    db.append_playlist_track(pid, a, 0)
    # Position 0 is taken; OR IGNORE leaves the original in place.
    db.insert_playlist_track_if_absent(pid, b, 0)
    assert db.get_track_position(pid, a) == 0
    assert db.get_track_position(pid, b) is None


def test_shift_and_set_position(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    tid = _track(db, "A", "x")
    db.append_playlist_track(pid, tid, 0)
    db.shift_playlist_position(pid, 0, 5)
    assert db.get_track_position(pid, tid) == 5
    db.set_playlist_track_position(pid, tid, 2)
    assert db.get_track_position(pid, tid) == 2


def test_delete_playlist(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    db.delete_playlist(pid)
    assert db.get_playlist_name(pid) is None


def test_platform_track_ids_and_approved_filter(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = _track(db, "A", "x")
    b = _track(db, "B", "y")
    db.upsert_platform_mapping(
        PlatformMapping(track_id=a, platform="tidal", platform_track_id="t-a", user_approved=True)
    )
    db.upsert_platform_mapping(
        PlatformMapping(track_id=b, platform="tidal", platform_track_id="t-b", user_approved=False)
    )
    assert db.get_platform_track_ids("tidal") == sorted([a, b])
    assert db.get_platform_track_ids("tidal", approved_only=True) == [a]


def test_platform_tracks_columns_and_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = _track(db, "A", "x")
    db.upsert_platform_mapping(
        PlatformMapping(track_id=a, platform="tidal", platform_track_id="t-a", user_approved=True)
    )
    cols = db.get_platform_tracks_columns()
    assert "platform" in cols and "track_id" in cols
    rows = db.get_platform_tracks_for_track(a)
    assert len(rows) == 1
    mapping = dict(zip(cols, rows[0], strict=True))
    assert mapping["platform"] == "tidal"


def test_batch_history_methods(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = db.create_playlist("P")
    h1 = db.record_batch(pid, '{"operations": []}')
    h2 = db.record_batch(pid, '{"operations": [{"action": "rm"}]}')
    # reverted rows are excluded from "latest"; revert h2 so h1 is the newest active.
    db.mark_batch_reverted(h2)
    latest = db.get_latest_batch_history()
    assert latest is not None and latest["id"] == h1
    entry = db.get_batch_history_entry(h1)
    assert entry is not None and entry["playlist_id"] == pid
    assert db.get_batch_history_entry(999999) is None
    all_rows = db.list_batch_history_entries()
    assert {r["id"] for r in all_rows} == {h1, h2}
    scoped = db.list_batch_history_entries(playlist_id=pid)
    assert len(scoped) == 2


def test_spec_crud_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = _track(db, "A", "x")
    db.upsert_platform_mapping(
        PlatformMapping(track_id=a, platform="tidal", platform_track_id="orig", user_approved=False)
    )
    spec = TABLE_SPECS["platform_tracks"]
    pk = {"track_id": a, "platform": "tidal"}
    row = db.read_spec_columns(spec, spec.all_columns, pk)
    assert row is not None
    assert row["platform_track_id"] == "orig"
    # Update a non-PK column.
    db.update_spec_row(spec, {**pk, "platform_track_id": "updated"})
    row2 = db.read_spec_columns(spec, ["platform_track_id"], pk)
    assert row2["platform_track_id"] == "updated"
    # Insert (upsert) a fresh row.
    b = _track(db, "B", "y")
    db.insert_spec_row(
        spec,
        {"track_id": b, "platform": "tidal", "platform_track_id": "new", "status": "ok"},
    )
    assert db.read_spec_columns(spec, ["platform_track_id"], {"track_id": b, "platform": "tidal"})["platform_track_id"] == "new"
    # Delete.
    db.delete_spec_row(spec, {"track_id": b, "platform": "tidal"})
    assert db.read_spec_columns(spec, ["platform_track_id"], {"track_id": b, "platform": "tidal"}) is None


def test_read_spec_columns_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    spec = TABLE_SPECS["platform_tracks"]
    with pytest.raises(ValueError, match="not in spec"):
        db.read_spec_columns(spec, ["evil_column"], {"track_id": 1, "platform": "tidal"})
