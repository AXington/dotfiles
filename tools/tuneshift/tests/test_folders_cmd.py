"""Tests for the folders/tag/untag/collections command handlers.

DB-backed handlers are exercised directly; Tidal-network subactions are
covered only through their not-logged-in guard (client mocked to None) so
no network calls occur.
"""

from pathlib import Path
from types import SimpleNamespace

import tuneshift.commands.folders_cmd as folders_cmd
from tuneshift.commands.folders_cmd import (
    handle_collections,
    handle_folders,
    handle_tag,
    handle_untag,
)
from tuneshift.db import Database


def _playlist(db: Database, name: str = "Mix") -> int:
    return db.create_playlist(name)


# --- tag ---------------------------------------------------------------

def test_tag_missing_playlist_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_tag(SimpleNamespace(playlist="Nope", collection="C"), db) == 1
    assert "Playlist not found: Nope" in capsys.readouterr().err


def test_tag_success_persists(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    _playlist(db)
    assert handle_tag(SimpleNamespace(playlist="Mix", collection="Rock"), db) == 0
    assert 'Tagged "Mix" with "Rock"' in capsys.readouterr().out
    assert [p.name for p in db.get_collection_playlists("Rock")] == ["Mix"]


# --- untag -------------------------------------------------------------

def test_untag_missing_playlist_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_untag(SimpleNamespace(playlist="Nope", collection="C"), db) == 1
    assert "Playlist not found: Nope" in capsys.readouterr().err


def test_untag_success(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    pid = _playlist(db)
    db.tag_playlist(pid, "Rock")
    assert handle_untag(SimpleNamespace(playlist="Mix", collection="Rock"), db) == 0
    assert 'Removed "Rock" from "Mix"' in capsys.readouterr().out


def test_untag_not_tagged_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    _playlist(db)
    assert handle_untag(SimpleNamespace(playlist="Mix", collection="Rock"), db) == 1
    assert "is not tagged" in capsys.readouterr().err


# --- collections -------------------------------------------------------

def _collections_args(**kwargs):
    base = dict(create_name=None, delete_name=None, collection=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_collections_create(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_collections(_collections_args(create_name="Rock"), db) == 0
    assert 'Created collection "Rock"' in capsys.readouterr().out


def test_collections_delete_found(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    db.create_collection("Rock")
    assert handle_collections(_collections_args(delete_name="Rock"), db) == 0
    assert 'Deleted collection "Rock"' in capsys.readouterr().out


def test_collections_delete_missing_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_collections(_collections_args(delete_name="Ghost"), db) == 1
    assert "Collection not found" in capsys.readouterr().err


def test_collections_show_populated(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    pid = _playlist(db)
    db.tag_playlist(pid, "Rock")
    assert handle_collections(_collections_args(collection="Rock"), db) == 0
    out = capsys.readouterr().out
    assert 'Collection "Rock" (1 playlists)' in out
    assert "- Mix" in out


def test_collections_show_empty(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_collections(_collections_args(collection="Empty"), db) == 0
    assert "No playlists" in capsys.readouterr().out


def test_collections_list_none(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_collections(_collections_args(), db) == 0
    assert "No collections." in capsys.readouterr().out


def test_collections_list_with_counts(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    pid = _playlist(db)
    db.tag_playlist(pid, "Rock")
    assert handle_collections(_collections_args(), db) == 0
    out = capsys.readouterr().out
    assert "Collections:" in out
    assert "Rock (1 playlists)" in out


# --- folders dispatch --------------------------------------------------

def test_folders_unknown_action_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    assert handle_folders(SimpleNamespace(action=None), db) == 1
    assert "Usage: tuneshift folders" in capsys.readouterr().err


def test_folders_move_missing_playlist_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    args = SimpleNamespace(action="move", playlist="Nope", to="Rock")
    assert handle_folders(args, db) == 1
    assert "Playlist not found: Nope" in capsys.readouterr().err


def test_folders_move_missing_folder_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    _playlist(db)
    args = SimpleNamespace(action="move", playlist="Mix", to="Ghost")
    assert handle_folders(args, db) == 1
    assert "Folder not found in cache" in capsys.readouterr().err


def test_folders_move_success(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    _playlist(db)
    db.cache_tidal_folder("trn:folder:abc", "Rock")
    args = SimpleNamespace(action="move", playlist="Mix", to="Rock")
    assert handle_folders(args, db) == 0
    assert 'Assigned "Mix" to folder "Rock"' in capsys.readouterr().out


def test_folders_unassign_missing_playlist_returns_1(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    args = SimpleNamespace(action="unassign", playlist="Nope")
    assert handle_folders(args, db) == 1
    assert "Playlist not found: Nope" in capsys.readouterr().err


def test_folders_unassign_success(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    _playlist(db)
    args = SimpleNamespace(action="unassign", playlist="Mix")
    assert handle_folders(args, db) == 0
    assert 'Unassigned "Mix"' in capsys.readouterr().out


def test_folders_status(tmp_db: Path, capsys) -> None:
    db = Database(tmp_db)
    pid = _playlist(db, "Assigned")
    _playlist(db, "Loose")
    db.cache_tidal_folder("trn:folder:abc", "Rock")
    db.set_playlist_tidal_folder(pid, "trn:folder:abc")
    assert handle_folders(SimpleNamespace(action="status"), db) == 0
    out = capsys.readouterr().out
    assert "[Rock]" in out
    assert "(root)" in out
    assert "- Loose" in out


def test_folders_list_not_logged_in_returns_1(tmp_db: Path, capsys, monkeypatch) -> None:
    db = Database(tmp_db)
    monkeypatch.setattr(folders_cmd, "_get_tidal_client", lambda: None)
    assert handle_folders(SimpleNamespace(action="list"), db) == 1


def test_folders_create_not_logged_in_returns_1(tmp_db: Path, capsys, monkeypatch) -> None:
    db = Database(tmp_db)
    monkeypatch.setattr(folders_cmd, "_get_tidal_client", lambda: None)
    assert handle_folders(SimpleNamespace(action="create", name="Rock"), db) == 1


# --- BUG-10: folder ids are TRNs in the DB, bare UUIDs at the tidalapi edge ---


def test_bare_folder_id_strips_trn_prefix() -> None:
    assert folders_cmd._bare_folder_id("trn:folder:abc-123") == "abc-123"


def test_bare_folder_id_passes_through_bare_uuid() -> None:
    """Tolerant of ids already stored bare, so a future storage change is safe."""
    assert folders_cmd._bare_folder_id("abc-123") == "abc-123"


class _RecordingFolder:
    def __init__(self, log: dict) -> None:
        self._log = log

    def rename(self, new_name: str) -> None:
        self._log["renamed_to"] = new_name

    def remove(self) -> None:
        self._log["removed"] = True

    def add_items(self, trns: list[str]) -> None:
        self._log.setdefault("added", []).extend(trns)


class _RecordingSession:
    """Stands in for tidalapi.Session, capturing the folder_id it is handed."""

    country_code = "US"

    def __init__(self, log: dict) -> None:
        self._log = log

    def folder(self, folder_id):
        self._log["folder_id"] = folder_id
        return _RecordingFolder(self._log)


def _mock_client(monkeypatch, log: dict) -> None:
    monkeypatch.setattr(
        folders_cmd,
        "_get_tidal_client",
        lambda: SimpleNamespace(_session=_RecordingSession(log)),
    )


def test_rename_resolves_folder_by_bare_uuid(tmp_db: Path, monkeypatch) -> None:
    """tidalapi matches on the bare ``data.id``; a TRN would raise ObjectNotFound."""
    db = Database(tmp_db)
    db.cache_tidal_folder("trn:folder:abc-123", "Rock")
    log: dict = {}
    _mock_client(monkeypatch, log)

    assert folders_cmd._folders_rename(db, "Rock", "Metal") == 0
    assert log["folder_id"] == "abc-123"
    assert log["renamed_to"] == "Metal"


def test_delete_resolves_folder_by_bare_uuid(tmp_db: Path, monkeypatch) -> None:
    db = Database(tmp_db)
    db.cache_tidal_folder("trn:folder:abc-123", "Rock")
    log: dict = {}
    _mock_client(monkeypatch, log)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert folders_cmd._folders_delete(db, "Rock") == 0
    assert log["folder_id"] == "abc-123"
    assert log["removed"] is True


# --- BUG-10 corollary: storage form is an invariant, so guard it --------------
#
# Read-side normalisation means tidal_folders.tidal_id and
# playlists.tidal_folder_id must *stay* in TRN form. Every folder lookup
# (get_playlists_by_tidal_folder, clear_tidal_folder_assignments,
# remove_tidal_folder_cache) is an exact-match query, so if a write path ever
# drifts to bare UUIDs the queries silently match zero rows -- e.g. `folders
# delete` would report "0 playlists moved to root" while orphaning every
# assignment. These tests fail loudly if that invariant breaks.


def test_import_stores_folder_ids_in_trn_form(tmp_db: Path, monkeypatch) -> None:
    db = Database(tmp_db)
    pid = _playlist(db, "Loose")

    folders_page = {
        "items": [{"trn": "trn:folder:abc-123", "name": "Rock"}]
    }
    contents_page = {"items": [{"name": "Loose"}]}

    class _Resp:
        status_code = 200

        def __init__(self, payload): self._payload = payload

        def json(self): return self._payload

    calls = {"n": 0}

    def _fake_get(_url, **_kwargs):
        calls["n"] += 1
        return _Resp(folders_page if calls["n"] == 1 else contents_page)

    monkeypatch.setattr(
        folders_cmd,
        "_get_tidal_client",
        lambda: SimpleNamespace(
            _session=SimpleNamespace(country_code="US", access_token="t0ken")
        ),
    )
    monkeypatch.setattr("requests.get", _fake_get)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert folders_cmd._folders_import(db) == 0

    stored = [f["tidal_id"] for f in db.get_cached_tidal_folders()]
    assert stored == ["trn:folder:abc-123"], "folder cache must stay TRN-form"

    assigned = db.get_playlists_by_tidal_folder("trn:folder:abc-123")
    assert [p.id for p in assigned] == [pid], "assignment must stay TRN-form"


def test_stored_form_keeps_exact_match_lookups_working(tmp_db: Path) -> None:
    """The exact-match queries only work while both sides share one form."""
    db = Database(tmp_db)
    pid = _playlist(db, "Loose")
    db.cache_tidal_folder("trn:folder:abc-123", "Rock")
    db.set_playlist_tidal_folder(pid, "trn:folder:abc-123")

    folder = db.get_tidal_folder_by_name("Rock")
    assert [p.id for p in db.get_playlists_by_tidal_folder(folder["tidal_id"])] == [pid]
    assert db.clear_tidal_folder_assignments(folder["tidal_id"]) == 1

    db.remove_tidal_folder_cache(folder["tidal_id"])
    assert db.get_tidal_folder_by_name("Rock") is None
