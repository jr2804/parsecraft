"""Auto-mode routing: deterministic signal→intent planning with a judge seam.

Hard constraints (extras, VRAM, formats, language, OCR permission, offline)
are code-owned and never delegated; the judge only re-ranks eligible
candidates. See ``AGENTS.md`` in this directory for the full contract.
"""

from __future__ import annotations

from parsecraft.routing.classifier import OcrFacts, PageOcrClassifier
from parsecraft.routing.judge import (
    DeterministicJudge,
    JudgeCacheIdentity,
    MachineProfile,
    PageContext,
    RoutingJudge,
    judge_cache_identity,
    page_context,
)
from parsecraft.routing.language import LanguageDetector
from parsecraft.routing.models import (
    Intent,
    JudgeViolationError,
    NoEligibleBackendError,
    PageRoute,
    RoutingConstraints,
    RoutingError,
    RoutingPlan,
    RoutingPreference,
)
from parsecraft.routing.planner import plan_route

__all__ = [
    "DeterministicJudge",
    "Intent",
    "JudgeCacheIdentity",
    "JudgeViolationError",
    "LanguageDetector",
    "MachineProfile",
    "NoEligibleBackendError",
    "OcrFacts",
    "PageContext",
    "PageOcrClassifier",
    "PageRoute",
    "RoutingConstraints",
    "RoutingError",
    "RoutingJudge",
    "RoutingPlan",
    "RoutingPreference",
    "judge_cache_identity",
    "page_context",
    "plan_route",
]
