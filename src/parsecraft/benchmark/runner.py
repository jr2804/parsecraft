"""Run backend benchmarks over local documents (offline, deterministic).

Each document is analyzed with the canonical analyzer (same deterministic
rule as ``pipeline.analysis.choose_analyzer``: native first, then name order),
then every eligible backend converts it once — measured with wall time,
stdlib ``tracemalloc`` peak, chunk census, typed failures, and a
text-coverage proxy against the analyzer's ``text_chars``.
"""

from __future__ import annotations

import tracemalloc
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from time import monotonic

from parsecraft.backends.errors import BackendError
from parsecraft.backends.native._common import DependencyUnavailableError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendDescriptor,
    BackendResult,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.benchmark.models import (
    BenchmarkFailure,
    BenchmarkMetrics,
    BenchmarkReport,
    BenchmarkSkip,
)
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange
from parsecraft.pipeline.analysis import MEDIA_TYPES, choose_analyzer
from parsecraft.routing import RoutingConstraints, RoutingError, RoutingJudge, plan_route
from parsecraft.routing.rules import is_hard_eligible

#: Fixed rounding so reports diff cleanly between runs.
_ROUND_DIGITS = 6

_ZEROS: dict[str, int] = {kind.value: 0 for kind in ChunkKind}


def run_benchmark(
    documents: Sequence[Path],
    registry: BackendRegistry,
    constraints: RoutingConstraints,
    judge: RoutingJudge | None = None,
) -> BenchmarkReport:
    """Benchmark every eligible backend on every readable local document.

    Offline by construction: documents are local paths (``tests/downloads``
    corpus is read, never fetched); absent files are reported as skips.
    """
    results: list[BenchmarkMetrics] = []
    skips: list[BenchmarkSkip] = []
    for path in sorted(documents, key=lambda p: (p.name, str(p))):
        document_results, skip = _benchmark_document(path, registry, constraints, judge)
        results.extend(document_results)
        if skip is not None:
            skips.append(skip)
    results.sort(key=lambda row: (row.document, row.backend))
    skips.sort(key=lambda row: (row.document, row.reason))
    return BenchmarkReport(package_version=_package_version(), results=results, skips=skips)


def _benchmark_document(
    path: Path,
    registry: BackendRegistry,
    constraints: RoutingConstraints,
    judge: RoutingJudge | None,
) -> tuple[list[BenchmarkMetrics], BenchmarkSkip | None]:
    document = path.name
    if not path.is_file():
        return [], BenchmarkSkip(document=document, reason="file not found")
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        return [], BenchmarkSkip(document=document, reason=f"unsupported source suffix {path.suffix!r}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        return [], BenchmarkSkip(document=document, reason=f"unreadable ({type(exc).__name__})")
    source = SourceDocument(uri=path.absolute().as_uri(), media_type=media_type, content=content)

    eligible = [
        descriptor
        for descriptor in sorted(registry.list_backends(), key=lambda d: d.name)
        if is_hard_eligible(descriptor, constraints) and media_type in descriptor.capabilities.supported_formats
    ]
    if not eligible:
        return [], BenchmarkSkip(document=document, reason=f"no eligible backend for {media_type}")

    try:
        analysis = _analyze(source, eligible, registry)
    except Exception as exc:  # analyzer failure is reported as a per-document skip
        return [], BenchmarkSkip(document=document, reason=f"analysis failed ({type(exc).__name__})")

    selected = _selected_backend(eligible, analysis, constraints, judge)
    results = [
        _measure(
            document,
            descriptor,
            source,
            analysis,
            registry,
            selected=None if selected is None else selected == descriptor.name,
        )
        for descriptor in eligible
    ]
    return results, None


def _analyze(source: SourceDocument, eligible: list[BackendDescriptor], registry: BackendRegistry) -> AnalysisResult:
    descriptor = choose_analyzer(eligible, source.media_type or "")
    backend = registry.create(descriptor.name, BackendConfig(name=descriptor.name))
    try:
        return backend.analyze(source)
    finally:
        del backend  # one instance at a time (8 GB VRAM ceiling, see pipeline)


def _selected_backend(
    eligible: list[BackendDescriptor],
    analysis: AnalysisResult,
    constraints: RoutingConstraints,
    judge: RoutingJudge | None,
) -> str | None:
    try:
        return plan_route(analysis, eligible, constraints, judge).primary
    except RoutingError:
        return None  # unplannable document: rows still measured, `selected` stays null


def _measure(
    document: str,
    descriptor: BackendDescriptor,
    source: SourceDocument,
    analysis: AnalysisResult,
    registry: BackendRegistry,
    *,
    selected: bool | None,
) -> BenchmarkMetrics:
    page_range = PageRange(start=1, end=analysis.page_count) if analysis.page_count > 1 else None
    failures: list[BenchmarkFailure] = []
    pages = 0
    chunk_counts = dict(_ZEROS)
    emitted_chars = 0

    tracemalloc.start()
    started = monotonic()
    try:
        result: BackendResult | None = None
        try:
            backend = registry.create(descriptor.name, BackendConfig(name=descriptor.name))
        except BackendError as exc:
            failures.append(_failure_from_exception(exc, descriptor.name))
        else:
            try:
                result = backend.convert(ConversionRequest(source=source, page_range=page_range))
            except BackendError as exc:
                failures.append(_failure_from_exception(exc, descriptor.name))
            finally:
                del backend  # one instance at a time (8 GB VRAM ceiling, see pipeline)
        if result is not None:
            pages = len(result.pages)
            for page in result.pages:
                for block in page.blocks:
                    chunk_counts[block.kind.value] += 1
                    emitted_chars += len(block.content)
            failures.extend(BenchmarkFailure(code=failure.code, detail=failure.detail) for failure in result.failures)
    finally:
        elapsed = monotonic() - started
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

    analyzer_text_chars = sum(signal.text_chars for signal in analysis.signals)
    return BenchmarkMetrics(
        document=document,
        backend=descriptor.name,
        elapsed_s=round(elapsed, _ROUND_DIGITS),
        pages=pages,
        pages_per_second=round(pages / elapsed, _ROUND_DIGITS) if elapsed > 0 and pages > 0 else None,
        chunk_counts=chunk_counts,
        emitted_chars=emitted_chars,
        analyzer_text_chars=analyzer_text_chars,
        text_coverage=round(emitted_chars / analyzer_text_chars, _ROUND_DIGITS) if analyzer_text_chars else None,
        failures=failures,
        peak_memory_bytes=peak,
        selected=selected,
    )


def _failure_from_exception(exc: BackendError, backend: str) -> BenchmarkFailure:
    code = FailureCode.DEPENDENCY_MISSING if isinstance(exc, DependencyUnavailableError) else FailureCode.BACKEND_ERROR
    return BenchmarkFailure(code=code, detail=f"{backend}: {exc}")


def _package_version() -> str:
    try:
        return _dist_version("parsecraft")
    except PackageNotFoundError:
        return "0.0.0"
