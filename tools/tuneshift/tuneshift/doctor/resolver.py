"""Doctor resolver: propose fixes for detected issues.

Given plan items produced by the scanner, the resolver fills in the proposed
fix for each API-driven issue type using the existing reconciler. Items whose
best candidate scores below the confidence threshold are marked ``manual`` and
require an operator override at apply time.

Fix strategy by issue type:
    unavailable       reconcile search -> best available match
    version_mismatch  reconcile search -> standard/album version (reconciler
                      already penalizes deluxe/remaster editions)
    unmapped          reconcile search -> new mapping (same as `tuneshift add`)
    stale_album       metadata-only refresh (album search for release year)
    duplicate         already resolved by the scanner (keep/merge sets)
"""

from __future__ import annotations

import sys

from tuneshift.db import Database
from tuneshift.doctor.plan import PlanItem
from tuneshift.reconcile import reconcile_track

# Minimum reconciler score for an auto-applied proposal.
DEFAULT_CONFIDENCE_THRESHOLD = 70

# Issue types whose fix comes from a reconciler search.
_RECONCILE_ISSUES = ("unavailable", "version_mismatch", "unmapped")


def resolve_item(
    db: Database,
    client,
    item: PlanItem,
    *,
    threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
) -> PlanItem:
    """Fill in the proposed fix for a single plan item (mutates and returns it)."""
    if item.issue == "duplicate":
        # Scanner already set keep/merge; nothing to search.
        item.resolution = "auto"
        item.confidence = item.confidence or 100
        return item

    if item.issue == "stale_album":
        # Metadata-only fix, applied by re-fetching release info. No remap, so
        # no candidate search is needed here.
        item.resolution = "auto"
        item.confidence = 100
        if not item.note:
            item.note = "refresh album release metadata"
        return item

    if item.issue in _RECONCILE_ISSUES:
        _playlist = db.find_playlist_by_name(item.playlist) if item.playlist else None
        result = reconcile_track(
            db,
            item.track_id,
            client,
            force=True,
            playlist_id=_playlist.id if _playlist else None,
        )
        db.save_match_audit(item.track_id, client.platform_name, result.audit)
        if result.confidence == "not_found" or not result.platform_track_id:
            item.resolution = "manual"
            item.confidence = 0
            # Surface the *reason* from the audit, not a flat "no candidate".
            # This distinguishes "wrong version rejected" / "blocked in market" /
            # "platform can't distinguish" so the human knows what to do next.
            if result.audit is not None:
                from tuneshift.matching import describe_reason

                detail = describe_reason(result.audit.reason_code)
            else:
                detail = "no candidate found"
            item.note = (item.note + "; " if item.note else "") + detail
            return item

        item.proposed_platform_id = result.platform_track_id
        item.proposed_title = result.platform_title
        item.proposed_album = result.platform_album
        item.confidence = int(result.score)

        # A proposal identical to the current mapping is not a fix.
        if (
            item.current_platform_id
            and item.proposed_platform_id == item.current_platform_id
        ):
            item.resolution = "manual"
            item.note = (
                item.note + "; " if item.note else ""
            ) + "best candidate is the current mapping"
            return item

        item.resolution = "auto" if item.confidence >= threshold else "manual"
        return item

    # Unknown issue type: leave as-is, require manual handling.
    item.resolution = "manual"
    return item


def resolve_all(
    db: Database,
    client,
    items: list[PlanItem],
    *,
    threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
    quiet: bool = False,
) -> list[PlanItem]:
    """Resolve every item in place. Returns the same list for convenience."""
    total = len(items)
    for i, item in enumerate(items):
        if not quiet:
            print(
                f"  Resolving [{i + 1}/{total}] {item.title} - {item.artist}...",
                end="\r",
                file=sys.stderr,
                flush=True,
            )
        resolve_item(db, client, item, threshold=threshold)
    if not quiet and total:
        print(file=sys.stderr)
    return items
