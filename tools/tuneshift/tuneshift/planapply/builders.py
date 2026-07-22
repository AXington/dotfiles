"""Plan builders for the ROUTED map/unmap (lock) and enrichment-overwrite paths.

Per the section 7.1 mutation-routing table, these mutations never write inline; they
produce a reviewable, journaled :class:`~tuneshift.planapply.models.Plan` that
the apply engine executes (and rollback reverses):

- ``map`` / ``unmap``, create or release a per-playlist identity lock on
  ``playlist_track_mappings`` (spec section 8, AC-L1). Overwriting or releasing an
  already-``user_approved`` mapping is a locked change (AC-P3): it is excluded
  from apply unless the caller explicitly opts in.
- enrichment overwrite, replace matcher-read fields on ``tracks`` with freshly
  enriched values (routing-table row "Enrichment metadata overwrite"). Only the
  fields the matcher actually reads are writable, and a no-op enrichment (values
  already current) yields an empty plan (AC-P4 idempotency).

The CLI verbs that call these builders are Task 4.8.
"""

from __future__ import annotations

from tuneshift.db import Database
from tuneshift.planapply.apply import _spec_for
from tuneshift.planapply.models import Plan, PlanChange, row_key_for
from tuneshift.planapply.plan import new_plan_id


def build_lock_plan(
    db: Database,
    playlist_id: int,
    track_id: int,
    platform: str,
    platform_track_id: str,
) -> Plan:
    """Plan a per-playlist identity lock to ``platform_track_id`` (map/AC-L1)."""
    current = db.get_playlist_track_mapping(playlist_id, track_id, platform)
    # Idempotent no-op: the mapping already holds exactly this locked state, so
    # re-planning after apply must yield an empty plan (AC-P1).
    if (
        current is not None
        and current["platform_track_id"] == platform_track_id
        and current["source"] == "locked"
        and current["user_approved"]
    ):
        return Plan(plan_id=new_plan_id(), kind="lock")
    row_key = row_key_for(playlist_id=playlist_id, track_id=track_id, platform=platform)
    proposed = {
        "playlist_id": playlist_id,
        "track_id": track_id,
        "platform": platform,
        "platform_track_id": platform_track_id,
        "source": "locked",
        "user_approved": 1,
    }
    op = "update" if current is not None else "insert"
    # Overwriting an already-approved mapping touches a locked row (AC-P3).
    locked = bool(current and current["user_approved"])
    change = PlanChange(
        op=op,
        table="playlist_track_mappings",
        row_key=row_key,
        current=_mapping_state(current),
        proposed=proposed,
        reason=f"lock {platform} mapping to {platform_track_id}",
        provenance="user:map",
        locked=locked,
    )
    return _single_change_plan(
        "lock", f"playlist:{playlist_id} track:{track_id}", change
    )


def build_unlock_plan(
    db: Database, playlist_id: int, track_id: int, platform: str
) -> Plan:
    """Plan the release of a per-playlist identity lock (unmap/AC-L1)."""
    current = db.get_playlist_track_mapping(playlist_id, track_id, platform)
    if current is None:
        # Nothing to release -> empty (no-op) plan.
        return Plan(plan_id=new_plan_id(), kind="unlock")
    row_key = row_key_for(playlist_id=playlist_id, track_id=track_id, platform=platform)
    change = PlanChange(
        op="delete",
        table="playlist_track_mappings",
        row_key=row_key,
        current=_mapping_state(current),
        proposed=None,
        reason=f"release {platform} lock",
        provenance="user:unmap",
        locked=bool(current["user_approved"]),
    )
    return _single_change_plan(
        "unlock", f"playlist:{playlist_id} track:{track_id}", change
    )


def build_global_lock_plan(
    db: Database,
    track_id: int,
    platform: str,
    platform_track_id: str,
) -> Plan:
    """Plan a GLOBAL (library-wide) identity lock (spec section 8, AC-L1).

    The global default lock lives on ``platform_tracks`` (``user_approved=1``);
    a per-playlist override (:func:`build_lock_plan`) wins over it. Locking to
    the id already held with approval is an idempotent no-op (empty plan, AC-P1).
    An update preserves the existing descriptive verify metadata / fingerprint;
    only ``user_approved`` (and ``platform_track_id`` when re-pointed) change.
    """
    current = db.get_platform_mapping(track_id, platform)
    if (
        current is not None
        and current.platform_track_id == platform_track_id
        and current.user_approved
    ):
        return Plan(plan_id=new_plan_id(), kind="lock")
    row_key = row_key_for(track_id=track_id, platform=platform)
    if current is None:
        op = "insert"
        proposed = {
            "track_id": track_id,
            "platform": platform,
            "platform_track_id": platform_track_id,
            "status": "matched",
            "user_approved": 1,
        }
    else:
        op = "update"
        proposed = {
            "track_id": track_id,
            "platform": platform,
            "platform_track_id": platform_track_id,
            "user_approved": 1,
        }
    # Overwriting an already-approved mapping touches a locked row (AC-P3).
    locked = bool(current and current.user_approved)
    change = PlanChange(
        op=op,
        table="platform_tracks",
        row_key=row_key,
        current=_global_mapping_state(current),
        proposed=proposed,
        reason=f"lock global {platform} mapping to {platform_track_id}",
        provenance="user:lock",
        locked=locked,
    )
    return _single_change_plan("lock", f"track:{track_id}", change)


def build_global_unlock_plan(db: Database, track_id: int, platform: str) -> Plan:
    """Plan the release of a GLOBAL identity lock (AC-L1/AC-L2).

    Releasing sets ``user_approved=0`` while KEEPING the mapping, so the match
    survives as an ordinary (re-doctorable) auto-match rather than being deleted
    outright (``unmap`` is the delete-entirely path). Nothing to release when no
    global lock is set -> empty (no-op) plan.
    """
    current = db.get_platform_mapping(track_id, platform)
    if current is None or not current.user_approved:
        return Plan(plan_id=new_plan_id(), kind="unlock")
    row_key = row_key_for(track_id=track_id, platform=platform)
    proposed = {
        "track_id": track_id,
        "platform": platform,
        "user_approved": 0,
    }
    change = PlanChange(
        op="update",
        table="platform_tracks",
        row_key=row_key,
        current=_global_mapping_state(current),
        proposed=proposed,
        reason=f"release global {platform} lock",
        provenance="user:unlock",
        locked=True,
    )
    return _single_change_plan("unlock", f"track:{track_id}", change)


def build_enrich_plan(db: Database, track_id: int, fields: dict[str, object]) -> Plan:
    """Plan an enrichment overwrite of matcher-read ``tracks`` fields.

    Only fields whose new value differs from the stored value are emitted, so a
    re-run against already-enriched data produces an empty (no-op) plan. Fields
    outside the matcher-read allowlist are rejected, enrichment must not touch
    identity columns like ``title``/``artist``.
    """
    spec = _spec_for("tracks")
    writable = set(spec.columns)
    unknown = set(fields) - writable
    if unknown:
        raise ValueError(
            f"Enrichment may only overwrite matcher-read fields; got {sorted(unknown)}"
        )

    cols = list(spec.columns)
    row = db.read_spec_columns(spec, cols, {"id": track_id})
    if row is None:
        raise ValueError(f"No track with id {track_id}")

    changed = {k: v for k, v in fields.items() if row[k] != v}
    if not changed:
        return Plan(plan_id=new_plan_id(), kind="enrich")

    current_state = {k: row[k] for k in changed}
    proposed = {"id": track_id, **changed}
    change = PlanChange(
        op="update",
        table="tracks",
        row_key=row_key_for(id=track_id),
        current=current_state,
        proposed=proposed,
        reason=f"enrich {sorted(changed)}",
        provenance="enrichment",
    )
    return _single_change_plan("enrich", f"track:{track_id}", change)


def _mapping_state(mapping: dict | None) -> dict | None:
    if mapping is None:
        return None
    return {
        "playlist_id": mapping["playlist_id"],
        "track_id": mapping["track_id"],
        "platform": mapping["platform"],
        "platform_track_id": mapping["platform_track_id"],
        "source": mapping["source"],
        "user_approved": 1 if mapping["user_approved"] else 0,
    }


def _global_mapping_state(mapping) -> dict | None:
    """Serialize the spec-column state of a ``platform_tracks`` row for a plan.

    Only the columns the apply engine may write (``_TABLE_SPECS['platform_tracks']``)
    are recorded, so rollback restores exactly what the change touched.
    """
    if mapping is None:
        return None
    return {
        "track_id": mapping.track_id,
        "platform": mapping.platform,
        "platform_track_id": mapping.platform_track_id,
        "status": mapping.status,
        "user_approved": 1 if mapping.user_approved else 0,
        "fingerprint": mapping.fingerprint,
    }


def _single_change_plan(kind: str, scope: str, change: PlanChange) -> Plan:
    change.change_id = 1
    return Plan(plan_id=new_plan_id(), kind=kind, scope=scope, changes=[change])
