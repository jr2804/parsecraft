"""Offline tests for the routing executor: grouping, fallbacks, aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from parsecraft import pipeline as public_pipeline
from parsecraft.backends import default_registry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendRef,
    BackendResult,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.cache import ConversionCache
from parsecraft.ir.models import (
    ChunkKind,
    DiagnosticLevel,
    FailureCode,
    PageRange,
    PageResult,
    PageSignal,
    PassFailure,
    PassKind,
    PassStatus,
    StructuredChunk,
)
from parsecraft.pipeline import (
    MEDIA_TYPES,
    NoAnalyzerError,
    PageGroup,
    PassAttempt,
    PipelineResult,
    UnsupportedSourceError,
    analyze_source,
    choose_analyzer,
    execute,
    media_type_for,
)
from parsecraft.pipeline.executor import ALL_PASSES_FAILED_CODE
from parsecraft.routing import Intent, RoutingConstraints, RoutingError

PRODUCED = datetime(2026, 9, 27, tzinfo=UTC)
_VERSION = "9.9.9"


class _StubBackend:
    def __init__(self, name: str, capabilities: BackendCapabilities, convert_fn: Any, calls: list[ConversionRequest]) -> None:
        self.name = name
        self.capabilities = capabilities
        self._convert_fn = convert_fn
        self._calls = calls

    def convert(self, request: ConversionRequest) -> BackendResult:
        self._calls.append(request)
        return self._convert_fn(request, self.name)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        raise AssertionError("pipeline must not call analyze()")


class _StubFactory:
    def __init__(
        self,
        descriptor: BackendDescriptor,
        convert_fn: Any,
        calls: list[ConversionRequest],
        create_error: Exception | None = None,
    ) -> None:
        self.descriptor = descriptor
        self._convert_fn = convert_fn
        self._calls = calls
        self._create_error = create_error

    def __call__(self, config: Any) -> _StubBackend:
        if self._create_error is not None:
            raise self._create_error
        return _StubBackend(self.descriptor.name, self.descriptor.capabilities, self._convert_fn, self._calls)


class _WorkingAnalyzer:
    name = "native-a"

    def __init__(self) -> None:
        self.capabilities = make_descriptor("native-a").capabilities

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return make_analysis(1)

    @staticmethod
    def convert(request: ConversionRequest) -> BackendResult:
        raise AssertionError("analyze_source must not convert")


# ── grouping: per-page plan → per-range dispatch ───────────────────────────


def test_contiguous_pages_group_into_one_range(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    result = execute(make_analysis(3), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert len(result.groups) == 1
    assert result.groups[0].page_numbers == [1, 2, 3]
    assert len(calls) == 1
    assert calls[0].page_range == PageRange(start=1, end=3)
    assert [page.page_number for page in result.document.pages] == [1, 2, 3]
    assert len(result.document.trace) == 1
    assert result.document.trace[0].status is PassStatus.OK
    assert result.document.trace[0].page_range == PageRange(start=1, end=3)


def test_range_incapable_backend_converts_single_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a", ranges=False, multi=False))
    result = execute(make_analysis(3), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert [group.page_numbers for group in result.groups] == [[1], [2], [3]]
    assert [call.page_range for call in calls] == [PageRange(start=n, end=n) for n in (1, 2, 3)]
    assert [page.page_number for page in result.document.pages] == [1, 2, 3]


def test_groups_split_when_route_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    add_stub(registry, make_descriptor("ocr-stub", group="ocr-stub"))
    result = execute(make_analysis(2, blank_pages=(2,)), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert [group.page_numbers for group in result.groups] == [[1], [2]]
    assert result.groups[0].intent is Intent.NATIVE
    assert result.groups[1].intent is Intent.OCR_GENERAL
    kinds = [entry.pass_kind for entry in result.document.trace]
    assert kinds == [PassKind.NATIVE, PassKind.VISUAL]
    settings = [entry.settings for entry in result.document.trace]
    assert settings == [{"intent": "native"}, {"intent": "ocr"}]


def test_single_page_document_uses_whole_document_request(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert calls[0].page_range is None


# ── fallbacks ──────────────────────────────────────────────────────────────


def test_fallback_on_typed_pass_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    calls_a = add_stub(registry, make_descriptor("native-a"), convert_fn=fail_timeout)
    calls_b = add_stub(registry, make_descriptor("native-b"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    group = result.groups[0]
    assert group.candidates[0] == "native-a"
    assert group.winner == "native-b"
    assert [attempt.status for attempt in group.attempts] == [PassStatus.FAILED, PassStatus.OK]
    assert group.attempts[0].failure is not None
    assert group.attempts[0].failure.code.value == "timeout"
    assert len(calls_a) == 1
    assert len(calls_b) == 1  # fallback actually dispatched
    statuses = [entry.status for entry in result.document.trace]
    assert statuses == [PassStatus.FAILED, PassStatus.OK]
    assert result.document.trace[0].failure is not None  # failure preserved verbatim
    assert result.document.pages[0].blocks[0].id == "native-b-1"


def test_fallback_on_create_dependency_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(
        registry,
        make_descriptor("native-a"),
        create_error=DependencyUnavailableError("some.module", "some-extra"),
    )
    add_stub(registry, make_descriptor("native-b"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    group = result.groups[0]
    assert group.winner == "native-b"
    assert group.attempts[0].failure is not None
    assert group.attempts[0].failure.code.value == "dependency_missing"
    assert "some.module" in group.attempts[0].failure.detail
    assert "some-extra" in group.attempts[0].failure.detail


def test_fallback_on_convert_raising_backend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=boom)
    add_stub(registry, make_descriptor("native-b"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    group = result.groups[0]
    assert group.winner == "native-b"
    assert group.attempts[0].failure is not None
    assert group.attempts[0].failure.code.value == "backend_error"
    assert "convert exploded" in group.attempts[0].failure.detail


def boom(request: ConversionRequest, name: str) -> BackendResult:
    raise BackendError("convert exploded")


def test_fallback_on_wrong_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=wrong_pages)
    add_stub(registry, make_descriptor("native-b"))
    result = execute(make_analysis(2), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    group = result.groups[0]
    assert group.winner == "native-b"
    assert group.attempts[0].failure is not None
    assert "expected pages [1, 2]" in group.attempts[0].failure.detail
    assert "returned [1]" in group.attempts[0].failure.detail


def wrong_pages(request: ConversionRequest, name: str) -> BackendResult:
    page = PageResult(
        page_number=1,
        blocks=[
            StructuredChunk(
                id=f"{name}-1",
                kind=ChunkKind.PARAGRAPH,
                content="only page one",
                page_number=1,
                reading_order=0,
            )
        ],
    )
    return BackendResult(backend=BackendRef(name=name, version=_VERSION), pages=[page], elapsed_s=0.01)


def test_all_candidates_fail_is_loud_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=fail_timeout)
    result = execute(make_analysis(2), registry, make_constraints(max_passes=1), make_source(), produced_at=PRODUCED)
    group = result.groups[0]
    assert group.winner is None
    assert len(group.attempts) == 1
    document = result.document
    assert document.metadata.page_count == len(document.pages) == 2
    for page in document.pages:
        assert page.blocks == []
        assert [d.code for d in page.diagnostics] == [ALL_PASSES_FAILED_CODE]
        assert page.diagnostics[0].level is DiagnosticLevel.WARNING
    assert all(entry.status is PassStatus.FAILED for entry in document.trace)


def test_all_reported_failures_reach_the_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_twice(request: ConversionRequest, name: str) -> BackendResult:
        first = PassFailure(
            code=FailureCode.TIMEOUT,
            pass_kind=PassKind.NATIVE,
            backend=name,
            backend_version=_VERSION,
            budget_s=5.0,
            elapsed_s=5.1,
            detail="first failure",
            occurred_at=PRODUCED,
        )
        second = PassFailure(
            code=FailureCode.BACKEND_ERROR,
            pass_kind=PassKind.NATIVE,
            backend=name,
            backend_version=_VERSION,
            budget_s=0.0,
            elapsed_s=5.1,
            detail="second failure",
            occurred_at=PRODUCED,
        )
        return BackendResult(
            backend=BackendRef(name=name, version=_VERSION),
            pages=[],
            failures=[first, second],
            elapsed_s=5.1,
        )

    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=fail_twice)
    add_stub(registry, make_descriptor("native-b"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    failed_entries = [entry for entry in result.document.trace if entry.status is PassStatus.FAILED]
    assert len(failed_entries) == 2
    assert [entry.failure.detail for entry in failed_entries if entry.failure is not None] == [
        "first failure",
        "second failure",
    ]
    assert result.groups[0].winner == "native-b"


def test_injected_judge_controls_dispatch_order(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    calls_a = add_stub(registry, make_descriptor("native-a"))
    calls_b = add_stub(registry, make_descriptor("native-b"))

    class PreferB:
        @staticmethod
        def rank(intent: Intent, candidates: Any) -> list[str]:
            names = sorted(descriptor.name for descriptor in candidates)
            return sorted(names, key=lambda name: (name != "native-b", name))

    result = execute(make_analysis(1), registry, make_constraints(), make_source(), PreferB(), produced_at=PRODUCED)
    assert result.groups[0].winner == "native-b"
    assert calls_a == []
    assert len(calls_b) == 1


# ── aggregation + determinism ──────────────────────────────────────────────


def test_aggregates_metadata_format_and_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    result = execute(make_analysis(3), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    metadata = result.document.metadata
    assert metadata.source_uri == "mem://pipeline"
    assert metadata.source_hash == "0" * 64
    assert metadata.format == "text/plain"
    assert metadata.page_count == 3
    assert metadata.produced_at == PRODUCED
    assert metadata.package_version
    assert result.plan.pages[0].chosen == "native-a"
    assert [page.reading_order for page in result.document.pages[0].blocks] == [0]


def test_media_type_falls_back_to_octet_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(media_type=None), produced_at=PRODUCED)
    assert result.document.metadata.format == "application/octet-stream"


def test_package_version_falls_back_when_metadata_missing(monkeypatch: pytest.MonkeyPatch) -> None:

    def raise_missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("parsecraft.pipeline.executor._dist_version", raise_missing)
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    result = execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert result.document.metadata.package_version == "0.0.0"


def test_execute_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=fail_timeout)
    add_stub(registry, make_descriptor("native-b"))
    analysis = make_analysis(3)
    constraints = make_constraints()
    first = execute(analysis, registry, constraints, make_source(), produced_at=PRODUCED)
    second = execute(analysis, registry, constraints, make_source(), produced_at=PRODUCED)
    assert normalized(first) == normalized(second)


def fail_timeout(request: ConversionRequest, name: str) -> BackendResult:
    failure = PassFailure(
        code=FailureCode.TIMEOUT,
        pass_kind=PassKind.NATIVE,
        backend=name,
        backend_version=_VERSION,
        budget_s=5.0,
        elapsed_s=5.1,
        detail="convert exceeded budget",
        occurred_at=PRODUCED,
    )
    return BackendResult(backend=BackendRef(name=name, version=_VERSION), pages=[], failures=[failure], elapsed_s=5.1)


def normalized(result: PipelineResult) -> dict[str, Any]:
    """Determinism view: runtime timing/timestamps wiped, structure kept."""
    data = result.model_dump(mode="json")

    def wipe(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "elapsed_s":
                    obj[key] = 0
                elif key == "occurred_at":
                    obj[key] = "FIXED"
                else:
                    wipe(value)
        elif isinstance(obj, list):
            for item in obj:
                wipe(item)

    wipe(data)
    return data


def test_empty_signals_propagates_routing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    analysis = AnalysisResult(source_hash="0" * 64, page_count=0, signals=[], diagnostics=[])
    with pytest.raises(RoutingError, match="no page signals"):
        execute(analysis, registry, make_constraints(), make_source(), produced_at=PRODUCED)


# ── model validators ───────────────────────────────────────────────────────


def test_pass_attempt_status_failure_bijection() -> None:
    with pytest.raises(ValidationError, match="must appear together"):
        PassAttempt(backend="x", status=PassStatus.FAILED, failure=None)
    with pytest.raises(ValidationError, match="must appear together"):
        PassAttempt(backend="x", status=PassStatus.OK, failure=make_failure())


def test_page_group_validators() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        PageGroup(page_numbers=[1, 3], intent=Intent.NATIVE, candidates=["a"])
    with pytest.raises(ValidationError, match="winner missing"):
        PageGroup(
            page_numbers=[1],
            intent=Intent.NATIVE,
            candidates=["a"],
            attempts=[PassAttempt(backend="a", status=PassStatus.OK)],
        )
    with pytest.raises(ValidationError, match="must be an OK-attempted"):
        PageGroup(page_numbers=[1], intent=Intent.NATIVE, candidates=["a", "b"], winner="b")
    with pytest.raises(ValidationError, match="must be an OK-attempted"):
        PageGroup(
            page_numbers=[1],
            intent=Intent.NATIVE,
            candidates=["a"],
            winner="a",
            attempts=[PassAttempt(backend="a", status=PassStatus.FAILED, failure=make_failure())],
        )


def make_failure() -> PassFailure:
    return PassFailure(
        code=FailureCode.BACKEND_ERROR,
        pass_kind=PassKind.NATIVE,
        backend="a",
        backend_version=_VERSION,
        budget_s=0.0,
        elapsed_s=0.0,
        detail="boom",
        occurred_at=PRODUCED,
    )


# ── public analysis API (parsecraft.pipeline) ──────────────────────────────


def test_public_analysis_names_are_exported_from_pipeline() -> None:
    for name in ("MEDIA_TYPES", "analyze_source", "choose_analyzer", "media_type_for"):
        assert name in public_pipeline.__all__
        assert hasattr(public_pipeline, name)
    assert public_pipeline.analyze_source is analyze_source
    assert public_pipeline.media_type_for is media_type_for


def test_analyze_source_uses_the_canonical_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)

    class Factory:
        descriptor = make_descriptor("native-a")

        @staticmethod
        def __call__(config: BackendConfig) -> _WorkingAnalyzer:
            return _WorkingAnalyzer()

    registry.register("native-a", Factory())
    analysis = analyze_source(make_source(), registry, media_type="text/plain")
    assert analysis.page_count == 1
    assert analysis.source_hash == "0" * 64


def test_analyze_source_propagates_backend_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)

    class FailingFactory:
        descriptor = make_descriptor("native-a")

        @staticmethod
        def __call__(config: BackendConfig) -> _WorkingAnalyzer:
            raise BackendError("analyzer exploded")

    registry.register("native-a", FailingFactory())
    with pytest.raises(BackendError, match="analyzer exploded"):
        analyze_source(make_source(), registry, media_type="text/plain")


def test_media_type_for_covers_extended_families(tmp_path: Path) -> None:
    assert media_type_for(tmp_path / "script.py") == "text/plain"
    assert media_type_for(tmp_path / "scan.PNG") == "image/png"
    assert media_type_for(tmp_path / "page.tiff") == "image/tiff"
    with pytest.raises(UnsupportedSourceError, match="unsupported source"):
        media_type_for(tmp_path / "data.xyz")


def test_media_types_values_are_claimed_by_registered_backends() -> None:
    # Classifier ⇄ capability invariant: no MIME in the table may drift away
    # from what installed backends actually declare.
    claimed: set[str] = set()
    for descriptor in default_registry.list_backends():
        claimed.update(descriptor.capabilities.supported_formats)
    unclaimed = set(MEDIA_TYPES.values()) - claimed
    assert unclaimed == set()


def test_choose_analyzer_prefers_native_then_name() -> None:
    native = make_descriptor("native-z")
    other = make_descriptor("aaa-first")
    assert choose_analyzer([other, native], "text/plain") is native
    with pytest.raises(NoAnalyzerError, match="no installed backend can analyze"):
        choose_analyzer([native], "application/pdf")


def test_choose_analyzer_none_keeps_historical_behaviour() -> None:
    claimers = _claimers()
    assert choose_analyzer(claimers, "image/png") is choose_analyzer(claimers, "image/png", installed_extras=None)
    # native-first then name, regardless of extras
    assert choose_analyzer(claimers, "image/png").name == "native-a"


def test_choose_analyzer_prefers_installed_extra_over_name_order() -> None:
    claimers = [make_descriptor("liteparse", formats=("image/png",), group="liteparse"), make_descriptor("ocr-z", formats=("image/png",), group="ocr-z")]
    # name order alone would pick liteparse; availability flips it
    assert choose_analyzer(claimers, "image/png").name == "liteparse"
    chosen = choose_analyzer(claimers, "image/png", installed_extras={"ocr-z"})
    assert chosen.name == "ocr-z"


def test_choose_analyzer_prefers_dependency_free_over_extra_claimers() -> None:
    claimers = [
        make_descriptor("liteparse", formats=("image/png",), group="liteparse"),
        make_descriptor("native-a", formats=("image/png",)),
    ]
    # no extras installed: the dependency-free native analyzer must win
    chosen = choose_analyzer(claimers, "image/png", installed_extras=set())
    assert chosen.name == "native-a"


def test_choose_analyzer_all_unavailable_falls_back_to_name_order() -> None:
    claimers = [
        make_descriptor("liteparse", formats=("image/png",), group="liteparse"),
        make_descriptor("ocr-z", formats=("image/png",), group="ocr-z"),
        make_descriptor("native-b", formats=("image/png",), group="missing-extra"),
    ]
    chosen = choose_analyzer(claimers, "image/png", installed_extras=set())
    assert chosen.name == "native-b"  # fallback pool, same native-first-then-name key


def test_choose_analyzer_is_deterministic_across_input_order() -> None:
    claimers = _claimers()
    expected = choose_analyzer(claimers, "image/png", installed_extras={"ocr-z"}).name
    assert choose_analyzer(list(reversed(claimers)), "image/png", installed_extras={"ocr-z"}).name == expected
    fallback = choose_analyzer(claimers, "image/png", installed_extras=set()).name
    assert choose_analyzer(list(reversed(claimers)), "image/png", installed_extras=set()).name == fallback


def test_choose_analyzer_still_raises_no_analyzer_with_extras() -> None:
    with pytest.raises(NoAnalyzerError):
        choose_analyzer(_claimers(), "application/pdf", installed_extras={"ocr-z"})


# ── availability-aware analyzer selection (pc-4u7.27) ──────────────────────


def _claimers() -> list[BackendDescriptor]:
    return [
        make_descriptor("liteparse", formats=("image/png",), group="liteparse"),
        make_descriptor("ocr-z", formats=("image/png",), group="ocr-z"),
        make_descriptor("native-a", formats=("image/png",), group="missing-extra"),
    ]


def test_analyze_source_threads_installed_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)

    class ImageAnalyzer:
        def __init__(self, name: str, pages: int) -> None:
            self.name = name
            self.capabilities = make_descriptor(name, formats=("image/png",), group="ocr-z" if name == "ocr-z" else "liteparse").capabilities
            self._pages = pages

        def analyze(self, source: SourceDocument) -> AnalysisResult:
            return make_analysis(self._pages)

        @staticmethod
        def convert(request: ConversionRequest) -> BackendResult:
            raise AssertionError("analyze_source must not convert")

    class Factory:
        descriptor = make_descriptor("liteparse", formats=("image/png",), group="liteparse")

        def __init__(self, name: str, pages: int) -> None:
            self.descriptor = make_descriptor(name, formats=("image/png",), group="ocr-z" if name == "ocr-z" else "liteparse")
            self._pages = pages

        def __call__(self, config: BackendConfig) -> ImageAnalyzer:
            return ImageAnalyzer(self.descriptor.name, self._pages)

    registry.register("liteparse", Factory("liteparse", 1))
    registry.register("ocr-z", Factory("ocr-z", 2))

    picked = analyze_source(make_source(), registry, media_type="image/png", installed_extras={"ocr-z"})
    assert picked.page_count == 2  # ocr-z is available and was preferred
    default = analyze_source(make_source(), registry, media_type="image/png")
    assert default.page_count == 1  # None → historical name order → liteparse


# ── conversion-cache seam (execute) ────────────────────────────────────────


def _run_with_cache(
    tmp_path: Path,
    registry: BackendRegistry,
    calls: list[ConversionRequest],
    **kwargs: Any,
) -> Any:
    store = ConversionCache(root=tmp_path / "conversions")
    source = kwargs.pop("source", None) or make_source()
    produced_at = kwargs.pop("produced_at", PRODUCED)
    first = execute(make_analysis(1), registry, make_constraints(), source, produced_at=produced_at, cache=store, **kwargs)
    dispatched_after_first = len(calls)
    second = execute(make_analysis(1), registry, make_constraints(), source, produced_at=produced_at, cache=store, **kwargs)
    return first, second, dispatched_after_first


def test_cache_hit_returns_identical_ir_without_redispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = make_source()

    first = execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert len(calls) == 1
    second = execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert len(calls) == 1  # no re-dispatch on hit
    assert first.document == second.document  # byte-identical stored IR
    assert first.plan == second.plan
    # hit groups are plan-shaped: nothing was dispatched, no attempts
    for group in second.groups:
        assert group.winner is None
        assert group.attempts == []


def test_cache_miss_on_changed_fingerprint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = make_source()

    execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert len(calls) == 1
    add_stub(registry, make_descriptor("native-b"))  # registry fingerprint changes
    execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert len(calls) == 2  # changed fingerprint must miss, never stale reuse


def test_cache_miss_on_changed_constraints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = make_source()

    execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    execute(
        make_analysis(1),
        registry,
        make_constraints(max_passes=2),
        source,
        produced_at=PRODUCED,
        cache=store,
    )
    assert len(calls) == 2  # different constraints ⇒ different key


def test_cache_miss_on_changed_judge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = make_source()

    execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)

    class ReverseJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any) -> list[str]:
            return [descriptor.name for descriptor in sorted(candidates, key=lambda d: d.name, reverse=True)]

    execute(
        make_analysis(1),
        registry,
        make_constraints(),
        source,
        judge=ReverseJudge(),
        produced_at=PRODUCED,
        cache=store,
    )
    assert len(calls) == 2  # judge identity is part of the key


def test_cache_hit_survives_corrupt_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    calls = add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = make_source()

    execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    for entry in (tmp_path / "conversions").iterdir():
        entry.write_text("garbage not json", encoding="utf-8")
    # corrupt entry = miss, never an error: it re-executes and overwrites
    result = execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert len(calls) == 2
    assert result.document.pages


def test_execute_without_cache_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    execute(make_analysis(1), registry, make_constraints(), make_source(), produced_at=PRODUCED)
    assert not store.root.exists()


def make_source(media_type: str | None = "text/plain") -> SourceDocument:
    return SourceDocument(uri="mem://pipeline", media_type=media_type, content=b"data")


def test_cache_key_falls_back_to_uri_for_content_less_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    store = ConversionCache(root=tmp_path / "conversions")
    source = SourceDocument(uri="mem://content-less", media_type="text/plain", content=None)
    result = execute(make_analysis(1), registry, make_constraints(), source, produced_at=PRODUCED, cache=store)
    assert result.document.pages
    assert store.entries()


def add_stub(
    registry: BackendRegistry,
    descriptor: BackendDescriptor,
    convert_fn: Any | None = None,
    create_error: Exception | None = None,
) -> list[ConversionRequest]:
    # Resolved at call time, not def time: csort's stepdown reorders functions.
    behavior = convert_fn if convert_fn is not None else echo_ok
    calls: list[ConversionRequest] = []
    registry.register(descriptor.name, _StubFactory(descriptor, behavior, calls, create_error))
    return calls


def echo_ok(request: ConversionRequest, name: str) -> BackendResult:
    page_range = request.page_range
    numbers = list(range(page_range.start, page_range.end + 1)) if page_range is not None else [1]
    pages = [
        PageResult(
            page_number=number,
            blocks=[
                StructuredChunk(
                    id=f"{name}-{number}",
                    kind=ChunkKind.PARAGRAPH,
                    content=f"content {number}",
                    page_number=number,
                    reading_order=0,
                )
            ],
        )
        for number in numbers
    ]
    return BackendResult(backend=BackendRef(name=name, version=_VERSION), pages=pages, elapsed_s=0.01)


def make_constraints(**overrides: Any) -> RoutingConstraints:
    base: dict[str, Any] = {
        "formats": {"text/plain"},
        "installed_extras": {"ocr-stub"},
        "vram_budget_gb": 8.0,
        "max_passes": 3,
    }
    base.update(overrides)
    return RoutingConstraints(**base)


def make_analysis(pages: int, *, blank_pages: tuple[int, ...] = ()) -> AnalysisResult:
    signals = [make_signal(page, text_chars=5 if page in blank_pages else 500, blank=page in blank_pages) for page in range(1, pages + 1)]
    return AnalysisResult(source_hash="0" * 64, page_count=pages, signals=signals, diagnostics=[])


def make_signal(page: int, *, text_chars: int = 500, blank: bool = False) -> PageSignal:
    return PageSignal(
        page_number=page,
        has_native_text=not blank,
        text_chars=text_chars,
        image_count=0,
        blank=blank,
        replacement_char_ratio=None,
    )


def make_registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    """Fresh registry with entry-point discovery stubbed out (offline, isolated)."""
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    return BackendRegistry()


def make_descriptor(name: str, **kwargs: Any) -> BackendDescriptor:
    return BackendDescriptor(name=name, version=_VERSION, capabilities=make_caps(**kwargs))


def make_caps(
    formats: tuple[str, ...] = ("text/plain",),
    *,
    ranges: bool = True,
    multi: bool = True,
    gpu: bool = False,
    vram: float | None = None,
    group: str | None = None,
) -> BackendCapabilities:
    return BackendCapabilities(
        supported_formats=list(formats),
        supports_page_ranges=ranges,
        supports_multi_page=multi,
        requires_gpu=gpu,
        estimated_vram_gb=vram,
        optional_dependency_group=group,
    )
