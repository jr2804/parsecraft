"""Offline tests for the benchmark harness: matrix, skips, deterministic writers."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

import pytest

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
from parsecraft.benchmark import (
    BenchmarkReport,
    run_benchmark,
    to_json,
    to_markdown,
    write_json,
    write_markdown,
)
from parsecraft.ir.models import (
    ChunkKind,
    FailureCode,
    PageResult,
    PageSignal,
    PassFailure,
    PassKind,
    StructuredChunk,
)
from parsecraft.routing import RoutingConstraints, RoutingJudge

CHUNKS_PER_PAGE = 50
ANALYZER_CHARS = 100
_FIXED_TIME = "2026-09-27T00:00:00+00:00"

VOLATILE_JSON_KEYS = ("elapsed_s", "pages_per_second", "peak_memory_bytes")


class StubBackend:
    def __init__(
        self,
        descriptor: BackendDescriptor,
        analyze_fn: Any,
        convert_fn: Any,
        calls: list[ConversionRequest],
    ) -> None:
        self.name = descriptor.name
        self.capabilities = descriptor.capabilities
        self._analyze_fn = analyze_fn
        self._convert_fn = convert_fn
        self._calls = calls

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        return self._analyze_fn(source, self.name)

    def convert(self, request: ConversionRequest) -> BackendResult:
        self._calls.append(request)
        return self._convert_fn(request, self.name)


class StubFactory:
    def __init__(
        self,
        descriptor: BackendDescriptor,
        analyze_fn: Any,
        convert_fn: Any,
        create_error: Exception | None,
        calls: list[ConversionRequest],
    ) -> None:
        self.descriptor = descriptor
        self._analyze_fn = analyze_fn
        self._convert_fn = convert_fn
        self._create_error = create_error
        self._calls = calls

    def __call__(self, config: BackendConfig) -> StubBackend:
        if self._create_error is not None:
            raise self._create_error
        return StubBackend(self.descriptor, self._analyze_fn, self._convert_fn, self._calls)


# ── matrix + metrics ───────────────────────────────────────────────────────


def test_matrix_measures_each_eligible_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    add_stub(registry, make_descriptor("native-b"))
    document = make_document(tmp_path, "doc-a.txt")
    report = run_benchmark([document], registry, default_constraints())

    assert report.skips == []
    assert len(report.results) == 2
    assert [(row.document, row.backend) for row in report.results] == [
        ("doc-a.txt", "native-a"),
        ("doc-a.txt", "native-b"),
    ]
    first = report.results[0]
    assert first.pages == 1
    assert first.pages_per_second is not None
    assert first.pages_per_second > 0
    assert set(first.chunk_counts) == {kind.value for kind in ChunkKind}
    assert first.chunk_counts["paragraph"] == 1
    assert first.emitted_chars == CHUNKS_PER_PAGE
    assert first.analyzer_text_chars == ANALYZER_CHARS
    assert first.text_coverage == CHUNKS_PER_PAGE / ANALYZER_CHARS
    assert first.failures == []
    assert first.peak_memory_bytes >= 0
    assert first.elapsed_s >= 0
    assert first.selected is True  # default judge picks native-a (name order)
    assert report.results[1].selected is False


def test_judge_controls_selected_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    add_stub(registry, make_descriptor("native-b"))

    class PreferB:
        @staticmethod
        def rank(intent: Any, candidates: Any, context: Any = None) -> list[str]:
            return ["native-b", "native-a"]

    report = run_benchmark(
        [make_document(tmp_path, "doc-j.txt")],
        registry,
        default_constraints(),
        cast(RoutingJudge, PreferB()),
    )
    selected = {row.backend: row.selected for row in report.results}
    assert selected == {"native-a": False, "native-b": True}


def test_convert_failures_are_typed_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def raising(request: ConversionRequest, name: str) -> BackendResult:
        raise BackendError("convert exploded")

    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=raising)
    add_stub(registry, make_descriptor("native-b"), create_error=DependencyUnavailableError("m", "e"))
    report = run_benchmark([make_document(tmp_path, "doc-f.txt")], registry, default_constraints())
    by_backend = {row.backend: row for row in report.results}
    assert by_backend["native-a"].failures[0].code is FailureCode.BACKEND_ERROR
    assert "convert exploded" in by_backend["native-a"].failures[0].detail
    assert by_backend["native-b"].failures[0].code is FailureCode.DEPENDENCY_MISSING
    assert by_backend["native-a"].pages == 0


def test_reported_pass_failures_are_carried_verbatim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def failing_convert(request: ConversionRequest, name: str) -> BackendResult:
        failure = PassFailure(
            code=FailureCode.TIMEOUT,
            pass_kind=PassKind.NATIVE,
            backend=name,
            backend_version="9.9.9",
            budget_s=5.0,
            elapsed_s=5.1,
            detail="budget breached",
            occurred_at="2026-09-27T00:00:00+00:00",  # pydantic parses the ISO literal into a datetime
        )
        return BackendResult(
            backend=BackendRef(name=name, version="9.9.9"),
            pages=[],
            failures=[failure],
            elapsed_s=5.1,
        )

    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), convert_fn=failing_convert)
    report = run_benchmark([make_document(tmp_path, "doc-t.txt")], registry, default_constraints())
    row = report.results[0]
    assert row.failures[0].code is FailureCode.TIMEOUT
    assert row.failures[0].detail == "budget breached"


def test_zero_pages_and_zero_analyzer_chars_yield_none_rates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def empty_pages(request: ConversionRequest, name: str) -> BackendResult:
        return BackendResult(backend=BackendRef(name=name, version="9.9.9"), pages=[], elapsed_s=0.0)

    def empty_analysis(source: SourceDocument, name: str) -> AnalysisResult:
        return AnalysisResult(
            source_hash="0" * 64,
            page_count=1,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=False,
                    text_chars=0,
                    image_count=0,
                    blank=True,
                    replacement_char_ratio=None,
                )
            ],
            diagnostics=[],
        )

    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), analyze_fn=empty_analysis, convert_fn=empty_pages)
    report = run_benchmark([make_document(tmp_path, "doc-e.txt")], registry, default_constraints())
    row = report.results[0]
    assert row.pages == 0
    assert row.pages_per_second is None
    assert row.text_coverage is None
    # The blank page plans now (pc-ztq: it degrades to native), so the row IS
    # selected — the None rates above come from zero output, not from an
    # unplannable document.
    assert row.selected is True


# ── skips ──────────────────────────────────────────────────────────────────


def test_missing_file_skips_gracefully(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    report = run_benchmark([tmp_path / "absent.pdf"], registry, default_constraints())
    assert report.results == []
    assert [(skip.document, skip.reason) for skip in report.skips] == [("absent.pdf", "file not found")]


def test_unsupported_suffix_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    report = run_benchmark([make_document(tmp_path, "doc.xyz")], registry, default_constraints())
    assert report.skips[0].reason == "unsupported source suffix '.xyz'"


def test_no_eligible_backend_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a", formats=("text/html",)))
    report = run_benchmark([make_document(tmp_path, "doc-n.txt")], registry, default_constraints(formats={"text/markdown"}))
    assert report.results == []
    assert report.skips[0].reason == "no eligible backend for text/plain"


def test_analyzer_failure_skips_document(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken_analyze(source: SourceDocument, name: str) -> AnalysisResult:
        raise BackendError("analyzer down")

    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"), analyze_fn=broken_analyze)
    report = run_benchmark([make_document(tmp_path, "doc-x.txt")], registry, default_constraints())
    assert report.results == []
    assert report.skips[0].reason == "analysis failed (BackendError)"


def test_unreadable_file_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    document = make_document(tmp_path, "doc-u.txt")
    original = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self == document:
            raise OSError("locked")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    report = run_benchmark([document], registry, default_constraints())
    assert report.results == []
    assert report.skips[0].reason == "unreadable (OSError)"


# ── determinism + writers ──────────────────────────────────────────────────


def test_report_has_no_wall_clock_fields() -> None:
    assert set(BenchmarkReport.model_fields) == {"package_version", "results", "skips"}


def test_json_is_deterministic_across_runs_and_input_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    add_stub(registry, make_descriptor("native-b"))
    doc_a = make_document(tmp_path, "doc-a.txt")
    doc_b = make_document(tmp_path, "doc-b.txt")
    first = run_benchmark([doc_a, doc_b], registry, default_constraints())
    second = run_benchmark([doc_b, doc_a], registry, default_constraints())
    first_payload = json.loads(to_json(first))
    second_payload = json.loads(to_json(second))
    assert list(first_payload) == sorted(first_payload)
    assert wiped(first_payload) == wiped(second_payload)


def wiped(payload: Any) -> Any:
    """Remove measured (run-dependent) values so structure can be compared."""
    if isinstance(payload, dict):
        return {key: ("MEASURED" if key in VOLATILE_JSON_KEYS else wiped(value)) for key, value in payload.items()}
    if isinstance(payload, list):
        return [wiped(item) for item in payload]
    return payload


def test_package_version_falls_back_when_metadata_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:

    def raise_missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("parsecraft.benchmark.runner._dist_version", raise_missing)
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    report = run_benchmark([make_document(tmp_path, "doc-v.txt")], registry, default_constraints())
    assert report.package_version == "0.0.0"


def test_package_version_is_the_installed_version() -> None:

    registry_report = BenchmarkReport(package_version=version("parsecraft"), results=[], skips=[])
    assert registry_report.package_version == version("parsecraft")


def test_markdown_contains_rows_and_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    report = run_benchmark(
        [make_document(tmp_path, "doc-a.txt"), tmp_path / "gone.txt"],
        registry,
        default_constraints(),
    )
    markdown = to_markdown(report)
    assert markdown.startswith("# ParseCraft benchmark report")
    assert "| doc-a.txt | native-a |" in markdown
    assert "| gone.txt | file not found |" in markdown
    assert markdown == to_markdown(report)
    assert "\r" not in markdown


def test_file_writers_emit_identical_lf_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    report = run_benchmark([make_document(tmp_path, "doc-a.txt")], registry, default_constraints())
    json_path = write_json(report, tmp_path / "report.json")
    md_path = write_markdown(report, tmp_path / "report.md")
    assert json_path.read_text(encoding="utf-8", newline="") == to_json(report)
    assert md_path.read_text(encoding="utf-8", newline="") == to_markdown(report)
    assert b"\r" not in json_path.read_bytes()
    assert json.loads(json_path.read_text(encoding="utf-8"))["package_version"] == report.package_version


def make_document(tmp_path: Path, name: str, content: str = "local document content") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_staged_corpus_absence_is_a_skip_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = make_registry(monkeypatch)
    add_stub(registry, make_descriptor("native-a"))
    corpus = Path("tests") / "downloads" / "definitely-not-staged.pdf"
    report = run_benchmark([corpus], registry, default_constraints())
    assert report.results == []
    assert report.skips[0].reason == "file not found"


def make_descriptor(name: str, formats: tuple[str, ...] = ("text/plain",)) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        version="9.9.9",
        capabilities=BackendCapabilities(supported_formats=list(formats)),
    )


def make_registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    return BackendRegistry()


def add_stub(
    registry: BackendRegistry,
    descriptor: BackendDescriptor,
    *,
    analyze_fn: Any | None = None,
    convert_fn: Any | None = None,
    create_error: Exception | None = None,
) -> list[ConversionRequest]:
    calls: list[ConversionRequest] = []
    factory = StubFactory(
        descriptor,
        analyze_fn if analyze_fn is not None else default_analyze,
        convert_fn if convert_fn is not None else echo_convert,
        create_error,
        calls,
    )
    registry.register(descriptor.name, factory)
    return calls


def default_analyze(source: SourceDocument, name: str) -> AnalysisResult:
    return AnalysisResult(
        source_hash=hashlib.sha256(source.content or b"").hexdigest(),
        page_count=1,
        signals=[
            PageSignal(
                page_number=1,
                has_native_text=True,
                text_chars=ANALYZER_CHARS,
                image_count=0,
                blank=False,
                replacement_char_ratio=None,
            )
        ],
        diagnostics=[],
    )


def echo_convert(request: ConversionRequest, name: str) -> BackendResult:
    chunk = StructuredChunk(
        id=f"{name}-1",
        kind=ChunkKind.PARAGRAPH,
        content="x" * CHUNKS_PER_PAGE,
        page_number=1,
        reading_order=0,
    )
    return BackendResult(
        backend=BackendRef(name=name, version="9.9.9"),
        pages=[PageResult(page_number=1, blocks=[chunk])],
        elapsed_s=0.0,
    )


def default_constraints(**overrides: Any) -> RoutingConstraints:
    base: dict[str, Any] = {"formats": set(), "installed_extras": set(), "vram_budget_gb": 8.0}
    base.update(overrides)
    return RoutingConstraints(**base)
