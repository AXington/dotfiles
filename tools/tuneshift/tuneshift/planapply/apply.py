"""Apply a resolved plan, journaling every write (ACs P1-P4).

Apply is the ONLY component that mutates local state on behalf of a plan. It
walks a plan's actionable changes (honoring the AC-P3 locked exclusion), and for
each one captures the row's prior state, performs the write, and records a
journal entry so :func:`rollback_plan` (Task 4.3) can reverse the whole batch.

**SQL safety.** A plan change names a target ``table`` and carries a ``proposed``
column mapping. To keep every write parameterized and free of injected
identifiers, apply does NOT trust those names blindly: each table is described by
a :class:`_TableSpec` allowlist, and only column names drawn from that spec are
ever interpolated into SQL. A change targeting an unknown table or column is a
hard error, never a silent or dynamic query.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tuneshift.db import Database
from tuneshift.planapply.models import Plan, PlanChange

# Journal entries for remote pushes are tagged with this table-name prefix so
# rollback can recognise them and refuse to un-push inline (AC-P4 forward-only).
REMOTE_TABLE_PREFIX = "remote:"

# A remote executor may perform a LOCAL side-effect (linking the local playlist
# to a find-or-created remote playlist) as part of its push. It reports that
# write back under this reserved key so apply can journal it as a normal local
# change — keeping the link inside the apply transaction (atomic on failure) and
# reversible on rollback, instead of a self-committing write that escapes both.
LOCAL_SIDE_EFFECT_KEY = "_local_journal"

# A remote executor performs one forward-only remote push (delegated to the
# platform client) and returns the prior remote state to journal, or None if
# there was nothing to capture. Kept as a callable so this module has no
# platform-SDK dependency.
RemoteExecutor = Callable[[PlanChange], "dict | None"]


class ApplyError(Exception):
    """Raised when a plan change cannot be applied safely."""


@dataclass(frozen=True)
class _TableSpec:
    """Allowlist describing a table apply may write."""

    name: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]

    @property
    def all_columns(self) -> tuple[str, ...]:
        return (*self.pk, *self.columns)


# Tables the plan/apply engine is permitted to mutate. Extend deliberately as
# new routed mutations are added (Tasks 4.4-4.7).
_TABLE_SPECS: dict[str, _TableSpec] = {
    "playlist_track_mappings": _TableSpec(
        name="playlist_track_mappings",
        pk=("playlist_id", "track_id", "platform"),
        columns=("platform_track_id", "source", "user_approved"),
    ),
    # NOTE: playlist_track_prefs is intentionally NOT routed through plan/apply.
    # Preferences are configuration set directly via the `prefs` CLI, not a
    # playlist mutation. Its storage now uses a surrogate id PK with a nullable
    # (playlist_id, target) logical key enforced by a COALESCE unique index —
    # shapes the generic engine's NULL-unsafe `col = ?` WHERE and
    # `ON CONFLICT(raw-columns)` arbiter cannot address. A future task that wants
    # planned pref changes must add NULL-safe key handling before re-listing it.
    # Global default lock lives on platform_tracks (spec §8, AC-L1). A routed
    # self-heal (planapply/heal.py, AC-L3) re-binds the locked id and refreshes
    # the same-recording fingerprint, so both are writable through plan/apply.
    "platform_tracks": _TableSpec(
        name="platform_tracks",
        pk=("track_id", "platform"),
        columns=("platform_track_id", "status", "user_approved", "fingerprint"),
    ),
    # A sync push may find-or-create + link the remote playlist at apply time.
    # That link is a LOCAL write, so it is journaled (see ``_apply_remote``) and
    # therefore must be a known table so rollback can reverse it.
    "platform_playlists": _TableSpec(
        name="platform_playlists",
        pk=("playlist_id", "platform"),
        columns=("platform_playlist_id",),
    ),
    # Enrichment overwrites of matcher-read fields are routed + journaled
    # (routing table row "Enrichment metadata overwrite"). Only the fields the
    # matcher actually reads are writable through plan/apply.
    "tracks": _TableSpec(
        name="tracks",
        pk=("id",),
        columns=(
            "isrc",
            "duration_seconds",
            "album_artist",
            "album_type",
            "label",
            "release_date",
            "audio_modes",
        ),
    ),
}


@dataclass
class ApplyReport:
    """Outcome of an apply run."""

    plan_id: str
    applied: int = 0
    skipped: int = 0
    skipped_locked: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _spec_for(table: str) -> _TableSpec:
    spec = _TABLE_SPECS.get(table)
    if spec is None:
        raise ApplyError(f"Refusing to apply change to unknown table: {table!r}")
    return spec


def _validate_columns(spec: _TableSpec, row: dict[str, Any]) -> None:
    unknown = set(row) - set(spec.all_columns)
    if unknown:
        raise ApplyError(
            f"Change targets unknown column(s) {sorted(unknown)} on {spec.name!r}"
        )


def _read_row(db: Database, spec: _TableSpec, pk_values: dict[str, Any]) -> dict | None:
    where = " AND ".join(f"{col} = ?" for col in spec.pk)
    cols = ", ".join(spec.all_columns)
    row = db.conn.execute(
        f"SELECT {cols} FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from allowlist
        tuple(pk_values[col] for col in spec.pk),
    ).fetchone()
    if row is None:
        return None
    return {col: row[col] for col in spec.all_columns}


def _insert_row(db: Database, spec: _TableSpec, proposed: dict[str, Any]) -> None:
    """Insert a full row, upserting on the primary key.

    Used for op=insert and for restoring a row that was deleted (its prior state
    carries the full set of spec columns). Not safe for partial updates of tables
    with NOT NULL columns outside the spec (e.g. ``tracks``) — use ``_update_row``.
    """
    _validate_columns(spec, proposed)
    cols = [c for c in spec.all_columns if c in proposed]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in spec.pk)
    pk_list = ", ".join(spec.pk)
    conflict = (
        f" ON CONFLICT({pk_list}) DO UPDATE SET {updates}"
        if updates
        else f" ON CONFLICT({pk_list}) DO NOTHING"
    )
    sql = f"INSERT INTO {spec.name} ({col_list}) VALUES ({placeholders}){conflict}"  # noqa: S608
    db.conn.execute(sql, tuple(proposed[c] for c in cols))


def _update_row(db: Database, spec: _TableSpec, proposed: dict[str, Any]) -> None:
    """Update only the supplied non-PK columns of an existing row.

    Partial-column safe: tables like ``tracks`` have NOT NULL columns outside the
    spec, so an enrichment overwrite must not go through an INSERT.
    """
    _validate_columns(spec, proposed)
    set_cols = [c for c in spec.columns if c in proposed]
    if not set_cols:
        return
    assignments = ", ".join(f"{c} = ?" for c in set_cols)
    where = " AND ".join(f"{col} = ?" for col in spec.pk)
    params = [proposed[c] for c in set_cols] + [proposed[col] for col in spec.pk]
    db.conn.execute(
        f"UPDATE {spec.name} SET {assignments} WHERE {where}",  # noqa: S608 - identifiers from allowlist
        tuple(params),
    )


def _restore_row(db: Database, spec: _TableSpec, prior: dict[str, Any]) -> None:
    """Restore a row to its prior state, whether it currently exists or not."""
    pk_values = {col: prior[col] for col in spec.pk}
    if _read_row(db, spec, pk_values) is not None:
        _update_row(db, spec, prior)
    else:
        _insert_row(db, spec, prior)


def _delete_row(db: Database, spec: _TableSpec, pk_values: dict[str, Any]) -> None:
    where = " AND ".join(f"{col} = ?" for col in spec.pk)
    db.conn.execute(
        f"DELETE FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from allowlist
        tuple(pk_values[col] for col in spec.pk),
    )


def _live_locked(db: Database, change: PlanChange) -> bool:
    """Is the change's target row locked (user_approved) in the LIVE database?

    A plan serializes ``change.locked`` at build time, but a lock can be created
    between plan build and apply. Gating on live state as well as the serialized
    flag ensures apply never silently overwrites or deletes a mapping that became
    user-approved after the plan was generated (AC-P3/AC-P5 no-regression).
    """
    if change.op == "remote_push":
        return False
    spec = _TABLE_SPECS.get(change.table)
    if spec is None or "user_approved" not in spec.columns:
        return False
    pk_values = json.loads(change.row_key)
    row = _read_row(db, spec, pk_values)
    return bool(row and row["user_approved"])


def _apply_remote(
    db: Database,
    plan_id: str,
    change: PlanChange,
    remote_executor: RemoteExecutor | None,
) -> None:
    """Execute one forward-only remote push and journal it (AC-P4).

    The push itself is delegated to ``remote_executor`` (a closure over the
    platform client) so this module stays free of platform SDK concerns. The
    executor returns the prior remote state, which is journaled under a
    ``remote:`` table name so :func:`rollback_plan` produces a compensating plan
    rather than silently un-pushing.
    """
    if remote_executor is None:
        raise ApplyError("remote_push change requires a remote_executor")
    if not change.table.startswith(REMOTE_TABLE_PREFIX):
        raise ApplyError(
            f"remote_push change must target a {REMOTE_TABLE_PREFIX!r} table, "
            f"got {change.table!r}"
        )
    prior_remote = remote_executor(change)

    # If the executor linked a find-or-created remote playlist, it reports that
    # LOCAL write here. Journal it first (same transaction) so rollback reverses
    # it; strip it from the remote prior_value before journaling the push.
    if prior_remote is not None and LOCAL_SIDE_EFFECT_KEY in prior_remote:
        side = prior_remote.pop(LOCAL_SIDE_EFFECT_KEY)
        db.record_journal_entry(
            plan_id=plan_id,
            table_name=side["table"],
            row_key=side["row_key"],
            op=side["op"],
            prior_value=side.get("prior"),
            new_value=side.get("new"),
        )

    db.record_journal_entry(
        plan_id=plan_id,
        table_name=change.table,
        row_key=change.row_key,
        op=change.op,
        prior_value=prior_remote,
        new_value=change.proposed,
    )


def _apply_one(
    db: Database,
    plan_id: str,
    change: PlanChange,
    remote_executor: RemoteExecutor | None = None,
) -> None:
    """Apply a single change and journal it. Caller owns the transaction."""
    if change.op == "remote_push":
        _apply_remote(db, plan_id, change, remote_executor)
        return

    spec = _spec_for(change.table)
    pk_values = json.loads(change.row_key)
    prior = _read_row(db, spec, pk_values)

    if change.op == "insert":
        if change.proposed is None:
            raise ApplyError("insert change has no proposed state")
        _insert_row(db, spec, change.proposed)
        new_value = change.proposed
    elif change.op == "update":
        if change.proposed is None:
            raise ApplyError("update change has no proposed state")
        _update_row(db, spec, change.proposed)
        new_value = change.proposed
    elif change.op == "delete":
        _delete_row(db, spec, pk_values)
        new_value = None
    else:
        raise ApplyError(f"apply_plan does not handle op {change.op!r}")

    db.record_journal_entry(
        plan_id=plan_id,
        table_name=spec.name,
        row_key=change.row_key,
        op=change.op,
        prior_value=prior,
        new_value=new_value,
    )


def apply_plan(
    db: Database,
    plan: Plan,
    *,
    include_locked: bool = False,
    remote_executor: RemoteExecutor | None = None,
) -> ApplyReport:
    """Apply a plan's resolved changes, journaling each write (AC-P1/P3/P4).

    Locked changes (touching ``user_approved``/locked rows) are skipped unless
    ``include_locked`` is set. Rejected/skipped/already-applied changes are never
    re-applied, so re-applying a fully-applied plan is a no-op (AC-P4).

    ``remote_push`` changes are forward-only remote mutations; they require a
    ``remote_executor`` (a closure over the platform client). Rollback of a
    remote push never un-pushes inline — it yields a compensating plan (AC-P4).
    """
    report = ApplyReport(plan_id=plan.plan_id)

    for change in plan.changes:
        if not change.is_actionable:
            report.skipped += 1
            continue
        if change.locked and not include_locked:
            report.skipped_locked += 1
            # AC-P3 lock exclusion is a per-run gate, not a permanent status:
            # leave the change pending so an explicit include_locked re-apply of
            # the SAME plan can act on it.
            continue
        if not include_locked and _live_locked(db, change):
            # A lock appeared after the plan was built. Treat it like any locked
            # change: skip unless the caller explicitly opts in (never regress a
            # user-approved mapping, even one the plan didn't know about).
            report.skipped_locked += 1
            continue
        try:
            with db.conn:
                _apply_one(db, plan.plan_id, change, remote_executor)
            change.status = "applied"
            report.applied += 1
        except Exception as exc:
            change.status = "failed"
            report.failed += 1
            report.errors.append(f"change {change.change_id}: {exc}")

    return report


@dataclass
class RollbackReport:
    """Outcome of a rollback run."""

    plan_id: str
    reverted: int = 0
    remote_skipped: int = 0
    # Journal entries for remote pushes that a compensating plan must undo.
    compensating: list[Any] = field(default_factory=list)


def _reverse_one(db: Database, entry: Any) -> None:
    """Reverse a single LOCAL journal entry. Caller owns the transaction.

    Uniform rule: if the write had no prior state the row was created, so delete
    it; otherwise restore the prior state. This inverts insert/update/delete
    symmetrically without needing to branch on the recorded op.
    """
    spec = _spec_for(entry.table_name)
    pk_values = json.loads(entry.row_key)
    if entry.prior_value is None:
        _delete_row(db, spec, pk_values)
    else:
        _restore_row(db, spec, entry.prior_value)


def rollback_plan(db: Database, plan_id: str) -> RollbackReport:
    """Reverse an applied plan in one step by reverse-replaying its journal.

    LOCAL mutations are undone directly. REMOTE pushes are forward-only: they are
    collected into :attr:`RollbackReport.compensating` for a compensating plan
    (Task 4.6) and never un-pushed inline. On success the journal is cleared so
    the plan cannot be rolled back twice.
    """
    report = RollbackReport(plan_id=plan_id)
    entries = db.get_journal_entries(plan_id)  # newest-first == reverse order

    with db.conn:
        for entry in entries:
            if entry.table_name.startswith(REMOTE_TABLE_PREFIX):
                report.remote_skipped += 1
                report.compensating.append(entry)
                continue
            _reverse_one(db, entry)
            report.reverted += 1

    # Local rollback is complete; drop the local journal entries. Any remote
    # entries needing a compensating plan are already captured in the report.
    if report.remote_skipped == 0:
        db.clear_journal(plan_id)

    return report
