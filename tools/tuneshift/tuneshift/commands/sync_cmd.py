"""Sync command: route a playlist's remote push through plan/apply (AC-P1).

Under the terraform-style plan/apply model (spec section 7.1) a sync's remote push is
ROUTED, never performed inline. By default ``sync`` writes a reviewable plan and
pushes NOTHING (AC-P1); ``--apply`` builds and applies the plan in one step for
daily use; ``--interactive`` (with ``--apply``) steps through each push before
applying (AC-P2). Rollback of a pushed plan is forward-only via a compensating
plan (``plan rollback`` -> ``plan apply``, AC-P4).

Scope boundary (section 7.1): this command owns only the remote push. Local mapping
reconcile/persist is the ROUTED ``doctor`` / ``plan rematch`` path and durable
sequencing is the ``order`` command. When a playlist has auto-reorder enabled the
push honors its arc order (computed read-only here), so the platform still
receives the sequenced order without this command mutating local state at plan
time; the durable local reorder is persisted only when ``--apply`` succeeds,
mirroring the previous inline behaviour.
"""

import sys

from tuneshift.db import Database
from tuneshift.planapply.apply import apply_plan
from tuneshift.planapply.models import Plan, PlanChange
from tuneshift.planapply.plan import write_plan
from tuneshift.planapply.sync import build_sync_plan, make_sync_executor

# _apply_sync_plan outcomes.
_PUSH_APPLIED = 0  # at least one push reached the platform
_PUSH_FAILED = 1  # a push was attempted and failed
_PUSH_NOOP = 2  # nothing applied (all changes rejected/skipped), no push


def handle_sync(args, db: Database) -> int:
    """Plan (default) or apply (``--apply``) a routed push of a playlist."""
    if args.all:
        any_failures = False
        synced_any = False
        for pl in db.list_playlists():
            platforms = db.get_linked_platforms(pl.id)
            if not platforms:
                continue
            synced_any = True
            if _sync_one(db, pl, platforms, args) != 0:
                any_failures = True
        if not synced_any:
            print("No linked playlists to sync.")
        return 1 if any_failures else 0

    if not args.playlist:
        print("Specify a playlist name or use --all.", file=sys.stderr)
        return 1

    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    if args.platform:
        platforms = [args.platform]
    else:
        platforms = db.get_linked_platforms(playlist.id)
        if not platforms:
            print(
                "No platforms linked. Specify: tuneshift sync <playlist> <platform>",
                file=sys.stderr,
            )
            return 1

    return _sync_one(db, playlist, platforms, args)


def _sync_one(db: Database, playlist, platforms, args) -> int:
    """Plan/apply the push of one playlist to one or more platforms."""
    from tuneshift.commands.ingest_cmd import _load_client

    tracks = db.get_playlist_tracks(playlist.id)
    if not tracks:
        print(f'Playlist "{playlist.name}" is empty.')
        return 0

    apply_mode = getattr(args, "apply", False)
    ordered_ids = _arc_order(db, playlist) if playlist.auto_reorder else None

    failed = False
    applied_any = False
    for platform_name in platforms:
        client = _load_client(platform_name)
        if not client:
            print(f"Unknown platform: {platform_name}", file=sys.stderr)
            continue
        if not client.load_session():
            print(
                f"Not logged in to {platform_name}. "
                f"Run: tuneshift login {platform_name}",
                file=sys.stderr,
            )
            continue

        plan = build_sync_plan(
            db,
            playlist.id,
            client,
            platform=platform_name,
            force=getattr(args, "reconcile", False),
            ordered_track_ids=ordered_ids,
        )
        label = f'sync "{playlist.name}" -> {platform_name}'

        if plan.is_empty() or not plan.actionable_changes():
            print(f"{label}: already in sync (nothing to push).")
            # BUG-6c: a confirmed no-op (the remote already holds exactly the ids
            # we would push) is a successful verification that the playlist is
            # synced, so record it. Otherwise status reports "never synced"
            # forever for a fully-pushed playlist. The all-unavailable no-op
            # (nothing resolved) is NOT a confirmed sync and is excluded.
            if _is_confirmed_in_sync(plan):
                db.mark_playlist_synced(playlist.id, platform_name)
            continue

        if not apply_mode:
            _write_and_report(db, plan, label, playlist.name, platform_name)
            continue

        rc = _apply_sync_plan(db, client, plan, platform_name, label, args)
        if rc == _PUSH_FAILED:
            failed = True
        elif rc == _PUSH_APPLIED:
            applied_any = True
            db.mark_playlist_synced(playlist.id, platform_name)
        # _PUSH_NOOP (nothing applied - e.g. interactively rejected): not a
        # failure, but the platform did NOT receive the push, so we must NOT
        # record it as synced.

    # Persist the durable local reorder only when a push actually reached a
    # platform, matching the previous inline "auto-reorder sticks on sync"
    # behaviour. A plan-only run or a fully-rejected apply never mutates local
    # order, so local order can't silently diverge from what was pushed.
    if apply_mode and applied_any and ordered_ids is not None:
        db.set_playlist_tracks(playlist.id, ordered_ids)
        print(f'  Auto-reordered "{playlist.name}" (arc={playlist.reorder_arc})')

    return 1 if failed else 0


def _arc_order(db: Database, playlist) -> list[int]:
    """Compute the auto-reorder arc order READ-ONLY (no local mutation)."""
    from tuneshift.sequencer import sequence_playlist

    return sequence_playlist(db, playlist.id, arc=playlist.reorder_arc)


def _write_and_report(
    db: Database, plan: Plan, label: str, playlist_name: str, platform_name: str
) -> None:
    """Write the plan and print review/apply guidance (AC-P1, applies nothing)."""
    path = write_plan(db.path, plan)
    n = len(plan.actionable_changes())
    print(f"{label}: wrote plan {plan.plan_id} ({n} push).")
    print(f"  file: {path}")
    print(f"  review: tuneshift plan show {plan.plan_id}")
    print(f"  apply:  tuneshift plan apply {plan.plan_id}")
    print(f'          (or: tuneshift sync "{playlist_name}" {platform_name} --apply)')


def _apply_sync_plan(
    db: Database, client, plan: Plan, platform_name: str, label: str, args
) -> int:
    """Apply a sync plan's remote push in one step (``--apply``).

    Returns one of ``_PUSH_APPLIED`` (a push reached the platform),
    ``_PUSH_FAILED`` (a push was attempted and failed), or ``_PUSH_NOOP``
    (nothing was applied, e.g. the user rejected every push interactively).
    The caller must distinguish these so a rejected push is never recorded as
    synced.
    """
    if getattr(args, "interactive", False):
        for change in plan.actionable_changes(include_locked=True):
            print(_describe_push(change))
            if input("  Apply this push? [Y/n] ").strip().lower() in ("n", "no"):
                change.status = "rejected"

    executor = make_sync_executor(db, client, platform=platform_name)
    report = apply_plan(db, plan, remote_executor=executor)
    write_plan(db.path, plan)

    if report.failed:
        for err in report.errors:
            print(f"  error: {err}", file=sys.stderr)
        print(
            f"{label}: push failed (applied {report.applied}, failed {report.failed}).",
            file=sys.stderr,
        )
        return _PUSH_FAILED
    if report.applied:
        print(f"{label}: applied ({report.applied} push).")
        return _PUSH_APPLIED
    print(f"{label}: nothing applied (skipped {report.skipped}).")
    return _PUSH_NOOP


def _describe_push(change: PlanChange) -> str:
    proposed = change.proposed or {}
    ids = proposed.get("track_ids") or []
    return f"  #{change.change_id} push {len(ids)} tracks to {proposed.get('platform')}"


def _is_confirmed_in_sync(plan: Plan) -> bool:
    """True when a no-op plan is a confirmed match against the live remote.

    The remote-push change is ``skipped`` for two reasons: the remote already
    holds exactly the ids we would push (a real in-sync verification), or nothing
    resolved this run (an all-unavailable no-op, not a sync). Only the former
    should stamp ``last_synced_at`` (BUG-6c), so require a non-empty proposed set
    that equals the current remote set.
    """
    for change in plan.changes:
        if not change.remote:
            continue
        proposed = (change.proposed or {}).get("track_ids") or []
        current = (change.current or {}).get("track_ids")
        if proposed and current == proposed:
            return True
    return False
