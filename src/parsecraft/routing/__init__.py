"""Auto-mode routing: deterministic signal→intent planning with a judge seam.

Hard constraints (extras, VRAM, formats, OCR permission, offline) are
code-owned and never delegated; the judge only re-ranks eligible candidates.
See ``AGENTS.md`` in this directory for the full contract.
"""

from __future__ import annotations

from parsecraft.routing.judge import DeterministicJudge, RoutingJudge
from parsecraft.routing.models import (
    Intent,
    JudgeViolationError,
    NoEligibleBackendError,
    PageRoute,
    RoutingConstraints,
    RoutingError,
    RoutingPlan,
)
from parsecraft.routing.planner import plan_route

__all__ = [
    "DeterministicJudge",
    "Intent",
    "JudgeViolationError",
    "NoEligibleBackendError",
    "PageRoute",
    "RoutingConstraints",
    "RoutingError",
    "RoutingJudge",
    "RoutingPlan",
    "plan_route",
]
