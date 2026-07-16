"""Doctor: detect and repair stale/broken Tidal track mappings.

The doctor command scans playlists (or the whole DB) against the Tidal API,
classifies mapping problems, proposes fixes, and applies them transactionally.

Submodules:
    plan     - plan file I/O (.tuneshift/doctor-plan.json)
    scanner  - issue detection
    resolver - fix proposal generation
    applier  - transactional application of a saved plan
"""

from tuneshift.doctor.plan import (
    ISSUE_TYPES,
    STATUS_VALUES,
    DoctorPlan,
    PlanItem,
    plan_path,
)

__all__ = [
    "ISSUE_TYPES",
    "STATUS_VALUES",
    "DoctorPlan",
    "PlanItem",
    "plan_path",
]
