"""Re-match plan builder (AC-P1, routing table row "Re-doctor / re-match").

A per-playlist re-match is ROUTED: it never mutates a playlist mapping inline.
Instead it runs the selection engine for every track on the playlist (via
``reconcile_track``, which already honours per-playlist preferences and locks)
and emits ``playlist_track_mappings`` ``current -> proposed`` changes into a
:class:`~tuneshift.planapply.models.Plan`. The plan is then reviewed and applied
through the shared apply engine, so re-matching gets journaling, rollback, and
the AC-P3 locked-row exclusion for free.
"""

from __future__ import annotations

from tuneshift.db import Database
from tuneshift.planapply.models import Plan, PlanChange, row_key_for
from tuneshift.planapply.plan import assign_change_ids, new_plan_id
from tuneshift.reconcile import check_lock_downgrade, reconcile_track

# Confidence levels a re-match will actually propose applying. Anything else is
# surfaced for human judgement rather than written.
_CONFIDENT = frozenset({"high"})


def build_rematch_plan(
    db: Database,
    playlist_id: int,
    client: object,
    *,
    platform: str = "tidal",
    force: bool = False,
) -> Plan:
    """Build (but do not apply) a re-match plan for one playlist.

    For each track: run the selection engine, compare the proposed release to the
    current per-playlist mapping (falling back to the global mapping as the
    baseline), and emit a change. Confident improvements are actionable; matches
    equal to the current release are ``unchanged`` no-ops; ambiguous/not-found
    results are ``needs-human-judgment`` and left for triage.
    """
    plan = Plan(
        plan_id=new_plan_id(),
        kind="rematch",
        scope=_playlist_scope(db, playlist_id),
    )

    for track in db.get_playlist_tracks(playlist_id):
        change = _change_for_track(
            db, playlist_id, track.id, client, platform, force=force
        )
        if change is not None:
            plan.changes.append(change)

    assign_change_ids(plan)
    return plan


def _playlist_scope(db: Database, playlist_id: int) -> str:
    name = db.get_playlist_name(playlist_id)
    return name if name is not None else str(playlist_id)


def _change_for_track(
    db: Database,
    playlist_id: int,
    track_id: int,
    client: object,
    platform: str,
    *,
    force: bool,
) -> PlanChange | None:
    playlist_mapping = db.get_playlist_track_mapping(playlist_id, track_id, platform)
    global_mapping = db.get_platform_mapping(track_id, platform)

    # Current DB state of the playlist row we would plan (for an accurate diff).
    current_id = ""
    if playlist_mapping is not None:
        current_id = playlist_mapping["platform_track_id"]
    elif global_mapping is not None:
        current_id = global_mapping.platform_track_id

    # The authoritative lock is the two-level EFFECTIVE lock - never raw row
    # precedence. An unapproved auto-matched playlist row does NOT shadow a global
    # lock (it falls through to the global default), so deriving "locked" from raw
    # rows would re-affirm the wrong release. ``locked_id`` is the release the lock
    # actually pins; the plan re-affirms that, not whatever id the row happens to
    # hold (AC-L1/L2/L4).
    effective_lock = db.get_effective_lock(track_id, platform, playlist_id)
    locked = effective_lock is not None
    locked_id = effective_lock.platform_track_id if effective_lock is not None else None

    op = "update" if playlist_mapping is not None else "insert"
    row_key = row_key_for(playlist_id=playlist_id, track_id=track_id, platform=platform)
    current_state = {"platform_track_id": current_id} if current_id else None

    # A locked row is never proposed for change (AC-L2): short-circuit to an
    # explicit "locked, skipped" change without running the engine. It carries
    # the locked release as its proposed state so an explicit include_locked
    # re-apply re-affirms the lock (user_approved stays set) rather than
    # downgrading it, and apply reports it under skipped_locked.
    if locked:
        proposed = (
            {
                "playlist_id": playlist_id,
                "track_id": track_id,
                "platform": platform,
                "platform_track_id": locked_id,
                "source": "locked",
                "user_approved": 1,
            }
            if locked_id
            else None
        )
        # AC-L5: the locked id still exists but its metadata may have degraded so
        # it no longer satisfies an active preference (e.g. Tidal dropped Atmos).
        # Flag it for the user - the lock is HELD, never silently broken nor
        # silently accepted-as-degraded.
        downgrades = (
            check_lock_downgrade(
                db,
                track_id,
                client,
                platform=platform,
                playlist_id=playlist_id,
                locked_id=locked_id,
            )
            if locked_id
            else []
        )
        if downgrades:
            return PlanChange(
                op=op,
                table="playlist_track_mappings",
                row_key=row_key,
                current=current_state,
                proposed=proposed,
                reason="; ".join(d.describe() for d in downgrades),
                provenance="effective_lock",
                classification="downgrade-flag",
                locked=True,
                status="skipped",
            )
        return PlanChange(
            op=op,
            table="playlist_track_mappings",
            row_key=row_key,
            current=current_state,
            proposed=proposed,
            reason="locked, protected from re-match (AC-L2)",
            provenance="effective_lock",
            classification="locked",
            locked=True,
        )

    result = reconcile_track(db, track_id, client, force=force, playlist_id=playlist_id)

    # Not confidently matched -> never write; surface for human judgement.
    if result.confidence not in _CONFIDENT or not result.platform_track_id:
        return PlanChange(
            op=op,
            table="playlist_track_mappings",
            row_key=row_key,
            current=current_state,
            proposed=None,
            reason=f"no confident match ({result.confidence})",
            provenance="reconcile_track",
            classification="needs-human-judgment",
            locked=locked,
            status="skipped",
        )

    proposed_id = result.platform_track_id
    if proposed_id == current_id:
        return PlanChange(
            op=op,
            table="playlist_track_mappings",
            row_key=row_key,
            current=current_state,
            proposed={
                "playlist_id": playlist_id,
                "track_id": track_id,
                "platform": platform,
                "platform_track_id": proposed_id,
                "source": result.match_type or "matched",
                "user_approved": 0,
            },
            reason="already matched to the proposed release",
            provenance="reconcile_track",
            classification="unchanged",
            locked=locked,
            status="skipped",
        )

    return PlanChange(
        op=op,
        table="playlist_track_mappings",
        row_key=row_key,
        current=current_state,
        proposed={
            "playlist_id": playlist_id,
            "track_id": track_id,
            "platform": platform,
            "platform_track_id": proposed_id,
            "source": result.match_type or "matched",
            "user_approved": 0,
        },
        reason=_improve_reason(current_id, result),
        provenance="reconcile_track",
        classification="improved",
        locked=locked,
    )


def _improve_reason(current_id: str, result) -> str:
    if not current_id:
        return f"first confident match -> {result.platform_track_id}"
    return (
        f"re-matched {current_id} -> {result.platform_track_id} (score {result.score})"
    )
