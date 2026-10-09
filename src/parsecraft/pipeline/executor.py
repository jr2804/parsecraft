"""Execute a routing plan: plan → contiguous groups → attempts → document.

Keeps :mod:`parsecraft.routing` pure (planning only): this module imports
routing, never the reverse.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from time import monotonic
from typing import Protocol, get_origin

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendResult,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import (
    Diagnostic,
    DiagnosticLevel,
    DocumentMetadata,
    DocumentResult,
    FailureCode,
    PageRange,
    PageResult,
    PassFailure,
    PassKind,
    PassStatus,
    QualitySignal,
    TraceEntry,
    utcnow,
)
from parsecraft.pipeline.models import PageGroup, PassAttempt, PipelineResult
from parsecraft.routing import (
    DeterministicJudge,
    Intent,
    RoutingConstraints,
    RoutingJudge,
    RoutingPlan,
    plan_route,
)
from parsecraft.routing.models import PageRoute

#: Placeholder diagnostic emitted when every candidate of a group failed.
#: The same code names the document-level quality signal, so one greppable name
#: covers the per-page record and the whole-document verdict.
ALL_PASSES_FAILED_CODE = "pipeline-all-passes-failed"


class CacheProtocol(Protocol):
    """Injectable conversion-cache seam (see ``parsecraft.cache``)."""

    def get(self, key: str) -> DocumentResult | None:
        """Stored document for ``key`` or ``None`` (any miss condition)."""
        ...

    def put(self, key: str, result: DocumentResult) -> None:
        """Store a freshly executed document under ``key``."""
        ...


def execute(
    analysis: AnalysisResult,
    registry: BackendRegistry,
    constraints: RoutingConstraints,
    source: SourceDocument,
    judge: RoutingJudge | None = None,
    *,
    produced_at: datetime | None = None,
    cache: CacheProtocol | None = None,
) -> PipelineResult:
    """Plan the route, dispatch contiguous groups, and aggregate the document.

    Deterministic for fixed inputs and a pinned ``produced_at`` (trace timing
    fields are runtime measurements). Heavy imports happen only inside
    ``registry.create`` — the instantiation boundary.

    A document no page of which could be converted is still returned, but it
    carries one document-level quality signal naming the diagnosis
    (:func:`pipeline_failure` reads it back), so callers can fail loudly instead
    of shipping an empty success. Such a document is never written to ``cache``:
    a missing extra is environment state, not a property of the content.

    ``cache`` (default ``None`` = exactly the previous behaviour) is keyed on
    source bytes + registry fingerprint + canonical constraints + effective
    judge identity: a hit returns the stored ``DocumentResult`` without any
    dispatch (its ``groups`` record the planned groups with no attempts —
    nothing was executed); a miss executes and stores. All filesystem policy
    lives behind the injected protocol.
    """
    plan = plan_route(analysis, registry.list_backends(), constraints, judge)
    key = _cache_key(source, registry, constraints, judge) if cache is not None else None
    if cache is not None and key is not None:
        cached = cache.get(key)
        if cached is not None and pipeline_failure(cached) is None:
            return PipelineResult(document=cached, plan=plan, groups=_planned_groups(plan, registry))
    pages: list[PageResult] = []
    trace: list[TraceEntry] = []
    groups: list[PageGroup] = []
    for group_routes in _group_pages(plan.pages, registry):
        group_pages, group_trace, group = _execute_group(group_routes, registry, source, analysis, constraints.offline)
        pages.extend(group_pages)
        trace.extend(group_trace)
        groups.append(group)

    metadata = DocumentMetadata(
        source_uri=source.uri,
        source_hash=analysis.source_hash,
        format=source.media_type or "application/octet-stream",
        page_count=len(pages),
        title=None,
        produced_at=produced_at if produced_at is not None else utcnow(),
        package_version=_package_version(),
    )
    document = DocumentResult(metadata=metadata, pages=pages, trace=trace, quality=_document_quality(plan, pages, trace))
    if cache is not None and key is not None and pipeline_failure(document) is None:
        cache.put(key, document)
    return PipelineResult(document=document, plan=plan, groups=groups)


def pipeline_failure(document: DocumentResult) -> PassFailure | None:
    """The failure that left the whole document empty, or ``None``.

    A document counts as failed only when no page produced a single block **and**
    every page carries the all-passes-failed warning. So a source whose pages
    legitimately convert to nothing is not a failure, and a partial degradation
    (some pages with content, some without) is not one either — that stays a
    successful document with a warning.

    Derived from the document alone rather than from ``PipelineResult.groups``,
    because a cache hit records plan-shaped groups with no attempts; the document
    carries its own verdict either way.
    """
    return _total_failure(document.pages, document.trace)


def _document_quality(
    plan: RoutingPlan,
    pages: Sequence[PageResult],
    trace: Sequence[TraceEntry],
) -> list[QualitySignal]:
    """Plan degradations plus, when nothing converted, one document-level diagnosis."""
    quality = _quality_from_plan(plan)
    failure = _total_failure(pages, trace)
    if failure is not None:
        quality.append(_all_passes_failed_signal(failure, len(pages)))
    return quality


def _total_failure(pages: Sequence[PageResult], trace: Sequence[TraceEntry]) -> PassFailure | None:
    """``pipeline_failure`` over the raw pieces, for the aggregator that has no document yet."""
    if not pages or any(page.blocks for page in pages):
        return None
    flagged = all(any(diagnostic.code == ALL_PASSES_FAILED_CODE for diagnostic in page.diagnostics) for page in pages)
    if not flagged:
        return None
    return next((entry.failure for entry in trace if entry.failure is not None), None)


def _all_passes_failed_signal(failure: PassFailure, page_count: int) -> QualitySignal:
    """One document-level quality signal stating the diagnosis once (``page_number=None``)."""
    return QualitySignal(
        name=ALL_PASSES_FAILED_CODE,
        score=0.0,
        detail=f"all {page_count} page(s) failed: {failure.code.value} in {failure.backend}: {failure.detail}",
        page_number=None,
    )


def _cache_key(
    source: SourceDocument,
    registry: BackendRegistry,
    constraints: RoutingConstraints,
    judge: RoutingJudge | None,
) -> str:
    """sha256 over source bytes, registry fingerprint, constraints, judge id."""
    # ponytail: a content-less source keys on its uri instead of hashing the
    # file (execute must stay I/O-free); upgrade path is hashing the bytes at
    # the read_source boundary, where the file is already open.
    source_bytes = source.content if source.content is not None else source.uri.encode("utf-8")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    effective_judge = judge if judge is not None else DeterministicJudge()
    judge_id = f"{type(effective_judge).__module__}.{type(effective_judge).__qualname__}"
    material = json.dumps(
        [source_hash, registry.fingerprint(), _canonical_constraints(constraints), judge_id],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _canonical_constraints(constraints: RoutingConstraints) -> str:
    """Deterministic JSON: stable key order, set-valued fields sorted."""
    dumped: dict[str, object] = constraints.model_dump(mode="json")
    for name, field in type(constraints).model_fields.items():
        value = dumped.get(name)
        if get_origin(field.annotation) is set and isinstance(value, list):
            dumped[name] = sorted(value)
    return json.dumps(dumped, sort_keys=True, separators=(",", ":"))


def _planned_groups(plan: RoutingPlan, registry: BackendRegistry) -> list[PageGroup]:
    """Plan-shaped groups for a cache hit: nothing was dispatched, no attempts."""
    return [
        PageGroup(
            page_numbers=[route.page_number for route in routes],
            intent=routes[0].intent,
            candidates=routes[0].candidates,
            winner=None,
            attempts=[],
        )
        for routes in _group_pages(plan.pages, registry)
    ]


def _quality_from_plan(plan: RoutingPlan) -> list[QualitySignal]:
    """Degradation travels on the document: the plan is transient, quality isn't."""
    quality: list[QualitySignal] = []
    for route in plan.pages:
        code = route.degradation_code
        score = route.degradation_score
        if code is None or score is None:
            continue
        quality.append(
            QualitySignal(
                name=code,
                score=score,
                detail=route.reason,
                page_number=route.page_number,
            )
        )
    return quality


def _package_version() -> str:
    try:
        return _dist_version("parsecraft")
    except PackageNotFoundError:
        return "0.0.0"


def _group_pages(
    pages: list[PageRoute],
    registry: BackendRegistry,
) -> list[list[PageRoute]]:
    """Bridge per-page plans to per-range dispatch: merge only mergeable neighbors."""
    ordered = sorted(pages, key=lambda route: route.page_number)
    groups: list[list[PageRoute]] = []
    current: list[PageRoute] = []
    for route in ordered:
        if current and _mergeable(current[-1], route, registry):
            current.append(route)
            continue
        if current:
            groups.append(current)
        current = [route]
    if current:
        groups.append(current)
    return groups


def _mergeable(previous: PageRoute, route: PageRoute, registry: BackendRegistry) -> bool:
    same_plan = route.intent is previous.intent and route.chosen == previous.chosen and route.candidates == previous.candidates
    contiguous = route.page_number == previous.page_number + 1
    capabilities = registry.get(previous.chosen).capabilities
    range_capable = capabilities.supports_page_ranges and capabilities.supports_multi_page
    return same_plan and contiguous and range_capable


def _execute_group(
    routes: list[PageRoute],
    registry: BackendRegistry,
    source: SourceDocument,
    analysis: AnalysisResult,
    offline: bool = False,
) -> tuple[list[PageResult], list[TraceEntry], PageGroup]:
    page_numbers = [route.page_number for route in routes]
    intent = routes[0].intent
    candidates = routes[0].candidates
    pass_kind = PassKind.NATIVE if intent is Intent.NATIVE else PassKind.VISUAL
    page_range = None if analysis.page_count == 1 else PageRange(start=page_numbers[0], end=page_numbers[-1])
    settings = {"intent": intent.value}
    attempts: list[PassAttempt] = []
    entries: list[TraceEntry] = []
    pages_out: list[PageResult] = []
    winner: str | None = None

    for name in candidates:
        version = registry.get(name).version
        started = monotonic()
        # ponytail: one backend instance at a time — the 8 GB VRAM ceiling
        # forbids two resident models. Deliberate simplification: swap/reload
        # the instance per attempt instead of an instance pool with eviction;
        # upgrade path: a residency-budgeted pool once concurrent models fit.
        try:
            backend = registry.create(name, BackendConfig(name=name, options={"offline": offline}))
        except BackendError as exc:
            failure, elapsed = _exception_failure(name, version, pass_kind, exc, started)
            attempts.append(PassAttempt(backend=name, status=PassStatus.FAILED, failure=failure))
            entries.append(_trace(name, version, pass_kind, page_range, PassStatus.FAILED, failure=failure, elapsed_s=elapsed, settings=settings))
            continue
        try:
            result = backend.convert(ConversionRequest(source=source, page_range=page_range))
        except BackendError as exc:
            failure, elapsed = _exception_failure(name, version, pass_kind, exc, started)
            attempts.append(PassAttempt(backend=name, status=PassStatus.FAILED, failure=failure))
            entries.append(_trace(name, version, pass_kind, page_range, PassStatus.FAILED, failure=failure, elapsed_s=elapsed, settings=settings))
            continue
        finally:
            del backend  # drop the reference before the next attempt/group (VRAM ceiling)

        failure = _result_failure(result, name, version, pass_kind, page_numbers)
        if failure is not None:
            elapsed = result.elapsed_s
            attempts.append(PassAttempt(backend=name, status=PassStatus.FAILED, failure=failure))
            entries.append(_trace(name, version, pass_kind, page_range, PassStatus.FAILED, failure=failure, elapsed_s=elapsed, settings=settings))
            for extra in result.failures:
                if extra is not failure:
                    entries.append(
                        _trace(
                            name,
                            version,
                            pass_kind,
                            page_range,
                            PassStatus.FAILED,
                            failure=extra,
                            elapsed_s=elapsed,
                            settings=settings,
                        )
                    )
            continue
        attempts.append(PassAttempt(backend=name, status=PassStatus.OK, failure=None))
        entries.append(_trace(name, version, pass_kind, page_range, PassStatus.OK, failure=None, elapsed_s=result.elapsed_s, settings=settings))
        pages_out = result.pages
        winner = name
        break

    if winner is None:
        detail = f"all {len(candidates)} candidates failed: {', '.join(candidates)}"
        pages_out = [
            PageResult(
                page_number=number,
                diagnostics=[
                    Diagnostic(
                        level=DiagnosticLevel.WARNING,
                        code=ALL_PASSES_FAILED_CODE,
                        message=detail,
                    )
                ],
            )
            for number in page_numbers
        ]

    group = PageGroup(
        page_numbers=page_numbers,
        intent=intent,
        candidates=candidates,
        winner=winner,
        attempts=attempts,
    )
    return pages_out, entries, group


def _exception_failure(
    name: str,
    version: str,
    pass_kind: PassKind,
    exc: BackendError,
    started: float,
) -> tuple[PassFailure, float]:
    code = FailureCode.DEPENDENCY_MISSING if isinstance(exc, DependencyUnavailableError) else FailureCode.BACKEND_ERROR
    elapsed = max(monotonic() - started, 0.0)
    failure = PassFailure(
        code=code,
        pass_kind=pass_kind,
        backend=name,
        backend_version=version,
        budget_s=0.0,
        elapsed_s=elapsed,
        detail=str(exc),
        occurred_at=utcnow(),
    )
    return failure, elapsed


def _result_failure(
    result: BackendResult,
    name: str,
    version: str,
    pass_kind: PassKind,
    page_numbers: list[int],
) -> PassFailure | None:
    if result.failures:
        return result.failures[0]
    returned = [page.page_number for page in result.pages]
    if returned != page_numbers:
        return PassFailure(
            code=FailureCode.BACKEND_ERROR,
            pass_kind=pass_kind,
            backend=name,
            backend_version=version,
            budget_s=0.0,
            elapsed_s=result.elapsed_s,
            detail=f"expected pages {page_numbers}, backend returned {returned}",
            occurred_at=utcnow(),
        )
    return None


def _trace(
    name: str,
    version: str,
    pass_kind: PassKind,
    page_range: PageRange | None,
    status: PassStatus,
    *,
    failure: PassFailure | None,
    elapsed_s: float,
    settings: dict[str, str],
) -> TraceEntry:
    return TraceEntry(
        pass_kind=pass_kind,
        backend=name,
        backend_version=version,
        status=status,
        page_range=page_range,
        elapsed_s=elapsed_s,
        settings=settings,
        failure=failure,
    )
