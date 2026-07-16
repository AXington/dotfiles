"""Doctor command: scan playlists for mapping issues and apply fixes.

Two modes:

- **Scan** (default): validate a playlist (or the whole DB with ``--all``)
  against Tidal, classify issues, resolve proposed fixes, and write a plan
  file. Read-only against the database; the plan is reviewed before applying.
- **Apply** (``--apply``): read the saved plan and apply the proposed fixes
  transactionally, with per-item overrides and a best-effort Tidal re-sync.

Exit codes:
    0  clean scan (no issues) / apply fully succeeded
    1  issues found (scan) / apply partially succeeded
    2  operational failure (auth, missing/malformed plan, unknown playlist)
"""

from __future__ import annotations

import sys

from tuneshift.db import Database
from tuneshift.doctor import applier as applier_mod
from tuneshift.doctor.plan import DoctorPlan, PlanError, read_plan, write_plan
from tuneshift.doctor.resolver import resolve_all
from tuneshift.doctor.scanner import scan_tracks
from tuneshift.enrichment.retry import RetryStats

_ISSUE_LABELS = {
    "unavailable": "Unavailable",
    "stale_album": "Stale album",
    "version_mismatch": "Version mismatch",
    "duplicate": "Duplicate",
    "unmapped": "Unmapped",
}


def handle_doctor(args, db: Database) -> int:
    if getattr(args, "orphans", False) or getattr(args, "enqueue_orphans", False):
        return _handle_orphans(args, db)
    if getattr(args, "apply", False):
        return _handle_apply(args, db)
    return _handle_scan(args, db)


def _handle_orphans(args, db: Database) -> int:
    """List orphaned tracks (no mapping, no queue, no quarantine); optionally enqueue.

    Read-only by default (needs no platform login). With --enqueue-orphans, each
    orphan is added to the resolution queue so a later `resolve` picks it up.
    """
    from tuneshift.doctor.scanner import detect_orphaned

    orphans = detect_orphaned(db)
    if not orphans:
        print("No orphaned tracks. Every track is resolved, queued, or quarantined.")
        return 0

    print(f"Orphaned tracks (unmapped, unqueued, not quarantined): {len(orphans)}")
    for track in orphans:
        print(f"  [{track.id}] {track.title} - {track.artist}")

    if getattr(args, "enqueue_orphans", False):
        for track in orphans:
            db.enqueue_resolution(track.id)
        print(
            f"\nEnqueued {len(orphans)} track(s) for resolution. "
            f"Run: tuneshift resolve --all"
        )
    else:
        print("\nRe-run with --enqueue-orphans to queue these for resolution.")
    return 0


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #


def _load_tidal_client():
    from tuneshift.commands.ingest_cmd import _load_client

    client = _load_client("tidal")
    if client is None:
        return None, "Unknown platform: tidal"
    if not client.load_session():
        return None, "Not logged in to Tidal. Run: tuneshift login tidal"
    return client, None


def _handle_scan(args, db: Database) -> int:
    scan_all = getattr(args, "all", False)
    playlist_name = getattr(args, "playlist", None)

    if not scan_all and not playlist_name:
        print("Specify a playlist name or use --all.", file=sys.stderr)
        return 2

    client, err = _load_tidal_client()
    if err:
        print(err, file=sys.stderr)
        return 2

    max_retries = getattr(args, "max_retries", 3)
    quiet = getattr(args, "quiet", False)
    stats = RetryStats()

    items = []
    next_id = 1
    if scan_all:
        playlists = db.list_playlists()
        scope = "all"
        for pl in playlists:
            tracks = db.get_playlist_tracks(pl.id)
            if not tracks:
                continue
            if not quiet:
                print(
                    f'Scanning "{pl.name}" ({len(tracks)} tracks)...', file=sys.stderr
                )
            pl_items, next_id = scan_tracks(
                db,
                client,
                tracks,
                pl.name,
                max_retries=max_retries,
                stats=stats,
                quiet=quiet,
                start_id=next_id,
            )
            items.extend(pl_items)
    else:
        playlist = db.find_playlist_by_name(playlist_name)
        if not playlist:
            print(f"Playlist not found: {playlist_name}", file=sys.stderr)
            return 2
        scope = playlist.name
        tracks = db.get_playlist_tracks(playlist.id)
        if not tracks:
            print(f'Playlist "{playlist.name}" is empty.')
            return 0
        if not quiet:
            print(
                f'Scanning "{playlist.name}" ({len(tracks)} tracks)...', file=sys.stderr
            )
        items, next_id = scan_tracks(
            db,
            client,
            tracks,
            playlist.name,
            max_retries=max_retries,
            stats=stats,
            quiet=quiet,
            start_id=next_id,
        )

    if not items:
        print("\nNo issues found. All mappings look healthy.")
        return 0

    if not quiet:
        print("\nResolving proposed fixes...", file=sys.stderr)
    resolve_all(db, client, items, quiet=quiet)

    plan = DoctorPlan(scope=scope, items=items)
    path = write_plan(db.path, plan)

    _print_plan(plan)
    print(f"\nPlan written to {path}")
    print(
        "Review it, then apply with:  tuneshift doctor --apply"
        + ("" if scan_all else f' "{scope}"')
    )
    return 1


def _print_plan(plan: DoctorPlan) -> None:
    print(f"\nFound {len(plan.items)} issue(s):\n")
    by_type: dict[str, list] = {}
    for item in plan.items:
        by_type.setdefault(item.issue, []).append(item)

    for issue, group in by_type.items():
        label = _ISSUE_LABELS.get(issue, issue)
        print(f"  {label} ({len(group)}):")
        for item in group:
            res = item.resolution
            marker = "auto" if res == "auto" else "MANUAL"
            detail = ""
            if item.issue == "duplicate":
                detail = f" -> keep #{item.keep_track_id}, merge {item.merge_track_ids}"
            elif item.proposed_platform_id:
                detail = f" -> {item.proposed_platform_id} (conf {item.confidence})"
            print(f"    [{item.id}] {item.title} - {item.artist}  [{marker}]{detail}")
            if item.note:
                print(f"        note: {item.note}")
    print()


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def _parse_overrides(raw: list[str] | None) -> tuple[dict[int, str], str | None]:
    """Parse ``ITEM_ID=TIDAL_ID`` override strings into a mapping."""
    overrides: dict[int, str] = {}
    for entry in raw or []:
        if "=" not in entry:
            return {}, f"Invalid --override (expected ITEM_ID=VALUE): {entry!r}"
        key, value = entry.split("=", 1)
        try:
            overrides[int(key)] = value
        except ValueError:
            return {}, f"Invalid --override item id: {key!r}"
    return overrides, None


def _handle_apply(args, db: Database) -> int:
    try:
        plan = read_plan(db.path)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    overrides, err = _parse_overrides(getattr(args, "override", None))
    if err:
        print(err, file=sys.stderr)
        return 2

    only = set(getattr(args, "only", None) or [])
    items = plan.actionable_items()
    if only:
        items = [i for i in items if i.id in only]
    if not items:
        print("Nothing to apply (plan has no actionable items).")
        return 0

    # Dry run: show the resolved apply set without touching the database.
    if getattr(args, "dry_run", False):
        preview = applier_mod.preview_apply(items, overrides)
        would_apply = sum(
            1 for _, action, _ in preview if action in ("auto", "override")
        )
        would_skip = sum(1 for _, action, _ in preview if action == "skip")
        print(
            f"\nDRY RUN - {len(items)} item(s), "
            f"{would_apply} would apply, {would_skip} would skip:\n"
        )
        for item, action, detail in preview:
            label = "APPLY " if action in ("auto", "override") else "SKIP  "
            tag = f"[{action}]"
            print(f"  {label}[{item.id}] {item.title} - {item.artist}  {tag} {detail}")
        print("\nNo changes made. Re-run without --dry-run to apply.")
        return 0

    _print_plan(DoctorPlan(scope=plan.scope, items=items))

    if not getattr(args, "yes", False):
        try:
            answer = input(f"Apply {len(items)} fix(es)? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    client, err = _load_tidal_client()
    if err:
        print(err, file=sys.stderr)
        return 2

    result = applier_mod.apply_plan(
        db,
        plan,
        items,
        overrides=overrides,
        client=client,
        do_sync=not getattr(args, "no_sync", False),
        quiet=getattr(args, "quiet", False),
    )

    # Persist updated statuses back to the plan file.
    write_plan(db.path, plan)

    print(
        f"\nApplied: {result.applied}  "
        f"No-sync: {result.no_sync}  "
        f"Failed: {result.failed}  "
        f"Skipped: {result.skipped}"
    )
    for item in items:
        if item.status in ("failed", "applied_no_sync", "skipped"):
            print(
                f"  [{item.id}] {item.status}: {item.title} - {item.artist}"
                + (f" ({item.note})" if item.note else "")
            )

    # Exit 0 only if every selected item applied cleanly (and synced). Failed,
    # skipped (manual awaiting override), or applied-without-sync items all
    # signal the plan is not fully resolved.
    fully_clean = result.failed == 0 and result.no_sync == 0 and result.skipped == 0
    return 0 if fully_clean else 1
