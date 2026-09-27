"""Execute a routing plan: plan → contiguous groups → attempts → document.

Keeps :mod:`parsecraft.routing` pure (planning only): this module imports
routing, never the reverse.
"""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from time import monotonic

from parsecraft.backends.errors import BackendError
from parsecraft.backends.native._common import DependencyUnavailableError
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
    TraceEntry,
    utcnow,
)
from parsecraft.pipeline.models import PageGroup, PassAttempt, PipelineResult
from parsecraft.routing import Intent, RoutingConstraints, RoutingJudge, plan_route
from parsecraft.routing.models import PageRoute

#: Placeholder diagnostic emitted when every candidate of a group failed.
ALL_PASSES_FAILED_CODE = "pipeline-all-passes-failed"


def execute(
    analysis: AnalysisResult,
    registry: BackendRegistry,
    constraints: RoutingConstraints,
    source: SourceDocument,
    judge: RoutingJudge | None = None,
    *,
    produced_at: datetime | None = None,
) -> PipelineResult:
    """Plan the route, dispatch contiguous groups, and aggregate the document.

    Deterministic for fixed inputs and a pinned ``produced_at`` (trace timing
    fields are runtime measurements). Heavy imports happen only inside
    ``registry.create`` — the instantiation boundary.
    """
    plan = plan_route(analysis, registry.list_backends(), constraints, judge)
    pages: list[PageResult] = []
    trace: list[TraceEntry] = []
    groups: list[PageGroup] = []
    for group_routes in _group_pages(plan.pages, registry):
        group_pages, group_trace, group = _execute_group(group_routes, registry, source, analysis)
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
    document = DocumentResult(metadata=metadata, pages=pages, trace=trace, quality=[])
    return PipelineResult(document=document, plan=plan, groups=groups)


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
            backend = registry.create(name, BackendConfig(name=name))
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
