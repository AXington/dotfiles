"""Durable plan-file storage and editing (ACs P1/P2).

Plans are JSON documents under ``.tuneshift/plans/<plan_id>.json`` beside the
database (so separate databases never share plans). The plan file is the durable
default, a mutating command writes a plan and applies nothing until
:func:`~tuneshift.planapply.apply.apply_plan` runs it. Between the two, the plan
can be edited/pruned: :func:`reject_change` drops an individual change (AC-P2).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from tuneshift.planapply.models import Plan


class PlanError(Exception):
    """Raised when a plan file is missing or malformed."""


def new_plan_id() -> str:
    """Return a fresh, unique plan id."""
    return uuid.uuid4().hex[:16]


def plans_dir(db_path: Path) -> Path:
    return db_path.parent / ".tuneshift" / "plans"


def plan_path(db_path: Path, plan_id: str) -> Path:
    return plans_dir(db_path) / f"{plan_id}.json"


def write_plan(db_path: Path, plan: Plan) -> Path:
    """Serialize a plan to its canonical location. Returns the path written."""
    path = plan_path(db_path, plan.plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=2))
    tmp.replace(path)
    return path


def read_plan(db_path: Path, plan_id: str) -> Plan:
    """Load a saved plan by id. Raises :class:`PlanError` if missing/malformed."""
    path = plan_path(db_path, plan_id)
    if not path.exists():
        raise PlanError(f"No plan found at {path}.")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"Malformed plan at {path}: {exc}") from exc
    return Plan.from_dict(data)


def list_plans(db_path: Path) -> list[str]:
    """Return the ids of all stored plans, newest file first."""
    directory = plans_dir(db_path)
    if not directory.exists():
        return []
    files = sorted(
        directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return [p.stem for p in files]


def reject_change(plan: Plan, change_id: int) -> None:
    """Mark a single change rejected so apply skips it (AC-P2)."""
    change = plan.get(change_id)
    if change is None:
        raise PlanError(f"No change with id {change_id} in plan {plan.plan_id}.")
    change.status = "rejected"


def assign_change_ids(plan: Plan) -> None:
    """Assign stable 1-based change ids so users can reference them for pruning."""
    for index, change in enumerate(plan.changes, start=1):
        change.change_id = index
