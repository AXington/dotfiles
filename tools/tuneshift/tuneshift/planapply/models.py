"""Plan/apply data model (§7, ACs P1-P5).

A :class:`Plan` is what a mutating command produces instead of writing directly.
It is a list of :class:`PlanChange` rows, each describing one row-level mutation
as ``current -> proposed`` with the reason and provenance that justify it, plus
the metadata the apply engine needs to journal, gate, and reverse it.

Design notes:

- ``row_key`` is a stable JSON encoding of the target row's primary key. It is
  the join key between a plan change and its journal entry, so it must be
  deterministic (sorted keys) — see :func:`row_key_for`.
- ``locked`` marks a change that touches a ``user_approved``/locked row. Such
  changes are excluded from apply by default (AC-P3); an explicit opt-in is
  required to include them.
- ``remote`` marks a push to a remote platform (Tidal/Spotify). Remote pushes
  are forward-only: a rollback does not silently un-push, it generates a
  compensating plan (AC-P4).
- ``classification`` is used by migration (AC-P5): ``improved`` / ``unchanged``
  / ``needs-human-judgment``. Non-migration plans default to ``improved``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

PLAN_VERSION = 1

OP_VALUES = ("insert", "update", "delete", "remote_push")
STATUS_VALUES = ("pending", "applied", "skipped", "rejected", "failed")
CLASSIFICATION_VALUES = (
    "improved",
    "unchanged",
    "needs-human-judgment",
    "locked",
    "downgrade-flag",
)


def row_key_for(**primary_key: object) -> str:
    """Return a stable JSON row-key from a primary-key mapping.

    Keys are sorted so the same logical row always produces the same string,
    which is what links a :class:`PlanChange` to its :class:`JournalEntry`.
    """
    return json.dumps(primary_key, sort_keys=True)


@dataclass
class PlanChange:
    """One proposed row-level mutation within a plan."""

    op: str
    table: str
    row_key: str
    current: dict | None = None
    proposed: dict | None = None
    reason: str = ""
    provenance: str = ""
    classification: str = "improved"
    locked: bool = False
    remote: bool = False
    change_id: int = 0
    status: str = "pending"

    def __post_init__(self) -> None:
        if self.op not in OP_VALUES:
            raise ValueError(f"Unknown op: {self.op!r}")
        if self.status not in STATUS_VALUES:
            raise ValueError(f"Unknown status: {self.status!r}")
        if self.classification not in CLASSIFICATION_VALUES:
            raise ValueError(f"Unknown classification: {self.classification!r}")

    @property
    def is_actionable(self) -> bool:
        """Whether apply should attempt this change (not rejected/applied/skipped)."""
        return self.status in ("pending", "failed")

    @classmethod
    def from_dict(cls, data: dict) -> PlanChange:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Plan:
    """A collection of proposed changes produced by one mutating command."""

    plan_id: str
    kind: str
    scope: str = ""
    changes: list[PlanChange] = field(default_factory=list)
    version: int = PLAN_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "plan_id": self.plan_id,
            "kind": self.kind,
            "scope": self.scope,
            "created_at": self.created_at,
            "changes": [asdict(c) for c in self.changes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        changes = [PlanChange.from_dict(d) for d in data.get("changes", [])]
        return cls(
            plan_id=data.get("plan_id", ""),
            kind=data.get("kind", ""),
            scope=data.get("scope", ""),
            changes=changes,
            version=data.get("version", PLAN_VERSION),
            created_at=data.get("created_at", ""),
        )

    def actionable_changes(self, *, include_locked: bool = False) -> list[PlanChange]:
        """Changes apply will act on, honoring the AC-P3 locked exclusion."""
        result = []
        for change in self.changes:
            if not change.is_actionable:
                continue
            if change.locked and not include_locked:
                continue
            result.append(change)
        return result

    def get(self, change_id: int) -> PlanChange | None:
        for change in self.changes:
            if change.change_id == change_id:
                return change
        return None

    def is_empty(self) -> bool:
        """A plan with no actionable changes is a no-op (AC-P4 idempotency)."""
        return not any(c.is_actionable for c in self.changes)


@dataclass
class JournalEntry:
    """One recorded write, used to reverse an applied plan (AC-P4)."""

    id: int
    plan_id: str
    table_name: str
    row_key: str
    op: str
    prior_value: dict | None
    new_value: dict | None
    applied_at: str
