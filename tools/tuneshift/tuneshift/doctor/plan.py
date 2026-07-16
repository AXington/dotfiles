"""Plan file I/O for the doctor command.

A doctor plan is a JSON document describing detected mapping issues and the
proposed fix for each. It lives at ``.tuneshift/doctor-plan.json`` next to the
database file. Only one plan exists at a time; each scan overwrites it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Issue classifications produced by the scanner.
ISSUE_TYPES = (
    "unavailable",
    "stale_album",
    "version_mismatch",
    "duplicate",
    "unmapped",
)

# Per-item lifecycle states.
STATUS_VALUES = (
    "pending",
    "applied",
    "applied_no_sync",
    "failed",
    "skipped",
)

PLAN_VERSION = 1


def plan_path(db_path: Path) -> Path:
    """Return the doctor plan path for a given database file.

    The plan lives in a ``.tuneshift`` directory beside the database so that
    separate databases (e.g. test fixtures) never share a plan.
    """
    return db_path.parent / ".tuneshift" / "doctor-plan.json"


@dataclass
class PlanItem:
    """A single detected issue and its proposed resolution."""

    id: int
    track_id: int
    playlist: str
    title: str
    artist: str
    issue: str
    current_platform_id: str = ""
    proposed_platform_id: str = ""
    proposed_title: str = ""
    proposed_album: str = ""
    proposed_release_year: int | None = None
    proposed_release_date: str | None = None
    confidence: int = 0
    # "auto" (>= threshold), "manual" (needs override), or "override" (user-set)
    resolution: str = "auto"
    status: str = "pending"
    note: str = ""
    # For duplicate issues: the canonical track row to keep, and the rows to
    # merge into it.
    keep_track_id: int | None = None
    merge_track_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.issue not in ISSUE_TYPES:
            raise ValueError(f"Unknown issue type: {self.issue!r}")
        if self.status not in STATUS_VALUES:
            raise ValueError(f"Unknown status: {self.status!r}")

    @classmethod
    def from_dict(cls, data: dict) -> PlanItem:
        """Build a PlanItem from a plain dict, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


@dataclass
class DoctorPlan:
    """A collection of plan items produced by a single scan."""

    scope: str
    items: list[PlanItem] = field(default_factory=list)
    version: int = PLAN_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "scope": self.scope,
            "items": [asdict(item) for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DoctorPlan:
        items = [PlanItem.from_dict(d) for d in data.get("items", [])]
        return cls(
            scope=data.get("scope", ""),
            items=items,
            version=data.get("version", PLAN_VERSION),
            created_at=data.get("created_at", ""),
        )

    def actionable_items(self) -> list[PlanItem]:
        """Items eligible for a fresh apply run: pending, failed, or skipped."""
        return [i for i in self.items if i.status in ("pending", "failed", "skipped")]

    def get(self, item_id: int) -> PlanItem | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None


class PlanError(Exception):
    """Raised when a plan file is missing or malformed."""


def write_plan(db_path: Path, plan: DoctorPlan) -> Path:
    """Serialize a plan to the canonical location. Returns the path written."""
    path = plan_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=2))
    tmp.replace(path)
    return path


def read_plan(db_path: Path) -> DoctorPlan:
    """Load the saved plan. Raises PlanError if missing or malformed."""
    path = plan_path(db_path)
    if not path.exists():
        raise PlanError(f"No plan found at {path}. Run `tuneshift doctor` first.")
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise PlanError(f"Malformed plan file {path}: {exc}") from exc
    if not isinstance(data, dict) or "items" not in data:
        raise PlanError(f"Malformed plan file {path}: missing 'items'")
    try:
        return DoctorPlan.from_dict(data)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"Malformed plan file {path}: {exc}") from exc
