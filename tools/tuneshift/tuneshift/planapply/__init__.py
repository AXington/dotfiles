"""Terraform-style plan/apply engine for TuneShift mutations.

Every mutating command (re-doctor/re-match, migration, sync, pin/pref/lock
change, enrichment overwrite) produces a :class:`~tuneshift.planapply.models.Plan`
of :class:`~tuneshift.planapply.models.PlanChange` items and applies nothing on
its own. The plan is reviewable and editable; :mod:`tuneshift.planapply.apply`
executes exactly the resolved plan, journaling every write so a LOCAL apply is
reversible in one step (section 7, ACs P1-P5).
"""

from __future__ import annotations

from tuneshift.planapply.models import Plan, PlanChange

__all__ = ["Plan", "PlanChange"]
