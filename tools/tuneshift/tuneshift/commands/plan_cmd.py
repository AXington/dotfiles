"""The ``plan`` command: generate, inspect, resolve, apply, and roll back plans.

This is the user-facing surface of the plan/apply engine (ACs P1-P5). Mutating
routes (``sync``, ``rematch``, ``migrate``) generate a durable plan file and
apply NOTHING on their own (AC-P1). The plan can be inspected (``show``), pruned
(``reject``), applied (``apply``, optionally ``--interactive`` / stepping
accept-reject, AC-P2), and reversed (``rollback``, AC-P4 — local changes replay
the journal; remote pushes yield a compensating plan).

The broader per-command surface (prefs/lock/explain/triage) lands in Chunk 6;
this wires the migrate + re-match + sync routes.
"""

from __future__ import annotations

import sys

from tuneshift.db import Database
from tuneshift.planapply.apply import apply_plan, rollback_plan
from tuneshift.planapply.heal import build_heal_plan
from tuneshift.planapply.migrate import build_migration_plan, migration_summary
from tuneshift.planapply.models import Plan, PlanChange
from tuneshift.planapply.plan import (
    PlanError,
    list_plans,
    read_plan,
    reject_change,
    write_plan,
)
from tuneshift.planapply.rematch import build_rematch_plan
from tuneshift.planapply.sync import (
    build_compensating_plan,
    build_sync_plan,
    make_sync_executor,
)


def handle_plan(args, db: Database) -> int:
    """Dispatch a ``plan`` subcommand."""
    action = getattr(args, "action", None)
    dispatch = {
        "sync": _generate_sync,
        "rematch": _generate_rematch,
        "migrate": _generate_migrate,
        "heal": _generate_heal,
        "list": _list,
        "show": _show,
        "reject": _reject,
        "apply": _apply,
        "rollback": _rollback,
    }
    handler = dispatch.get(action)
    if handler is None:
        print(
            "Usage: tuneshift plan {sync|rematch|migrate|heal|list|show|reject|apply|rollback}",
            file=sys.stderr,
        )
        return 1
    return handler(args, db)


# --- plan generation (AC-P1: writes a plan, applies nothing) -----------------


def _require_client(platform: str):
    from tuneshift.commands.ingest_cmd import _load_client

    client = _load_client(platform)
    if client is None:
        print(f"Unknown platform: {platform}", file=sys.stderr)
        return None
    if not client.load_session():
        print(
            f"Not logged in to {platform}. Run: tuneshift login {platform}",
            file=sys.stderr,
        )
        return None
    return client


def _resolve_playlist_id(db: Database, name: str) -> int | None:
    playlist = db.find_playlist_by_name(name)
    if playlist is None:
        print(f'Playlist not found: "{name}"', file=sys.stderr)
        return None
    return playlist.id


def _finish_generation(db: Database, plan: Plan, label: str) -> int:
    if plan.is_empty():
        print(f"{label}: nothing to do (plan is empty).")
        return 0
    path = write_plan(db.path, plan)
    print(
        f"{label}: wrote plan {plan.plan_id} ({len(plan.actionable_changes())} "
        f"actionable change(s))."
    )
    print(f"  file: {path}")
    print(f"  review: tuneshift plan show {plan.plan_id}")
    print(f"  apply:  tuneshift plan apply {plan.plan_id}")
    return 0


def _generate_sync(args, db: Database) -> int:
    playlist_id = _resolve_playlist_id(db, args.playlist)
    if playlist_id is None:
        return 1
    client = _require_client(args.platform)
    if client is None:
        return 1
    plan = build_sync_plan(
        db,
        playlist_id,
        client,
        platform=args.platform,
        force=getattr(args, "reconcile", False),
    )
    return _finish_generation(db, plan, f'sync "{args.playlist}"')


def _generate_rematch(args, db: Database) -> int:
    playlist_id = _resolve_playlist_id(db, args.playlist)
    if playlist_id is None:
        return 1
    client = _require_client(args.platform)
    if client is None:
        return 1
    plan = build_rematch_plan(
        db,
        playlist_id,
        client,
        platform=args.platform,
        force=getattr(args, "reconcile", False),
    )
    return _finish_generation(db, plan, f'rematch "{args.playlist}"')


def _generate_migrate(args, db: Database) -> int:
    client = _require_client(args.platform)
    if client is None:
        return 1
    plan = build_migration_plan(db, client, platform=args.platform)
    summary = migration_summary(plan)
    print(
        f"migrate {args.platform}: {summary['improved']} improved, "
        f"{summary['unchanged']} unchanged, "
        f"{summary['needs-human-judgment']} need human judgment."
    )
    return _finish_generation(db, plan, f"migrate {args.platform}")


def _generate_heal(args, db: Database) -> int:
    """Plan a routed self-heal of dead identity locks (AC-L3).

    A locked release that has gone dead is never silently swapped: this proposes
    re-binding to a same-recording equivalent (or holding it as unavailable) into
    a reviewable plan. Optionally scoped to a single playlist's override locks.
    """
    client = _require_client(args.platform)
    if client is None:
        return 1
    playlist_id = None
    label = f"heal {args.platform}"
    playlist = getattr(args, "playlist", None)
    if playlist:
        playlist_id = _resolve_playlist_id(db, playlist)
        if playlist_id is None:
            return 1
        label = f'heal "{playlist}"'
    plan = build_heal_plan(db, client, platform=args.platform, playlist_id=playlist_id)
    return _finish_generation(db, plan, label)


# --- plan inspection & editing -----------------------------------------------


def _list(args, db: Database) -> int:
    ids = list_plans(db.path)
    if not ids:
        print("No saved plans.")
        return 0
    for plan_id in ids:
        try:
            plan = read_plan(db.path, plan_id)
        except PlanError:
            continue
        actionable = len(plan.actionable_changes())
        print(f"{plan_id}  {plan.kind:12s}  {actionable} actionable  {plan.scope}")
    return 0


def _format_change(change: PlanChange) -> str:
    marks = []
    if change.locked:
        marks.append("LOCKED")
    if change.remote:
        marks.append("REMOTE")
    mark = f" [{','.join(marks)}]" if marks else ""
    lines = [
        f"  #{change.change_id} [{change.status}] {change.classification}{mark} "
        f"{change.op} {change.table}",
        f"      reason: {change.reason}",
    ]
    if change.current is not None:
        lines.append(f"      current:  {change.current}")
    if change.proposed is not None:
        lines.append(f"      proposed: {change.proposed}")
    return "\n".join(lines)


def _load(db: Database, plan_id: str) -> Plan | None:
    try:
        return read_plan(db.path, plan_id)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _show(args, db: Database) -> int:
    plan = _load(db, args.plan_id)
    if plan is None:
        return 1
    print(f"Plan {plan.plan_id} ({plan.kind}) — {plan.scope}")
    for change in plan.changes:
        print(_format_change(change))
    return 0


def _reject(args, db: Database) -> int:
    plan = _load(db, args.plan_id)
    if plan is None:
        return 1
    try:
        reject_change(plan, args.change_id)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_plan(db.path, plan)
    print(f"Rejected change #{args.change_id} in plan {plan.plan_id}.")
    return 0


# --- apply & rollback --------------------------------------------------------


def _make_executor_if_needed(db: Database, plan: Plan):
    """Build a remote executor if the plan pushes to a platform, else None."""
    remote_changes = [c for c in plan.changes if c.op == "remote_push"]
    if not remote_changes:
        return None
    platform = (remote_changes[0].proposed or {}).get("platform", "tidal")
    client = _require_client(platform)
    if client is None:
        return None
    return make_sync_executor(db, client, platform=platform)


def _interactive_prune(plan: Plan) -> None:
    """Step through actionable changes, rejecting the ones the user declines."""
    for change in plan.actionable_changes(include_locked=True):
        print(_format_change(change))
        choice = input("  Apply this change? [Y/n] ").strip().lower()
        if choice in ("n", "no"):
            change.status = "rejected"


def _apply(args, db: Database) -> int:
    plan = _load(db, args.plan_id)
    if plan is None:
        return 1

    if getattr(args, "interactive", False):
        _interactive_prune(plan)

    remote_changes = [c for c in plan.changes if c.op == "remote_push"]
    executor = None
    if remote_changes:
        executor = _make_executor_if_needed(db, plan)
        if executor is None:
            return 1

    report = apply_plan(
        db,
        plan,
        include_locked=getattr(args, "include_locked", False),
        remote_executor=executor,
    )
    # Persist status changes (applied/skipped/rejected) back to the plan file.
    write_plan(db.path, plan)

    print(
        f"Applied {report.applied}, skipped {report.skipped}, "
        f"locked-skipped {report.skipped_locked}, failed {report.failed}."
    )
    if report.skipped_locked:
        print(
            "  Locked changes were skipped. Re-run with --include-locked to apply them."
        )
    for err in report.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 1 if report.failed else 0


def _rollback(args, db: Database) -> int:
    report = rollback_plan(db, args.plan_id)
    print(f"Reverted {report.reverted} local change(s).")
    if report.remote_skipped:
        comp = build_compensating_plan(report)
        write_plan(db.path, comp)
        print(
            f"  {report.remote_skipped} remote push(es) are forward-only and were "
            f"NOT un-pushed."
        )
        print(f"  Wrote compensating plan {comp.plan_id} to undo them:")
        print(f"    review: tuneshift plan show {comp.plan_id}")
        print(f"    apply:  tuneshift plan apply {comp.plan_id}")
    return 0
