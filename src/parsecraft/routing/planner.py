"""The deterministic auto-mode planner: signals → intent → eligible → judged plan."""

from __future__ import annotations

from collections.abc import Sequence

from parsecraft.backends.protocol import AnalysisResult, BackendDescriptor
from parsecraft.ir.models import PageSignal
from parsecraft.routing.judge import DeterministicJudge, RoutingJudge, page_context
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
    can_degrade_to_native,
    classify_page,
    degradation_for,
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

    The judge the planner builds itself honours ``constraints.preference``; an
    injected judge carries its own ranking policy (the caller resolves it with
    the same preference — see ``cli/convert.py``).
    """
    if not analysis.signals:
        raise RoutingError("analysis carries no page signals to route")
    active_judge: RoutingJudge = judge if judge is not None else DeterministicJudge(constraints.preference)
    eligible = [descriptor for descriptor in sorted(backends, key=lambda d: d.name) if is_hard_eligible(descriptor, constraints)]
    if not eligible:
        raise NoEligibleBackendError(None, "no backend satisfies the hard constraints")
    hints = extract_hints(analysis)

    pages: list[PageRoute] = []
    for signal in sorted(analysis.signals, key=lambda s: s.page_number):
        intent = classify_page(signal, analysis.page_count, hints)
        family = [descriptor for descriptor in eligible if in_intent_family(intent, descriptor)]
        degraded = False
        unavailable = ""
        degradation_code: str | None = None
        degradation_score: float | None = None
        if not family and intent is not Intent.NATIVE and can_degrade_to_native(signal, eligible):
            # Mirror of the NATIVE-lead guard: no OCR family is available
            # (missing extras / allow_ocr off / no usable GPU) but native can
            # tell the truth about this page (it has text, or it is blank) →
            # degrade to NATIVE with a recorded reason instead of failing a
            # valid document (routing/AGENTS.md, pc-ztq).
            degraded = True
            unavailable = _ocr_unavailable_cause(backends, constraints)
            degradation_code, degradation_score = degradation_for(signal)
            intent = Intent.NATIVE
            family = [descriptor for descriptor in eligible if in_intent_family(intent, descriptor)]
        if not family:
            raise NoEligibleBackendError(intent, "intent family empty after hard constraints")
        if intent is Intent.NATIVE and all(is_ocr(descriptor) for descriptor in family):
            # NATIVE-intent pages must never silently route to OCR: the format
            # needs a native-capable lead backend (see routing/AGENTS.md).
            raise NoEligibleBackendError(intent, "no native backend covers the source format")
        order = list(active_judge.rank(intent, family, page_context(intent, signal, hints)))
        _validate_order(order, family, intent)
        candidates = order[: constraints.max_passes]
        pages.append(
            PageRoute(
                page_number=signal.page_number,
                intent=intent,
                candidates=candidates,
                chosen=candidates[0],
                reason=_degraded_reason(signal, candidates[0], unavailable) if degraded else _reason(intent, candidates[0], signal),
                degradation_code=degradation_code,
                degradation_score=degradation_score,
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
    _validate_native_fallbacks(order, family, intent)


def _validate_native_fallbacks(order: list[str], family: list[BackendDescriptor], intent: Intent) -> None:
    """A NATIVE page must be led by a native-capable candidate.

    The rules admit OCR backends into a NATIVE page's family as *fallbacks*, and
    a judge reorders that family — so without this check a provider judge could
    lead a page the rule table called native with a VLM, spending minutes on
    text a native backend reads in milliseconds. Contract, not ranking advice:
    the deterministic judge happens to sort native first, and a provider judge
    must honour the same rule or its order is rejected.
    """
    if intent is not Intent.NATIVE:
        return
    by_name = {descriptor.name: descriptor for descriptor in family}
    ocr_positions = [index for index, name in enumerate(order) if is_ocr(by_name[name])]
    native_positions = [index for index, name in enumerate(order) if not is_ocr(by_name[name])]
    if ocr_positions and native_positions and min(ocr_positions) < max(native_positions):
        offending = order[min(ocr_positions)]
        raise JudgeViolationError(offending, "an OCR backend may not lead a NATIVE page (native-capable candidates first)", intent)


def _primary(pages: list[PageRoute]) -> str:
    counts: dict[str, int] = {}
    for page in pages:
        counts[page.chosen] = counts.get(page.chosen, 0) + 1
    return sorted(counts, key=lambda name: (-counts[name], name))[0]


def _ocr_unavailable_cause(backends: Sequence[BackendDescriptor], constraints: RoutingConstraints) -> str:
    """Why no OCR backend was eligible — a HOST fact, not a routing failure (decision 5).

    The distinction is the whole point: a host missing its engine and a host with
    no OCR backend at all need different fixes, and both used to read as the same
    opaque "no eligible OCR backend". The engine case NAMES the engine, because
    the remedy is a single OS package.

    It reads the same two facts ``is_hard_eligible`` reads — the declared backend
    set and ``constraints.engines`` — rather than re-deriving eligibility, so the
    message can never claim a cause the eligibility rule did not apply.
    """
    declared = [descriptor for descriptor in backends if is_ocr(descriptor)]
    if not declared:
        return "no OCR backend is installed"
    missing = sorted(
        {
            descriptor.capabilities.required_engine
            for descriptor in declared
            if descriptor.capabilities.required_engine is not None and descriptor.capabilities.required_engine not in constraints.engines
        }
    )
    if missing:
        return f"the {', '.join(missing)} engine is not installed on this host"
    return "no eligible OCR backend (missing extras, OCR disabled, or no usable GPU)"


def _degraded_reason(signal: PageSignal, chosen: str, cause: str) -> str:
    """Recorded when an OCR-intent page falls back to native (no OCR family).

    The parenthetical says WHY native could stand in, and a blank page is not
    the same case as a thin-text one: there is no native text to speak of, so
    claiming "native text present" alongside ``text_chars=0`` would be a second
    false statement in the one message whose whole purpose is to be honest about
    why a page was not OCR'd.
    """
    stood_in = "blank page: no text to lose" if signal.blank else f"native text present, text_chars={signal.text_chars}"
    return f"page {signal.page_number}: OCR unavailable ({cause}); degraded to native ({stood_in}); first pass {chosen}"


def _reason(intent: Intent, chosen: str, signal: PageSignal) -> str:
    if intent is Intent.NATIVE:
        return f"page {signal.page_number}: native text sufficient (text_chars={signal.text_chars}); first pass {chosen}"
    if intent is Intent.OCR_TABLES:
        return f"page {signal.page_number}: multi-page dense tables; OCR pass {chosen}"
    if intent is Intent.OCR_VISION:
        return f"page {signal.page_number}: equations/figures-heavy; vision OCR pass {chosen}"
    return f"page {signal.page_number}: no usable native text (blank/garbled); OCR pass {chosen}"
