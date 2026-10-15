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
    extraction_tier,
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

    The judge the planner builds itself honours ``constraints.preference`` and
    carries the host's ``installed_extras`` (A10: the NATIVE tier is host-fact
    aware); an injected judge carries its own ranking policy (the caller
    resolves it with the same preference — see ``cli/convert.py``).
    """
    if not analysis.signals:
        raise RoutingError("analysis carries no page signals to route")
    active_judge: RoutingJudge = judge if judge is not None else DeterministicJudge(constraints.preference, frozenset(constraints.installed_extras))
    eligible = [descriptor for descriptor in sorted(backends, key=lambda d: d.name) if is_hard_eligible(descriptor, constraints)]
    if constraints.only_backend is not None:
        # A pin is a hard filter: pages it cannot serve fail loudly (pc-4u7,
        # polqa-re). Content is not coverage — a blank page still degrades.
        pinned = [descriptor for descriptor in eligible if descriptor.name == constraints.only_backend]
        if not pinned:
            raise NoEligibleBackendError(None, f"pinned backend {constraints.only_backend!r} is not eligible for this source")
        eligible = pinned
    if not eligible:
        raise NoEligibleBackendError(None, "no backend satisfies the hard constraints")
    hints = extract_hints(analysis)

    pages: list[PageRoute] = []
    for signal in sorted(analysis.signals, key=lambda s: s.page_number):
        intent = classify_page(signal, analysis.page_count, hints)
        family = [descriptor for descriptor in eligible if in_intent_family(intent, descriptor)]
        # Per page, not per document: a caller's preferred backend may be eligible
        # for one page and not another, and the cap must follow the family.
        lead_tier_cap = _lead_tier_cap(constraints, family)
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
        elif _nothing_to_lose(signal) and intent is not Intent.NATIVE and can_degrade_to_native(signal, eligible):
            # A blank page with no images has nothing an OCR pass could return:
            # sending it to a VLM is pure cost (measured: 527 s of ocr-ovis for
            # an empty page). Degrade it even when the OCR family IS available —
            # the reason string records it exactly as it does without a GPU.
            degraded = True
            unavailable = "blank page: no text or images to lose"
            degradation_code, degradation_score = degradation_for(signal)
            intent = Intent.NATIVE
            family = [descriptor for descriptor in eligible if in_intent_family(intent, descriptor)]
        if not family:
            raise NoEligibleBackendError(intent, "intent family empty after hard constraints")
        if intent is Intent.NATIVE and lead_tier_cap is None and all(is_ocr(descriptor) for descriptor in family):
            # Only OCR can answer a NATIVE page and the caller named nothing, so the
            # cap stays at the family's cheapest tier — which is 2, the tier this
            # guard exists to prevent. Keep the old hard failure: a native page must
            # never silently become a VLM run.
            raise NoEligibleBackendError(intent, "no native backend covers the source format")
        order = list(active_judge.rank(intent, family, page_context(intent, signal, hints)))
        order = _apply_preference(order, family, intent, constraints)
        _validate_order(order, family, intent, lead_tier_cap)
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


def _apply_preference(order: list[str], family: list[BackendDescriptor], intent: Intent, constraints: RoutingConstraints) -> list[str]:
    """Move ``constraints.preferred_backend`` to the front.

    Preference is a ranking input, never a hard constraint: a preferred backend
    that is not eligible for this page leaves the order untouched (the normal
    order serves the page - a document never aborts because one page cannot use
    the caller's favourite).

    Naming a backend is the caller choosing, and an OCR choice on a ``NATIVE``
    page is a legitimate choice: the OCR family is the high-accuracy harness for
    figures, tables and equations, and a caller who names it has paid for that
    tier (pc-4u7.41). The *unpriced* case is not handled here - it is the lead
    tier cap in :func:`_validate_order`, which no caller preference can bypass by
    omission and no judge can bypass at all.
    """
    preferred = constraints.preferred_backend
    if preferred is None or preferred not in order:
        return order
    return [preferred, *(name for name in order if name != preferred)]


def _nothing_to_lose(signal: PageSignal) -> bool:
    """A blank page with no images: an OCR pass could not return anything.

    The distinction that matters is coverage versus content (polqa-re): such a
    page is fully *covered* by the OCR family, it just has no content, so OCR
    cost buys nothing. A blank page *with* images stays in the OCR family —
    there is a figure a vision pass could read.
    """
    return signal.blank and signal.image_count == 0


def _validate_order(
    order: list[str],
    family: list[BackendDescriptor],
    intent: Intent,
    lead_tier_cap: int | None = None,
) -> None:
    """One candidate order: subset of the family, no duplicates, tier cap honoured.

    ``lead_tier_cap`` is caller-level and computed per page (a caller's preferred
    backend may be eligible for one page and not another): no judge may lead a
    NATIVE page above the caller's tier.
    """
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
    _validate_native_fallbacks(order, family, intent, lead_tier_cap)


def _validate_native_fallbacks(
    order: list[str],
    family: list[BackendDescriptor],
    intent: Intent,
    lead_tier_cap: int | None = None,
) -> None:
    """A NATIVE page's lead may not exceed the tier the caller asked for.

    The cap defaults to the CHEAPEST tier present in the family, which is what the
    original guard meant: a native page is led by a native candidate unless the
    caller says otherwise, so a provider judge cannot spend GPU minutes on text a
    native backend reads in milliseconds (the reason this rule exists at all).

    The cap is NOT a prohibition on the quality tier. An explicit backend
    preference is the caller paying for that tier, so the cap rises to the tier of
    ``constraints.preferred_backend`` when it is in this page's family — which is
    what makes ``--backend ocr-ovis`` legal on a digital PDF (pc-4u7.41). The OCR
    family is the deliberate high-accuracy harness for figures, tables and
    equations; the rule protects against an unpriced surprise, never against the
    caller's own choice.
    """
    if intent is not Intent.NATIVE:
        return
    by_name = {descriptor.name: descriptor for descriptor in family}
    lead = order[0]
    cap = lead_tier_cap if lead_tier_cap is not None else _default_lead_cap(family)
    if extraction_tier(by_name[lead]) > cap:
        raise JudgeViolationError(
            lead,
            (f"leads above the caller's extraction tier (tier {extraction_tier(by_name[lead])} > {cap}); name the backend to choose it"),
            intent,
        )


def _default_lead_cap(family: Sequence[BackendDescriptor]) -> int:
    """The cap when the caller names no backend: OCR/VLM requires a name.

    Tiers 0 and 1 lead freely (a native or an intelligent parser may win the
    deterministic ranking); tier 2 is the only one that must be asked for, which
    is precisely the rule the old binary guard expressed. If the family holds
    nothing below tier 2, the cap follows the family rather than rejecting the
    only candidate that can answer.
    """
    tiers = {extraction_tier(descriptor) for descriptor in family}
    if tiers & {0, 1}:
        return 1
    return max(tiers, default=1)


def _lead_tier_cap(constraints: RoutingConstraints, family: Sequence[BackendDescriptor]) -> int | None:
    """The highest tier the caller asked for, or ``None`` for the default cap.

    An explicit backend preference raises the cap to that backend's tier. Nothing
    else moves it: no host fact, no judge opinion. The default (``None``) is the
    cheapest tier available in the family, applied inside the validator.
    """
    if constraints.preferred_backend is None:
        return None
    for descriptor in family:
        if descriptor.name == constraints.preferred_backend:
            return extraction_tier(descriptor)
    return None


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

    The parenthetical says WHY native could stand in, and that is genuinely
    three different situations, not two. A blank page has nothing to lose; a
    SCANNED page has no text layer but a pageful of pixels to lose; and a
    thin-text page has a little text. Collapsing the scan into "blank" made the
    one message whose whole purpose is honesty assert something false about the
    very pages OCR exists for (pc-svr).
    """
    return f"page {signal.page_number}: OCR unavailable ({cause}); degraded to native ({_what_native_kept(signal)}); first pass {chosen}"


def _what_native_kept(signal: PageSignal) -> str:
    """Why a native pass could serve this page instead of an OCR one."""
    if not signal.blank:
        return f"native text present, text_chars={signal.text_chars}"
    if signal.image_count == 0:
        return "blank page: no text or images to lose"
    images = "image" if signal.image_count == 1 else "images"
    return f"scanned page: {signal.image_count} {images}, no text layer"


def _reason(intent: Intent, chosen: str, signal: PageSignal) -> str:
    if intent is Intent.NATIVE:
        return f"page {signal.page_number}: native text sufficient (text_chars={signal.text_chars}); first pass {chosen}"
    if intent is Intent.OCR_TABLES:
        return f"page {signal.page_number}: multi-page dense tables; OCR pass {chosen}"
    if intent is Intent.OCR_VISION:
        return f"page {signal.page_number}: equations/figures-heavy; vision OCR pass {chosen}"
    return f"page {signal.page_number}: no usable native text (blank/garbled); OCR pass {chosen}"
