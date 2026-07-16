"""Migration of stale/abandoned global mappings as a plan (AC-P5).

The ~254 abandoned/stale ``platform_tracks`` mappings are migrated through the
SAME plan/apply engine as everything else, no separate clobbering path. The
plan carries, per track, ``current -> proposed`` with a reason and a
:attr:`~tuneshift.planapply.models.PlanChange.classification`:

- ``improved``, the engine re-resolves the track confidently to a DIFFERENT
  release. This is the only actionable change; nothing else is ever applied.
- ``unchanged``, re-resolves to the same id (a no-op), or the row is
  ``user_approved`` and therefore bypassed for guaranteed zero regressions.
- ``needs-human-judgment``, the engine cannot confidently improve the mapping,
  so it is left as-is for the triage surface rather than silently rewritten.

Success is not "zero changes" or an automatic threshold: it is
Alice-approved-plan-applied. Rollback (AC-P4) restores any applied batch.
"""

from __future__ import annotations

from tuneshift.db import Database
from tuneshift.planapply.models import Plan, PlanChange, row_key_for
from tuneshift.planapply.plan import new_plan_id
from tuneshift.platforms.protocol import MusicPlatformClient
from tuneshift.reconcile import reconcile_track

_CONFIDENT = "high"


def _mapping_row(
    track_id: int,
    platform: str,
    platform_track_id: str,
    status: str,
    user_approved: int,
) -> dict:
    return {
        "track_id": track_id,
        "platform": platform,
        "platform_track_id": platform_track_id,
        "status": status,
        "user_approved": user_approved,
    }


def _candidate_track_ids(db: Database, platform: str) -> list[int]:
    rows = db.conn.execute(
        "SELECT track_id FROM platform_tracks WHERE platform = ? ORDER BY track_id",
        (platform,),
    ).fetchall()
    return [row["track_id"] for row in rows]


def build_migration_plan(
    db: Database,
    client: MusicPlatformClient,
    *,
    platform: str = "tidal",
    track_ids: list[int] | None = None,
    force: bool = True,
) -> Plan:
    """Plan the re-resolution of existing global mappings (AC-P5).

    ``user_approved`` rows are bypassed (listed as ``unchanged`` + locked, never
    applied). Non-approved rows are re-reconciled read-only: a confident,
    different result is an actionable ``improved`` change; a same-id result is
    ``unchanged``; anything the engine cannot confidently improve is
    ``needs-human-judgment`` and left as-is.
    """
    candidates = (
        track_ids if track_ids is not None else _candidate_track_ids(db, platform)
    )

    changes: list[PlanChange] = []
    change_id = 0
    for track_id in candidates:
        current = db.get_platform_mapping(track_id, platform)
        if current is None:
            continue
        change_id += 1
        row_key = row_key_for(track_id=track_id, platform=platform)
        current_state = _mapping_row(
            track_id,
            platform,
            current.platform_track_id,
            current.status,
            1 if current.user_approved else 0,
        )

        if current.user_approved:
            # Bypassed: listed for transparency, never applied (guaranteed zero
            # regressions on approved/locked rows).
            changes.append(
                PlanChange(
                    op="update",
                    table="platform_tracks",
                    row_key=row_key,
                    current=current_state,
                    proposed=current_state,
                    reason="user-approved: bypassed by migration",
                    provenance="migration",
                    classification="unchanged",
                    locked=True,
                    change_id=change_id,
                    status="skipped",
                )
            )
            continue

        result = reconcile_track(db, track_id, client, force=force, playlist_id=None)
        confident = result.confidence == _CONFIDENT and bool(result.platform_track_id)

        if confident and result.platform_track_id != current.platform_track_id:
            proposed = _mapping_row(
                track_id, platform, result.platform_track_id, "matched", 0
            )
            changes.append(
                PlanChange(
                    op="update",
                    table="platform_tracks",
                    row_key=row_key,
                    current=current_state,
                    proposed=proposed,
                    reason=(
                        f"re-resolved {current.platform_track_id} -> "
                        f"{result.platform_track_id} (score {result.score})"
                    ),
                    provenance="migration",
                    classification="improved",
                    change_id=change_id,
                )
            )
        elif confident:
            changes.append(
                PlanChange(
                    op="update",
                    table="platform_tracks",
                    row_key=row_key,
                    current=current_state,
                    proposed=current_state,
                    reason="re-resolved to the same release",
                    provenance="migration",
                    classification="unchanged",
                    change_id=change_id,
                    status="skipped",
                )
            )
        else:
            changes.append(
                PlanChange(
                    op="update",
                    table="platform_tracks",
                    row_key=row_key,
                    current=current_state,
                    proposed=current_state,
                    reason=(
                        f"low-confidence re-resolution ({result.confidence}); "
                        "left as-is for triage"
                    ),
                    provenance="migration",
                    classification="needs-human-judgment",
                    change_id=change_id,
                    status="skipped",
                )
            )

    return Plan(
        plan_id=new_plan_id(),
        kind="migration",
        scope=f"platform:{platform}",
        changes=changes,
    )


def migration_summary(plan: Plan) -> dict[str, int]:
    """Count changes by classification for the plan's summary line (AC-P5)."""
    summary = {"improved": 0, "unchanged": 0, "needs-human-judgment": 0}
    for change in plan.changes:
        summary[change.classification] = summary.get(change.classification, 0) + 1
    return summary
