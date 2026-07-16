"""Doctor applier: apply a saved plan transactionally.

Each plan item is applied in its own transaction. If one item fails, its
changes roll back but previously applied items remain committed, partial
success is the expected outcome and is reflected in the plan file.

After the database changes, affected tracks are re-enriched and affected
playlists are pushed to Tidal, both best-effort: failures there warn but never
roll back a committed remap. Sync failures downgrade an item's status to
``applied_no_sync`` so the operator can retry with ``tuneshift sync``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

from tuneshift.db import Database
from tuneshift.doctor.plan import DoctorPlan, PlanItem
from tuneshift.enrichment import platform_metadata
from tuneshift.enrichment.retry import RetryConfig, RetryStats
from tuneshift.enrichment.retry import retry_api_call as _retry_api_call
from tuneshift.models import PlatformMapping

PLATFORM = "tidal"


class ApplyError(Exception):
    """Raised when a single plan item cannot be applied."""


@dataclass
class ApplyResult:
    applied: int = 0
    failed: int = 0
    skipped: int = 0
    no_sync: int = 0
    affected_playlists: set[str] = field(default_factory=set)


def preview_apply(
    items: list[PlanItem], overrides: dict[int, str] | None = None
) -> list[tuple[PlanItem, str, str]]:
    """Classify what an apply run would do, without touching the database.

    Returns a list of ``(item, action, detail)`` where action is one of
    ``"override"``, ``"auto"``, or ``"skip"``. Used by ``--apply --dry-run``.
    """
    overrides = overrides or {}
    preview: list[tuple[PlanItem, str, str]] = []
    for item in items:
        if item.id in overrides:
            preview.append((item, "override", f"-> {overrides[item.id]}"))
        elif item.resolution == "auto":
            if item.issue == "duplicate":
                detail = f"keep #{item.keep_track_id}, merge {item.merge_track_ids}"
            elif item.issue == "stale_album":
                detail = "refresh album release metadata"
            else:
                detail = f"-> {item.proposed_platform_id} (conf {item.confidence})"
            preview.append((item, "auto", detail))
        else:
            reason = (
                "no candidate found"
                if not item.proposed_platform_id
                else f"low confidence ({item.confidence}); needs --override"
            )
            preview.append((item, "skip", reason))
    return preview


def _apply_override(item: PlanItem, override: str) -> None:
    """Apply an operator override string to an item before it is applied."""
    if item.issue == "stale_album":
        print(
            f"  ! Override for stale_album item {item.id} ignored (metadata-only fix).",
            file=sys.stderr,
        )
        return
    if item.issue == "duplicate":
        try:
            new_keep = int(override)
        except (TypeError, ValueError) as exc:
            raise ApplyError(
                f"duplicate override must be a track id, got {override!r}"
            ) from exc
        group = [item.keep_track_id, *item.merge_track_ids]
        if new_keep not in group:
            raise ApplyError(
                f"override keep id {new_keep} is not part of the duplicate group {group}"  # noqa: E501
            )
        item.keep_track_id = new_keep
        item.merge_track_ids = [t for t in group if t != new_keep]
        return
    # Remap issues: override the proposed platform track id.
    item.proposed_platform_id = override
    item.resolution = "override"


def _apply_remap(db: Database, item: PlanItem) -> None:
    if not item.proposed_platform_id:
        raise ApplyError("no proposed platform id (item is manual; provide --override)")
    db.upsert_platform_mapping(
        PlatformMapping(
            track_id=item.track_id,
            platform=PLATFORM,
            platform_track_id=item.proposed_platform_id,
            platform_title=item.proposed_title or item.title,
            platform_artist=item.artist,
            platform_album=item.proposed_album or "",
            match_score=item.confidence or 100,
            status="matched",
            user_approved=True,
        )
    )


def _apply_merge(db: Database, item: PlanItem) -> None:
    if item.keep_track_id is None or not item.merge_track_ids:
        raise ApplyError("duplicate item missing keep/merge ids")
    db.merge_tracks(item.keep_track_id, item.merge_track_ids)


def _apply_stale_album(db: Database, client, item: PlanItem) -> None:
    """Recover release metadata for a track whose album was delisted.

    The track still plays; only the album-level release info could not be
    fetched. Search the artist's catalog for the album to recover the release
    year, then update the cached metadata. No remap is performed.
    """
    track = db.get_track(item.track_id)
    if track is None:
        raise ApplyError("track no longer exists")
    if not track.album:
        raise ApplyError("track has no album name to search for")

    query = f"{track.artist} {track.album}"
    try:
        results = client.search_album(query, limit=5)
    except Exception as exc:
        raise ApplyError(f"album search failed: {exc}") from exc

    from tuneshift.matching import classify_album_results, score_album_match

    # Rank candidates by shared album-match distance (best/smallest first),
    # then take the best acceptable candidate that carries a release year.
    ranked = sorted(
        (
            (score_album_match(track.album, track.artist, res).total, res)
            for res in results
        ),
        key=lambda pair: pair[0],
    )
    match = None
    if ranked and classify_album_results([d for d, _ in ranked]) != "not_found":
        match = next((res for _, res in ranked if res.release_year), None)
    if match is None or not match.release_year:
        raise ApplyError("could not recover release year from album search")

    db.upsert_track_platform_metadata(
        item.track_id,
        PLATFORM,
        item.current_platform_id or "",
        release_year=match.release_year,
        release_date=f"{match.release_year}-01-01",
        album_name=track.album,
    )


def _apply_one(db: Database, client, item: PlanItem) -> None:
    if item.issue in ("unavailable", "version_mismatch", "unmapped"):
        _apply_remap(db, item)
    elif item.issue == "duplicate":
        _apply_merge(db, item)
    elif item.issue == "stale_album":
        _apply_stale_album(db, client, item)
    else:
        raise ApplyError(f"unknown issue type: {item.issue}")


def _reenrich_track(db: Database, client, item: PlanItem, stats: RetryStats) -> None:
    """Best-effort refresh of cached metadata for a remapped track."""
    pid = item.proposed_platform_id or item.current_platform_id
    if not pid:
        return

    def _fetch():
        platform_metadata._tidal_limiter.wait()
        return platform_metadata.fetch_track_report(client, pid)

    try:
        report = _retry_api_call(_fetch, config=RetryConfig(max_retries=2), stats=stats)
        if report.get("metadata"):
            db.upsert_track_platform_metadata(
                item.track_id, PLATFORM, pid, **report["metadata"]
            )
            # AC10: derive the atmos-available tag from the freshly captured
            # metadata (the upsert alone never wrote tags -- that gap is why an
            # Atmos-mapped track stayed untagged after doctor --apply).
            platform_metadata.derive_tags(db, item.track_id)
    except Exception:
        pass


def _sync_playlist(db: Database, name: str) -> bool:
    """Best-effort push of a single playlist to Tidal. Returns success."""
    from tuneshift.commands.sync_cmd import handle_sync

    args = SimpleNamespace(
        playlist=name,
        platform=PLATFORM,
        all=False,
        auto=True,
        reconcile=False,
    )
    try:
        return handle_sync(args, db) == 0
    except Exception as exc:
        print(f'  ! Sync of "{name}" failed: {exc}', file=sys.stderr)
        return False


def apply_plan(
    db: Database,
    plan: DoctorPlan,  # noqa: ARG001 - part of public apply_plan signature
    items: list[PlanItem],
    *,
    overrides: dict[int, str] | None = None,
    client=None,
    do_sync: bool = True,
    quiet: bool = False,
) -> ApplyResult:
    """Apply the given items. Mutates each item's status and returns a summary.

    Items whose resolution is ``manual`` with no override are skipped. DB
    changes are applied per-item; re-enrichment and Tidal sync run afterward.
    """
    overrides = overrides or {}
    result = ApplyResult()

    if client is None:
        from tuneshift.commands.ingest_cmd import _load_client

        client = _load_client(PLATFORM)
        if client and not client.load_session():
            client = None

    # --- DB application phase (per-item transactions) ---
    for item in items:
        if item.id in overrides:
            try:
                _apply_override(item, overrides[item.id])
            except ApplyError as exc:
                item.status = "failed"
                item.note = str(exc)
                result.failed += 1
                continue

        # Low-confidence proposals must not be applied in bulk. A manual item
        # is only applied when the operator supplies an --override (which the
        # override handler records as resolution "override"). Auto items, and
        # duplicate/stale_album fixes (always resolved "auto"), apply directly.
        if item.resolution == "manual":
            item.status = "skipped"
            reason = (
                "manual: no candidate found"
                if not item.proposed_platform_id
                else f"manual: low confidence ({item.confidence}), "
                "provide --override to apply"
            )
            item.note = (item.note + "; " if item.note else "") + reason
            result.skipped += 1
            continue

        try:
            _apply_one(db, client, item)
            item.status = "applied"
            result.applied += 1
            result.affected_playlists.add(item.playlist)
        except Exception as exc:
            item.status = "failed"
            item.note = str(exc)
            result.failed += 1

    if client is None:
        # Without a session we cannot re-enrich or sync; leave applied items as-is.
        return result

    # --- Best-effort re-enrichment of remapped tracks ---
    stats = RetryStats()
    for item in items:
        if item.status != "applied":
            continue
        if item.issue in ("unavailable", "version_mismatch", "unmapped"):
            _reenrich_track(db, client, item, stats)
        elif item.issue == "stale_album":
            # stale_album already recovered release metadata directly (no remap,
            # no refetch), so just (re)derive tags -- otherwise the newly
            # recovered release_year never yields its decade tag.
            platform_metadata.derive_tags(db, item.track_id)

    # --- Best-effort Tidal sync of affected playlists ---
    if do_sync and result.affected_playlists:
        for name in sorted(result.affected_playlists):
            if not quiet:
                print(f'\nSyncing "{name}" to Tidal...')
            ok = _sync_playlist(db, name)
            if not ok:
                for item in items:
                    if item.status == "applied" and item.playlist == name:
                        item.status = "applied_no_sync"
                        result.applied -= 1
                        result.no_sync += 1

    return result
