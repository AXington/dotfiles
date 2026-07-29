"""ROUTED remote push for sync (section 7.1 routing table, AC-P4 forward-only).

A ``sync`` pushes a playlist's reconciled ordered track list to a streaming
platform. Under the plan/apply architecture that push is not performed inline,
it is a ``remote_push`` change the apply engine executes through a
:data:`~tuneshift.planapply.apply.RemoteExecutor`, journaling it under a
``remote:`` table name. Remote pushes are forward-only: :func:`rollback_plan`
never un-pushes; it surfaces the prior remote state as a compensating plan,
which :func:`build_compensating_plan` turns back into an apply-able re-push.

This module is deliberately thin: plan construction reconciles read-only, the
executor is the only code that mutates a remote platform, and both are testable
with a fake client (no live platform SDK).
"""

from __future__ import annotations

import json
import logging

from tuneshift import TuneShiftError
from tuneshift.db import Database
from tuneshift.planapply.apply import (
    LOCAL_SIDE_EFFECT_KEY,
    REMOTE_TABLE_PREFIX,
    RemoteExecutor,
    RollbackReport,
)
from tuneshift.planapply.models import Plan, PlanChange, row_key_for
from tuneshift.planapply.plan import new_plan_id
from tuneshift.platforms.protocol import MusicPlatformClient
from tuneshift.reconcile import reconcile_track

logger = logging.getLogger(__name__)


class RemoteStateUnknownError(TuneShiftError):
    """Refused a remote push whose prior state could not be read.

    Pushing here would overwrite remote contents and ordering while journaling
    no way back, so the mutation is unreversible by construction. Failing the
    change is recoverable; a silent unreversible push is not (SYNC-WIPE).
    """


def _playlist_meta(db: Database, playlist_id: int) -> tuple[str, str]:
    meta = db.get_playlist_name_description(playlist_id)
    if meta is None:
        raise ValueError(f"No playlist with id {playlist_id}")
    name, description = meta
    return name, (description or "")


def _reorder_tracks(tracks: list, ordered_track_ids: list[int]) -> list:
    """Return ``tracks`` in the given id order; unlisted tracks keep their tail
    position so a partial order override never drops a track from the push."""
    by_id = {t.id: t for t in tracks}
    ordered = [by_id[tid] for tid in ordered_track_ids if tid in by_id]
    seen = {t.id for t in ordered}
    ordered.extend(t for t in tracks if t.id not in seen)
    return ordered


def _remote_ids(
    client: MusicPlatformClient, platform_playlist_id: str
) -> list[str] | None:
    """Read the remote track order, or ``None`` when it could not be read.

    ``None`` means UNKNOWN, never "empty". Callers must preserve that
    distinction: coercing an unreadable snapshot to ``[]`` turns a rollback
    into a permanent wipe of the remote playlist (SYNC-WIPE).
    """
    try:
        return [t.platform_id for t in client.get_playlist_tracks(platform_playlist_id)]
    except Exception:  # noqa: BLE001
        # Genuine platform SDK boundary: a client can raise anything from
        # transport, auth, or vendor-specific errors. Swallowing it silently hid
        # an auth failure and a transient timeout behind the same empty-looking
        # result. The value is still None so the callers' fail-closed paths
        # engage, but the cause is now observable.
        logger.warning(
            "could not read remote playlist state",
            extra={"platform_playlist_id": platform_playlist_id},
            exc_info=True,
        )
        return None


def build_sync_plan(
    db: Database,
    playlist_id: int,
    client: MusicPlatformClient,
    *,
    platform: str = "tidal",
    force: bool = False,
    ordered_track_ids: list[int] | None = None,
) -> Plan:
    """Plan the forward-only remote push of a playlist's reconciled track list.

    Reconciliation is read-only: it computes the ordered platform ids that would
    be pushed. Tracks that don't confidently resolve are omitted from the push
    (matching today's "unavailable tracks aren't pushed" behavior) rather than
    silently dropped from the library. If the remote already holds exactly the
    proposed ids, the plan is a no-op (AC-P4 idempotency).

    ``ordered_track_ids`` optionally overrides the push ORDER (e.g. an
    auto-reorder arc computed read-only by the caller); it never mutates local
    playlist order. Unlisted tracks keep their playlist position after the listed
    ones so nothing is silently dropped.
    """
    name, description = _playlist_meta(db, playlist_id)
    tracks = db.get_playlist_tracks(playlist_id)
    if ordered_track_ids is not None:
        tracks = _reorder_tracks(tracks, ordered_track_ids)
    cached = db.get_platform_mappings_for_tracks([t.id for t in tracks], platform)

    push_ids: list[str] = []
    for track in tracks:
        result = reconcile_track(
            db,
            track.id,
            client,
            force=force,
            cached_mapping=cached.get(track.id),
            playlist_id=playlist_id,
        )
        if result.platform_track_id and result.confidence != "not_found":
            push_ids.append(result.platform_track_id)

    platform_playlist_id = db.get_platform_playlist_id(playlist_id, platform)
    prior_ids = (
        _remote_ids(client, platform_playlist_id) if platform_playlist_id else None
    )

    change = PlanChange(
        op="remote_push",
        table=f"{REMOTE_TABLE_PREFIX}{platform}",
        row_key=row_key_for(playlist_id=playlist_id, platform=platform),
        current={"platform_playlist_id": platform_playlist_id, "track_ids": prior_ids},
        proposed={
            "platform": platform,
            "local_playlist_id": playlist_id,
            "playlist_name": name,
            "description": description,
            "platform_playlist_id": platform_playlist_id,
            "track_ids": push_ids,
        },
        remote=True,
        reason=f"push {len(push_ids)} tracks to {platform}",
        provenance="sync",
        change_id=1,
    )
    # Never wipe a remote playlist just because nothing resolved this run: an
    # all-unavailable result is a no-op, not a destructive empty push. (A playlist
    # with no local tracks is short-circuited by the caller before we get here, so
    # an empty push here always means "resolution found nothing to push".)
    if not push_ids:
        change.status = "skipped"
    # Idempotent no-op: the remote already holds exactly what we would push.
    elif prior_ids is not None and prior_ids == push_ids:
        change.status = "skipped"

    return Plan(
        plan_id=new_plan_id(),
        kind="sync",
        scope=f"playlist:{playlist_id} platform:{platform}",
        changes=[change],
    )


def make_sync_executor(
    db: Database, client: MusicPlatformClient, *, platform: str = "tidal"
) -> RemoteExecutor:
    """Build the :data:`RemoteExecutor` that performs a sync push at apply time.

    Resolves (find-or-create + link) the platform playlist if needed, captures
    the prior remote track order for the compensating plan, then replaces the
    remote track list. Playlist creation is deferred to apply time so plan
    construction stays free of remote mutations.
    """

    def _execute(change: PlanChange) -> dict | None:
        proposed = change.proposed or {}
        platform_playlist_id = proposed.get("platform_playlist_id")
        local_playlist_id = proposed.get("local_playlist_id")
        link_journal: dict | None = None
        created_now = False

        if not platform_playlist_id:
            existing = client.find_playlist_by_name(proposed["playlist_name"])
            if existing is not None:
                platform_playlist_id = existing.platform_id
            else:
                created = client.create_playlist(
                    proposed["playlist_name"], proposed.get("description", "")
                )
                platform_playlist_id = created.platform_id
                created_now = True
            if local_playlist_id is not None:
                # Link within the apply transaction (no self-committing helper),
                # and report it so apply journals it as a reversible local change
                # instead of leaving an orphaned link if the push then fails.
                prior_link = db.get_platform_playlist_id(local_playlist_id, platform)
                # ON CONFLICT DO UPDATE (not INSERT OR REPLACE) so re-linking an
                # already-synced playlist preserves last_synced_at instead of
                # deleting the row and resetting it to NULL (BUG-6b: a re-link
                # made status report "never synced" for a genuinely-synced
                # playlist). commit=False keeps the link inside the caller's
                # journaled apply transaction.
                db.link_platform_playlist(
                    local_playlist_id,
                    platform,
                    platform_playlist_id,
                    commit=False,
                )
                link_journal = {
                    "table": "platform_playlists",
                    "row_key": row_key_for(
                        playlist_id=local_playlist_id, platform=platform
                    ),
                    "op": "update" if prior_link is not None else "insert",
                    "prior": (
                        {
                            "playlist_id": local_playlist_id,
                            "platform": platform,
                            "platform_playlist_id": prior_link,
                        }
                        if prior_link is not None
                        else None
                    ),
                    "new": {
                        "playlist_id": local_playlist_id,
                        "platform": platform,
                        "platform_playlist_id": platform_playlist_id,
                    },
                }

        if created_now:
            # Created moments ago in this same call, so the prior state is empty
            # by construction. Reading it would add a network round trip that
            # can only fail, and a failure here must not block a first sync:
            # there is nothing in a brand-new playlist to destroy.
            prior_ids: list[str] | None = []
        else:
            prior_ids = _remote_ids(client, platform_playlist_id)
            if prior_ids is None:
                # Last point at which the unreversibility is still avoidable.
                # Once the push lands, the pre-push order is gone and the
                # journal records nothing to restore from (SYNC-WIPE).
                raise RemoteStateUnknownError(
                    f"refusing to push to {platform} playlist "
                    f"{platform_playlist_id!r}: its current contents could not "
                    "be read, so the push could not be rolled back"
                )
        client.replace_playlist_tracks(
            platform_playlist_id, list(proposed.get("track_ids", []))
        )
        result: dict = {
            "platform_playlist_id": platform_playlist_id,
            "track_ids": prior_ids,
        }
        if link_journal is not None:
            result[LOCAL_SIDE_EFFECT_KEY] = link_journal
        return result

    return _execute


def build_compensating_plan(
    report: RollbackReport, *, plan_id: str | None = None
) -> Plan:
    """Turn a rollback's forward-only remote entries into a re-push plan (AC-P4).

    Each remote push that a rollback could not un-push inline becomes a
    ``remote_push`` change that restores the prior remote track order. Applying
    this plan with :func:`make_sync_executor` completes the reversal.
    """
    changes: list[PlanChange] = []
    for change_id, entry in enumerate(report.compensating, start=1):
        prior = entry.prior_value or {}
        row = json.loads(entry.row_key)
        platform = entry.table_name[len(REMOTE_TABLE_PREFIX) :]
        prior_ids = prior.get("track_ids")
        # ``None`` means the pre-push snapshot could not be read, NOT that the
        # playlist was empty. ``build_sync_plan`` already draws this distinction
        # ("never wipe a remote playlist just because nothing resolved"); the
        # compensating path must draw it too. Coercing unknown to [] re-pushes
        # an empty tracklist and permanently destroys the remote contents and
        # ordering that the rollback existed to restore (SYNC-WIPE).
        prior_unknown = prior_ids is None
        change = PlanChange(
            op="remote_push",
            table=entry.table_name,
            row_key=entry.row_key,
            current=None,
            proposed={
                "platform": platform,
                "local_playlist_id": row.get("playlist_id"),
                "playlist_name": prior.get("playlist_name", ""),
                "description": "",
                "platform_playlist_id": prior.get("platform_playlist_id"),
                "track_ids": None if prior_unknown else list(prior_ids),
            },
            remote=True,
            reason=(
                f"prior remote state unknown; cannot restore {platform} "
                "playlist without wiping it"
                if prior_unknown
                else f"compensating re-push to {platform}"
            ),
            provenance="rollback-compensation",
            change_id=change_id,
        )
        if prior_unknown:
            # Recorded rather than dropped, so the unrestorable entry stays
            # visible in the plan; "skipped" makes it non-actionable on apply.
            change.status = "skipped"
        changes.append(change)
    return Plan(
        plan_id=plan_id or new_plan_id(),
        kind="compensating",
        changes=changes,
    )
