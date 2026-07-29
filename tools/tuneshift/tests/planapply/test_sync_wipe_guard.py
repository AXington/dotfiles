"""A rollback must never convert "prior state unknown" into "prior state empty".

Reported by the 2026-07-28 re-audit as SYNC-WIPE (CRITICAL). Four links turned a
failed read into permanent remote data loss:

1. ``_remote_ids`` catches every exception and returns ``None``
2. the executor pushed anyway, journaling ``{"track_ids": None}`` as prior state
3. ``build_compensating_plan`` did ``prior.get("track_ids") or []``
4. applying that plan called ``replace_playlist_tracks(id, [])``

The prior snapshot is often the only record of pre-sync ordering, so the loss is
unrecoverable rather than merely damaged. ``sync.py`` already draws the right
distinction when building a forward plan (``prior_ids is not None and ...``);
these tests pin the same rule onto the compensating path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tuneshift.db import Database
from tuneshift.models import PlaylistInfo, Track, TrackResult
from tuneshift.planapply import sync as sync_mod
from tuneshift.planapply.apply import REMOTE_TABLE_PREFIX, RollbackReport, apply_plan
from tuneshift.planapply.models import row_key_for
from tuneshift.planapply.sync import (
    build_compensating_plan,
    build_sync_plan,
    make_sync_executor,
)
from tuneshift.reconcile import ReconcileResult
from tuneshift.types import JournalEntry


class FakeClient:
    """Protocol subset used by sync, with a switchable read failure."""

    platform_name = "tidal"

    def __init__(self, *, read_fails: bool = False) -> None:
        self.playlists: dict[str, list[str]] = {}
        self.read_fails = read_fails
        self.replace_calls: list[tuple[str, list[str]]] = []

    def find_playlist_by_name(self, name: str) -> PlaylistInfo | None:
        pid = f"pl:{name}"
        if pid in self.playlists:
            return PlaylistInfo(platform_id=pid, name=name, num_tracks=0)
        return None

    def create_playlist(self, name: str, description: str = "") -> PlaylistInfo:
        pid = f"pl:{name}"
        self.playlists.setdefault(pid, [])
        return PlaylistInfo(platform_id=pid, name=name, num_tracks=0)

    def get_playlist_tracks(self, playlist_id: str) -> list[TrackResult]:
        if self.read_fails:
            raise TimeoutError("transient read failure")
        return [
            TrackResult(platform_id=tid, title="", artist="", album="")
            for tid in self.playlists.get(playlist_id, [])
        ]

    def replace_playlist_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        self.replace_calls.append((playlist_id, list(track_ids)))
        self.playlists[playlist_id] = list(track_ids)


def _seed(tmp_db: Path) -> tuple[Database, int, list[int]]:
    db = Database(tmp_db)
    pid = db.create_playlist("Roadtrip")
    tids = [
        db.add_track(Track(title=f"Song {i}", artist="Artist", album="Album"))
        for i in range(3)
    ]
    for pos, tid in enumerate(tids):
        db.add_track_to_playlist(pid, tid, pos)
    return db, pid, tids


def _stub_reconcile(monkeypatch, mapping: dict[int, str]) -> None:
    def fake(db, track_id, client, **kwargs):  # noqa: ANN001, ANN003
        tid = mapping.get(track_id)
        if tid is None:
            return ReconcileResult(confidence="not_found")
        return ReconcileResult(platform_track_id=tid, confidence="high", score=95)

    monkeypatch.setattr(sync_mod, "reconcile_track", fake)


def _report_with_prior(track_ids: list[str] | None) -> RollbackReport:
    """A rollback report holding one remote entry with the given prior state."""
    report = RollbackReport(plan_id="pl-test")
    report.remote_skipped = 1
    report.compensating.append(
        JournalEntry(
            id=1,
            plan_id="pl-test",
            table_name=f"{REMOTE_TABLE_PREFIX}tidal",
            row_key=row_key_for(playlist_id=1, platform="tidal"),
            op="remote_push",
            prior_value={
                "platform_playlist_id": "pl:Roadtrip",
                "track_ids": track_ids,
            },
            new_value=None,
            applied_at="2026-07-29T00:00:00",
        )
    )
    return report


class TestCompensatingPlanNeverCoercesUnknown:
    def test_unknown_prior_state_is_not_an_empty_push(self) -> None:
        # The whole bug in one assertion: None must not become [].
        comp = build_compensating_plan(_report_with_prior(None))

        assert len(comp.changes) == 1
        change = comp.changes[0]
        assert change.proposed.get("track_ids") != []
        assert not change.is_actionable, (
            "a compensating push whose prior state is unknown must never be "
            "applied; applying it wipes the remote playlist permanently"
        )

    def test_unknown_prior_state_stays_visible_in_the_plan(self) -> None:
        # Recorded rather than dropped, so the unrecoverable entry is auditable.
        comp = build_compensating_plan(_report_with_prior(None))

        assert len(comp.changes) == 1
        assert "unknown" in (comp.changes[0].reason or "").lower()

    def test_known_empty_prior_state_still_restores_to_empty(self) -> None:
        # A genuinely empty prior state is a legitimate reversal target, and
        # must not be collateral damage of the guard above.
        comp = build_compensating_plan(_report_with_prior([]))

        assert comp.changes[0].proposed["track_ids"] == []
        assert comp.changes[0].is_actionable

    def test_known_prior_state_is_restored_unchanged(self) -> None:
        comp = build_compensating_plan(_report_with_prior(["OLD1", "OLD2"]))

        assert comp.changes[0].proposed["track_ids"] == ["OLD1", "OLD2"]
        assert comp.changes[0].is_actionable


class TestExecutorRefusesUnreversiblePush:
    def test_failed_read_on_existing_playlist_does_not_push(
        self, tmp_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pushing here would destroy a remote order we could not read, and the
        # journal would record no way back. Fail closed instead.
        db, pid, tids = _seed(tmp_db)
        _stub_reconcile(monkeypatch, {tids[0]: "A", tids[1]: "B", tids[2]: "C"})
        client = FakeClient()
        client.playlists["pl:Roadtrip"] = ["OLD1", "OLD2"]
        db.link_platform_playlist(pid, "tidal", "pl:Roadtrip")

        plan = build_sync_plan(db, pid, client, platform="tidal")
        client.read_fails = True
        executor = make_sync_executor(db, client, platform="tidal")

        report = apply_plan(db, plan, remote_executor=executor)

        assert client.replace_calls == [], "must not push with prior state unknown"
        assert client.playlists["pl:Roadtrip"] == ["OLD1", "OLD2"]
        assert report.applied == 0

    def test_newly_created_playlist_is_empty_by_construction(
        self, tmp_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing can be destroyed in a playlist that did not exist a moment
        # ago, so a failing read must not block the very first sync.
        db, pid, tids = _seed(tmp_db)
        _stub_reconcile(monkeypatch, {tids[0]: "A", tids[1]: "B", tids[2]: "C"})
        client = FakeClient(read_fails=True)

        plan = build_sync_plan(db, pid, client, platform="tidal")
        executor = make_sync_executor(db, client, platform="tidal")

        report = apply_plan(db, plan, remote_executor=executor)

        assert report.applied == 1
        assert client.playlists["pl:Roadtrip"] == ["A", "B", "C"]
        entries = db.get_journal_entries(plan.plan_id)
        remote = [e for e in entries if e.table_name.startswith(REMOTE_TABLE_PREFIX)]
        assert remote[0].prior_value is not None
        assert remote[0].prior_value["track_ids"] == []
