"""The deterministic auto-mode planner: signals → intent → eligible → judged plan."""

from __future__ import annotations

from collections.abc import Sequence

from parsecraft.backends.protocol import AnalysisResult, BackendDescriptor
from parsecraft.ir.models import PageSignal
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
from parsecraft.routing.rules import (
    classify_page,
    extract_hints,
    in_intent_family,
    is_hard_eligible,
    is_ocr,
)


def plan_route(
    analysis: AnalysisResult,
    backends: Sequence[BackendDescriptor],
    constraints: RoutingConstraints,
    judge: RoutingJudge | None = None,
) -> RoutingPlan:
    """Plan a route for every analyzed page. Pure, offline, deterministic.

    Judge violations (ineligible/duplicate/empty candidates) raise
    :class:`JudgeViolationError`; nothing eligible raises
    :class:`NoEligibleBackendError`; an analysis without signals raises
    :class:`RoutingError`.
    """
    if not analysis.signals:
        raise RoutingError("analysis carries no page signals to route")
    active_judge: RoutingJudge = judge if judge is not None else DeterministicJudge()
    eligible = [descriptor for descriptor in sorted(backends, key=lambda d: d.name) if is_hard_eligible(descriptor, constraints)]
    if not eligible:
        raise NoEligibleBackendError(None, "no backend satisfies the hard constraints")
    hints = extract_hints(analysis)

    pages: list[PageRoute] = []
    for signal in sorted(analysis.signals, key=lambda s: s.page_number):
        intent = classify_page(signal, analysis.page_count, hints)
        family = [descriptor for descriptor in eligible if in_intent_family(intent, descriptor)]
        if not family:
            raise NoEligibleBackendError(intent, "intent family empty after hard constraints")
        if intent is Intent.NATIVE and all(is_ocr(descriptor) for descriptor in family):
            raise NoEligibleBackendError(intent, "no native backend covers the source format")
        order = list(active_judge.rank(intent, family))
        _validate_order(order, family, intent)
        candidates = order[: constraints.max_passes]
        pages.append(
            PageRoute(
                page_number=signal.page_number,
                intent=intent,
                candidates=candidates,
                chosen=candidates[0],
                reason=_reason(intent, candidates[0], signal),
            )
        )
    return RoutingPlan(primary=_primary(pages), pages=pages)


def _validate_order(order: list[str], family: list[BackendDescriptor], intent: Intent) -> None:
    if not order:
        raise JudgeViolationError("", "judge returned an empty order", intent)
    allowed = {descriptor.name for descriptor in family}
    seen: set[str] = set()
    for name in order:
        if name not in allowed:
            raise JudgeViolationError(name, "not an eligible candidate for this intent", intent)
        if name in seen:
            raise JudgeViolationError(name, "duplicate candidate", intent)
        seen.add(name)


def _primary(pages: list[PageRoute]) -> str:
    counts: dict[str, int] = {}
    for page in pages:
        counts[page.chosen] = counts.get(page.chosen, 0) + 1
    return sorted(counts, key=lambda name: (-counts[name], name))[0]


def _reason(intent: Intent, chosen: str, signal: PageSignal) -> str:
    if intent is Intent.NATIVE:
        return f"page {signal.page_number}: native text sufficient (text_chars={signal.text_chars}); first pass {chosen}"
    if intent is Intent.OCR_TABLES:
        return f"page {signal.page_number}: multi-page dense tables; OCR pass {chosen}"
    if intent is Intent.OCR_VISION:
        return f"page {signal.page_number}: equations/figures-heavy; vision OCR pass {chosen}"
    return f"page {signal.page_number}: no usable native text (blank/garbled); OCR pass {chosen}"
