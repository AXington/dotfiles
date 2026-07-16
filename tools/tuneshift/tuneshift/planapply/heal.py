"""Routed lock self-heal (AC-L3, section 7.1 routing row "Lock self-heal").

A durable identity lock must never be silently swapped. When a locked release is
found to have genuinely disappeared, :func:`build_heal_plan` PROPOSES the outcome
into a reviewable :class:`~tuneshift.planapply.models.Plan` instead of mutating
the mapping inline:

- locked id alive / undeterminable -> no change,
- locked id dead + a same-recording equivalent is live -> propose a re-bind
  (surfaced for review; the change touches a locked row, so it applies only under
  an explicit ``include_locked`` apply, the reviewed "yes, heal it"),
- locked id dead + no equivalent -> propose holding it unavailable (global scope)
  or flag it for human judgement (per-playlist scope, which has no status column).

This is the EXPLICIT exception to section 6's "no live search": a dead-lock verification
may fetch fresh candidates to find the same-recording replacement, but the write
is always routed through plan/apply.
"""

from __future__ import annotations

import json

from tuneshift.db import Database
from tuneshift.planapply.models import Plan, PlanChange, row_key_for
from tuneshift.planapply.plan import assign_change_ids, new_plan_id
from tuneshift.reconcile import (
    _find_equivalent_candidate,
    _fingerprint_for_track,
    _locked_id_alive,
    _mapping_from_effective,
)

#: AC-L3 documented config constant: same-recording self-heal tolerates a small
#: duration delta (reissues/remasters differ by a few seconds). Enforced via the
#: fingerprint duration bucket in ``_find_equivalent_candidate``.
SELF_HEAL_DURATION_TOLERANCE = 2  # seconds


def build_heal_plan(
    db: Database,
    client: object,
    *,
    platform: str = "tidal",
    playlist_id: int | None = None,
    track_ids: list[int] | None = None,
) -> Plan:
    """Build (but do not apply) a self-heal plan for locked tracks.

    Scope resolution: explicit ``track_ids`` win; else all locked tracks on
    ``playlist_id``; else every track carrying a global default lock for
    ``platform``.
    """
    plan = Plan(
        plan_id=new_plan_id(),
        kind="heal",
        scope=_scope(db, playlist_id),
    )
    for track_id in _target_track_ids(db, platform, playlist_id, track_ids):
        change = _heal_change(db, track_id, client, platform, playlist_id)
        if change is not None:
            plan.changes.append(change)
    assign_change_ids(plan)
    return plan


def _scope(db: Database, playlist_id: int | None) -> str:
    if playlist_id is None:
        return "global"
    row = db.conn.execute(
        "SELECT name FROM playlists WHERE id = ?", (playlist_id,)
    ).fetchone()
    return row["name"] if row is not None else str(playlist_id)


def _target_track_ids(
    db: Database,
    platform: str,
    playlist_id: int | None,
    track_ids: list[int] | None,
) -> list[int]:
    if track_ids is not None:
        return track_ids
    if playlist_id is not None:
        return [t.id for t in db.get_playlist_tracks(playlist_id)]
    rows = db.conn.execute(
        "SELECT track_id FROM platform_tracks WHERE platform = ? AND user_approved = 1",
        (platform,),
    ).fetchall()
    return [row["track_id"] for row in rows]


def _heal_change(
    db: Database,
    track_id: int,
    client: object,
    platform: str,
    playlist_id: int | None,
) -> PlanChange | None:
    eff = db.get_effective_lock(track_id, platform, playlist_id)
    if eff is None:
        return None  # not locked -> nothing to heal

    alive = _locked_id_alive(client, eff.platform_track_id)
    if alive is not False:
        # Alive or undeterminable: honour the lock, propose nothing.
        return None

    track = db.get_track(track_id)
    mapping = _mapping_from_effective(track_id, platform, eff)
    target_fp = _fingerprint_for_track(track, mapping)
    cand = _find_equivalent_candidate(track, client, target_fp)

    is_playlist = eff.scope == "playlist"
    table = "playlist_track_mappings" if is_playlist else "platform_tracks"
    if is_playlist:
        row_key = row_key_for(
            playlist_id=playlist_id, track_id=track_id, platform=platform
        )
        current = {"platform_track_id": eff.platform_track_id}
    else:
        row_key = row_key_for(track_id=track_id, platform=platform)
        current = {"platform_track_id": eff.platform_track_id, "status": "matched"}

    if cand is not None:
        # Re-bind to the same recording under its new id (surfaced for review).
        if is_playlist:
            proposed = {
                "playlist_id": playlist_id,
                "track_id": track_id,
                "platform": platform,
                "platform_track_id": cand.platform_id,
                "source": "locked",
                "user_approved": 1,
            }
        else:
            proposed = {
                "track_id": track_id,
                "platform": platform,
                "platform_track_id": cand.platform_id,
                "status": "matched",
                "user_approved": 1,
                "fingerprint": json.dumps(target_fp.as_dict()),
            }
        return PlanChange(
            op="update",
            table=table,
            row_key=row_key,
            current=current,
            proposed=proposed,
            reason="lock_healed",
            provenance="build_heal_plan",
            classification="improved",
            locked=True,
        )

    # No equivalent recording is live - hold, never swap to a different recording.
    if is_playlist:
        # playlist_track_mappings has no availability column; surface for review.
        return PlanChange(
            op="update",
            table=table,
            row_key=row_key,
            current=current,
            proposed=None,
            reason="lock_held",
            provenance="build_heal_plan",
            classification="needs-human-judgment",
            locked=True,
            status="skipped",
        )
    return PlanChange(
        op="update",
        table=table,
        row_key=row_key,
        current=current,
        proposed={
            "track_id": track_id,
            "platform": platform,
            "platform_track_id": eff.platform_track_id,
            "status": "unavailable",
            "user_approved": 1,
        },
        reason="lock_held",
        provenance="build_heal_plan",
        classification="improved",
        locked=True,
    )
