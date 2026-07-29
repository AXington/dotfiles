"""AC-L4 / AC-P1: the resolved selection is authoritative, and a routed ``sync``
push never corrupts local lock state.

Under the plan/apply architecture (spec §7.1) ``sync`` owns ONLY the forward-only
remote push. It reconciles read-only through ``build_sync_plan`` (which calls
``reconcile_track`` — honouring the per-playlist effective lock and any cached
user-approved mapping) and pushes only confidently-resolved ids. It writes NOTHING
to the local ``platform_tracks`` / ``playlist_track_mappings`` tables, so it can no
longer clobber a global default nor wipe a held lock. The old "auto-reorder re-push
re-reads global mappings and resurrects an excluded release" bypass is eliminated by
construction: there is now a single push, built once from the resolved set.

These are the routed-sync regression guards for the Chunk 5 lock guarantees:
- a user-rejected push applies nothing (interactive control preserved),
- an approved selection is pushed (in auto-reorder arc order),
- a per-playlist override lock does not clobber the global default,
- a held/dead lock (``not_found``) is neither wiped nor pushed.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from tuneshift.commands.sync_cmd import handle_sync
from tuneshift.db import Database
from tuneshift.matching.audit import ReasonCode
from tuneshift.models import PlatformMapping, PlaylistInfo, Track
from tuneshift.reconcile import ReconcileResult


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.platform_name = "tidal"
    client.load_session.return_value = True
    client.find_playlist_by_name.return_value = PlaylistInfo(
        platform_id="tidal-pl-1", name="Reorder Me", num_tracks=1,
    )
    # A remote read that can't be enumerated (MagicMock isn't iterable) makes the
    # plan treat prior remote order as unknown, so it never idempotently skips.
    # An absent remote is an EMPTY READABLE playlist, not one whose read
    # raises. Raising here modelled the read-fails/write-succeeds asymmetry
    # that SYNC-WIPE needs, so the suite normalised the very state it
    # should have caught.
    client.get_playlist_tracks.return_value = []
    client.replace_playlist_tracks.return_value = None
    return client


def _pushed_ids(client: MagicMock) -> list[str]:
    """Every id passed to replace_playlist_tracks across all calls."""
    ids: list[str] = []
    for call in client.replace_playlist_tracks.call_args_list:
        ids.extend(call.args[1])
    return ids


def _args(**overrides: object) -> Namespace:
    """Routed-sync args: ``--apply`` performs the push (plan-by-default pushes
    nothing, AC-P1); ``interactive`` steps through each push (AC-P2)."""
    base = dict(
        playlist="Reorder Me", platform="tidal", all=False,
        reconcile=False, apply=True, interactive=False,
    )
    base.update(overrides)
    return Namespace(**base)


def _setup(tmp_db: Path) -> tuple[Database, int, int]:
    db = Database(tmp_db)
    pid = db.create_playlist("Reorder Me")
    tid = db.insert_track(Track(title="Song", artist="Artist"))
    db.add_track_to_playlist(pid, tid, 0)
    db.link_platform_playlist(pid, "tidal", "tidal-pl-1")
    db.set_auto_reorder(pid, True, "wave")
    return db, pid, tid


@patch("tuneshift.sequencer.sequence_playlist")
@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_interactive_reject_pushes_nothing(
    mock_load, mock_reconcile, mock_sequence, tmp_db: Path
) -> None:
    """AC-P2: rejecting the push at the interactive prompt applies nothing.

    In the routed model a divergent substitute is surfaced per-track by the
    ``doctor`` / ``plan rematch`` review; ``sync`` is the forward-only push and the
    user retains a final veto over it via ``--interactive``. A rejected push must
    push nothing — the reorder cannot resurrect it because there is a single push.
    """
    db, pid, tid = _setup(tmp_db)
    # A second track so the arc order (reversed below) differs from playlist
    # order — this makes the "local order unchanged" assertion an independent
    # discriminator for a wrongly-persisted reorder, not a tautology.
    tid2 = db.insert_track(Track(title="Song B", artist="Artist"))
    db.add_track_to_playlist(pid, tid2, 1)
    client = _mock_client()
    mock_load.return_value = client
    mock_sequence.return_value = [tid2, tid]  # arc order != playlist order
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="DIVERGENT_ID", score=90, confidence="high",
        is_divergent=True, divergence_note="live version", from_cache=False,
    )

    args = _args(interactive=True)
    with patch("builtins.input", return_value="n"):
        handle_sync(args, db)

    assert _pushed_ids(client) == []
    assert "DIVERGENT_ID" not in _pushed_ids(client)
    # A rejected push must not record the playlist as synced nor persist the
    # auto-reorder locally — otherwise local/remote silently diverge with no push.
    assert db.get_last_synced(pid, "tidal") is None
    assert [t.id for t in db.get_playlist_tracks(pid)] == [tid, tid2]


@patch("tuneshift.sequencer.sequence_playlist")
@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_apply_pushes_approved_selection_in_arc_order(
    mock_load, mock_reconcile, mock_sequence, tmp_db: Path
) -> None:
    """An approved match is pushed once, in the auto-reorder arc order (AC-L4).

    The old monolithic sync pushed twice (main push + a separate reorder re-push
    that re-read global mappings). The routed push builds the arc order read-only
    and performs a SINGLE push of the resolved set — no second, independent read.
    """
    db, pid, tid = _setup(tmp_db)
    client = _mock_client()
    mock_load.return_value = client
    mock_sequence.return_value = [tid]
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="RESOLVED_ID", score=98, confidence="high",
    )

    handle_sync(_args(), db)

    assert _pushed_ids(client) == ["RESOLVED_ID"]
    # The durable local reorder is persisted only after a successful apply.
    assert [t.id for t in db.get_playlist_tracks(pid)] == [tid]


@patch("tuneshift.sequencer.sequence_playlist")
@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_playlist_override_sync_does_not_clobber_global_default(
    mock_load, mock_reconcile, mock_sequence, tmp_db: Path
) -> None:
    """BLOCKER-1 regression: syncing a playlist with a per-playlist override lock
    must NOT rewrite the library-wide ``platform_tracks`` default.

    The routed push writes nothing local, so the global default other playlists
    rely on is untouched by construction; the override id is what gets pushed.
    """
    db, pid, tid = _setup(tmp_db)
    db.upsert_platform_mapping(PlatformMapping(
        track_id=tid, platform="tidal", platform_track_id="GLOBAL_ID",
        match_score=97, status="matched", user_approved=True,
    ))
    db.set_playlist_track_mapping(
        pid, tid, "tidal", "OVERRIDE_ID", source="locked", user_approved=True
    )
    client = _mock_client()
    mock_load.return_value = client
    mock_sequence.return_value = [tid]
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="OVERRIDE_ID", score=98, confidence="high",
    )

    handle_sync(_args(), db)

    assert db.get_platform_mapping(tid, "tidal").platform_track_id == "GLOBAL_ID"
    assert db.get_playlist_track_mapping(pid, tid, "tidal")["platform_track_id"] == "OVERRIDE_ID"
    assert "OVERRIDE_ID" in _pushed_ids(client)


@patch("tuneshift.sequencer.sequence_playlist")
@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_sync_holds_dead_lock_without_wiping_mapping(
    mock_load, mock_reconcile, mock_sequence, tmp_db: Path
) -> None:
    """BLOCKER-2a regression: a lock whose platform id has gone dead
    (``not_found`` / ``LOCK_HELD``) must be neither wiped nor pushed.

    The routed push writes nothing local (so the lock mapping is intact) and omits
    ``not_found`` results from the push. Routed self-heal (``plan heal``, AC-L3)
    owns any remediation — sync never destroys the lock.
    """
    db, pid, tid = _setup(tmp_db)
    db.upsert_platform_mapping(PlatformMapping(
        track_id=tid, platform="tidal", platform_track_id="DEAD_LOCK",
        match_score=97, status="matched", user_approved=True,
    ))
    client = _mock_client()
    mock_load.return_value = client
    mock_sequence.return_value = [tid]
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="", score=0, confidence="not_found",
        reason_code=ReasonCode.LOCK_HELD,
    )

    handle_sync(_args(), db)

    mapping = db.get_platform_mapping(tid, "tidal")
    assert mapping.platform_track_id == "DEAD_LOCK"
    assert mapping.status == "matched"
    assert _pushed_ids(client) == []


@patch("tuneshift.sequencer.sequence_playlist")
@patch("tuneshift.planapply.sync.reconcile_track")
@patch("tuneshift.commands.ingest_cmd._load_client")
def test_ordinary_sync_does_not_wipe_held_unavailable_lock(
    mock_load, mock_reconcile, mock_sequence, tmp_db: Path
) -> None:
    """Regression: an ordinary sync must not destroy a lock already held in the
    ``unavailable`` state that ``plan heal`` produced.

    ``reconcile_track`` returns ``not_found`` with reason_code ``LOCKED``. The
    routed push writes nothing local, so the held id survives (a future heal can
    still act) and nothing is pushed.
    """
    db, pid, tid = _setup(tmp_db)
    db.upsert_platform_mapping(PlatformMapping(
        track_id=tid, platform="tidal", platform_track_id="HELD_LOCK",
        match_score=0, status="unavailable", user_approved=True,
    ))
    client = _mock_client()
    mock_load.return_value = client
    mock_sequence.return_value = [tid]
    mock_reconcile.return_value = ReconcileResult(
        platform_track_id="", score=0, confidence="not_found",
        reason_code=ReasonCode.LOCKED,
    )

    handle_sync(_args(), db)

    mapping = db.get_platform_mapping(tid, "tidal")
    assert mapping.platform_track_id == "HELD_LOCK"
    assert db.get_effective_lock(tid, "tidal", pid) is not None
    assert _pushed_ids(client) == []
