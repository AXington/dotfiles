"""The ``lock`` / ``unlock`` commands: identity locks at global + per-playlist scope.

An IdentityLock pins a track to a specific platform release so it survives a
re-doctor (spec §8, AC-L1/AC-L2). Two levels exist and are both exposed here:

- **global** (default): the library-wide default lock on ``platform_tracks``.
- **per-playlist** (``--playlist NAME``): an override lock on
  ``playlist_track_mappings`` that wins over the global lock for that playlist.

Per the §7.1 mutation-routing table a lock change is ROUTED: it produces a
reviewable, journaled plan rather than mutating inline. By default these verbs
WRITE A PLAN and apply nothing (AC-P1); ``--apply`` is the one-step
plan-then-apply convenience for daily use, and ``--interactive`` steps through
the change before applying. Because the user explicitly issued ``lock``/
``unlock``, ``--apply`` applies the (locked-row) change without a second
``--include-locked`` opt-in — the intent is already unambiguous.
"""

from __future__ import annotations

import sys

from tuneshift.commands.map_cmd import _extract_platform_args, _resolve_target_track
from tuneshift.db import Database
from tuneshift.planapply.apply import apply_plan
from tuneshift.planapply.builders import (
    build_global_lock_plan,
    build_global_unlock_plan,
    build_lock_plan,
    build_unlock_plan,
)
from tuneshift.planapply.models import Plan, PlanChange
from tuneshift.planapply.plan import write_plan


def handle_lock_list(args, db: Database) -> int:
    """List effective identity locks with precedence order (AC-CLI4).

    Without ``--playlist`` it lists the library-wide default locks. With
    ``--playlist NAME`` it shows both layers (global default + that playlist's
    override) and marks the effective winner per (track, platform): the
    per-playlist override wins over the global default (precedence
    global < playlist), mirroring ``prefs list``.
    """
    global_locks = db.get_global_locks()

    name = getattr(args, "playlist", None)
    if not name:
        _print_lock_layer("locks (global default)", global_locks, overridden=set())
        if not global_locks:
            print("No locks set.")
        return 0

    playlist = db.find_playlist_by_name(name)
    if playlist is None:
        print(f'Playlist not found: "{name}"', file=sys.stderr)
        return 1
    playlist_locks = db.get_playlist_locks(playlist.id)

    # A per-playlist override wins over the global default for the same
    # (track, platform); mark the shadowed global rows as overridden.
    override_keys = {(row["track_id"], row["platform"]) for row in playlist_locks}

    print(
        f'Effective locks (playlist "{name}"), precedence '
        "global < playlist (most specific wins):"
    )
    _print_lock_layer("global default", global_locks, overridden=override_keys)
    _print_lock_layer(f'playlist "{name}" override', playlist_locks, overridden=set())
    if not global_locks and not playlist_locks:
        print("  No locks set.")
    return 0


def _print_lock_layer(title: str, locks: list[dict], *, overridden: set) -> None:
    """Render one lock layer; mark rows shadowed by a more-specific override."""
    print(f"  [{title}]")
    if not locks:
        print("    (none)")
        return
    for lock in locks:
        key = (lock["track_id"], lock["platform"])
        shadowed = key in overridden
        marker = " " if shadowed else "*"
        note = "  (overridden)" if shadowed else ""
        print(
            f"    {marker} #{lock['track_id']} {lock['title']} — {lock['artist']}: "
            f"{lock['platform']}:{lock['platform_track_id']}{note}"
        )


def handle_lock(args, db: Database) -> int:
    """Create an identity lock (global default or per-playlist override)."""
    track = _resolve_target_track(args, db)
    if track is None:
        return 1
    platform, platform_id = _extract_platform_args(args)
    if not platform or not platform_id:
        print("Specify --tidal or --ytmusic with a track ID", file=sys.stderr)
        return 1

    playlist_id, scope_label = _resolve_scope(args, db)
    if playlist_id is False:  # scope requested but playlist not found
        return 1

    if playlist_id is None:
        plan = build_global_lock_plan(db, track.id, platform, platform_id)
    else:
        plan = build_lock_plan(db, playlist_id, track.id, platform, platform_id)

    label = f'lock "{track.title}" -> {platform}:{platform_id} ({scope_label})'
    return _emit_or_apply(args, db, plan, label)


def handle_unlock(args, db: Database) -> int:
    """Release an identity lock (global default or per-playlist override)."""
    track = _resolve_target_track(args, db)
    if track is None:
        return 1
    platform = _unlock_platform(args)
    if not platform:
        print("Specify --tidal or --ytmusic", file=sys.stderr)
        return 1

    playlist_id, scope_label = _resolve_scope(args, db)
    if playlist_id is False:
        return 1

    if playlist_id is None:
        plan = build_global_unlock_plan(db, track.id, platform)
    else:
        plan = build_unlock_plan(db, playlist_id, track.id, platform)

    label = f'unlock "{track.title}" on {platform} ({scope_label})'
    return _emit_or_apply(args, db, plan, label)


def _resolve_scope(args, db: Database):
    """Return ``(playlist_id_or_None, label)``; ``(False, ...)`` on error.

    Scope is explicit via ``--scope`` (default ``global``) so it is never
    inferred from the positional playlist used to locate the track. A
    ``--scope playlist`` lock is an override on ``playlist_track_mappings`` and
    needs the playlist name (the positional ``<playlist>``).
    """
    scope = getattr(args, "scope", "global") or "global"
    if scope == "global":
        return None, "global"
    name = getattr(args, "playlist", None)
    if not name:
        print(
            "--scope playlist requires a playlist name (the positional <playlist>).",
            file=sys.stderr,
        )
        return False, ""
    playlist = db.find_playlist_by_name(name)
    if playlist is None:
        print(f'Playlist not found: "{name}"', file=sys.stderr)
        return False, ""
    return playlist.id, f'playlist "{name}"'


def _unlock_platform(args) -> str | None:
    """Platform for unlock: accept the ``--tidal``/``--ytmusic`` flag with or
    without an id (releasing does not need the id)."""
    if getattr(args, "tidal", None):
        return "tidal"
    if getattr(args, "ytmusic", None):
        return "ytmusic"
    return None


def _emit_or_apply(args, db: Database, plan: Plan, label: str) -> int:
    """Write the plan (default) or apply it in one step (``--apply``)."""
    if plan.is_empty():
        print(f"{label}: nothing to do (plan is empty).")
        return 0

    if not getattr(args, "apply", False):
        path = write_plan(db.path, plan)
        print(
            f"{label}: wrote plan {plan.plan_id} "
            f"({len(plan.actionable_changes())} actionable change(s))."
        )
        print(f"  file: {path}")
        print(f"  review: tuneshift plan show {plan.plan_id}")
        print(f"  apply:  tuneshift plan apply {plan.plan_id} --include-locked")
        return 0

    if getattr(args, "interactive", False):
        for change in plan.actionable_changes(include_locked=True):
            print(_describe(change))
            if input("  Apply this change? [Y/n] ").strip().lower() in ("n", "no"):
                change.status = "rejected"

    # The user explicitly issued lock/unlock, so a locked-row change is intended.
    report = apply_plan(db, plan, include_locked=True)
    write_plan(db.path, plan)
    if report.applied:
        print(f"{label}: applied.")
    else:
        print(
            f"{label}: no changes applied "
            f"(skipped {report.skipped}, failed {report.failed})."
        )
    for err in report.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 1 if report.failed else 0


def _describe(change: PlanChange) -> str:
    return (
        f"  #{change.change_id} {change.op} {change.table}: {change.reason}\n"
        f"      current:  {change.current}\n"
        f"      proposed: {change.proposed}"
    )
