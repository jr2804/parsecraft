"""pdf-inspector classifier provider: spec contract, IR-based facts, typed failures.

Fully offline: the heavy ``pdf_inspector`` extension is a stub in ``sys.modules``
and the distribution version is patched — the same pattern as the backend
families. One live test locks the upstream 1-based page indexing and skips when
the extra is absent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    PageSignal,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import ChunkKind, PageResult, StructuredChunk
from parsecraft.pipeline.analysis import analyze_source
from parsecraft.providers import pdfinspector as provider
from parsecraft.routing import Intent, RoutingConstraints, plan_route
from parsecraft.routing.classifier import (
    CLASSIFIER_PROVENANCE_CODE,
    ClassifierError,
    ClassifierProviderLoadError,
    ClassifierProviderUnavailableError,
    ClassifierSpecError,
    PageOcrClassifier,
    parse_classifier_spec,
    resolve_classifier,
)
from tests.fixtures.documents import minimal_pdf, mixed_scanned_pdf

_VERSION = "9.9.9"
_TEXT_PAGE = "A paragraph comfortably longer than the forty character routing threshold."
_PDF_BYTES = minimal_pdf([[_TEXT_PAGE], ["A second text page, also long enough to be native."]])
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)
_SPEC = "pdfinspector/detect_pdf"


# ── Stub: the heavy `pdf_inspector` extension, faked through sys.modules ────────


class _Detection:
    """Stand-in for pdf_inspector's PdfResult detection fields."""

    def __init__(
        self,
        *,
        pdf_type: str = "text_based",
        confidence: float = 0.5,
        pages_needing_ocr: tuple[int, ...] = (),
        pages_with_tables: tuple[int, ...] = (),
    ) -> None:
        self.pdf_type = pdf_type
        self.confidence = confidence
        self.pages_needing_ocr = list(pages_needing_ocr)
        self.pages_with_tables = list(pages_with_tables)


class _Stub:
    """Fake ``pdf_inspector`` module: state scripted by each test."""

    def __init__(self) -> None:
        self.detection = _Detection()
        self.detect_error: Exception | None = None
        self.detect_calls: list[bytes] = []
        self.ocr_calls = 0
        self.extract_calls = 0

    def detect_pdf_bytes(self, data: bytes) -> _Detection:
        self.detect_calls.append(data)
        if self.detect_error is not None:
            raise self.detect_error
        return self.detection

    # The OCR and extraction entry points are out of contract (model-free, and
    # the classifier never extracts content): reaching either records a call so
    # a regression fails loudly instead of loading a model.
    def process_pdf_with_ocr_bytes(self, data: bytes) -> None:
        self.ocr_calls += 1

    def extract_pages_markdown_bytes(self, data: bytes, pages: list[int] | None = None) -> None:
        self.extract_calls += 1


# ── Stub backends and registry ──────────────────────────────────────────────────


class _StubBackend:
    """Minimal DocumentBackend: two native-text pages, echo conversion."""

    def __init__(self, name: str, capabilities: BackendCapabilities) -> None:
        self.name = name
        self.capabilities = capabilities

    def convert(self, request: ConversionRequest) -> BackendResult:
        page_range = request.page_range
        numbers = list(range(page_range.start, page_range.end + 1)) if page_range is not None else [1, 2]
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.0.0"),
            pages=[
                PageResult(
                    page_number=number,
                    blocks=[
                        StructuredChunk(
                            id=f"{self.name}-{number}",
                            kind=ChunkKind.PARAGRAPH,
                            content=f"{self.name} content {number}",
                            page_number=number,
                            reading_order=0,
                        )
                    ],
                )
                for number in numbers
            ],
            elapsed_s=0.0,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return AnalysisResult(
            source_hash="0" * 64,
            page_count=2,
            signals=[PageSignal(page_number=number, has_native_text=True, text_chars=len(_TEXT_PAGE), image_count=0, blank=False) for number in (1, 2)],
        )


class _StubFactory:
    def __init__(self, descriptor: BackendDescriptor) -> None:
        self.descriptor = descriptor

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _StubBackend(self.descriptor.name, self.descriptor.capabilities)


# ── load_classifier: spec contract ─────────────────────────────────────────────


def test_load_classifier_returns_a_page_ocr_classifier(stub: _Stub) -> None:
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    assert isinstance(classifier, PageOcrClassifier)


def test_resolve_classifier_loads_the_provider_lazily(stub: _Stub) -> None:
    """The spec string path resolves through `parsecraft.providers.pdfinspector`."""
    classifier = resolve_classifier(_SPEC)
    assert isinstance(classifier, PageOcrClassifier)
    assert classifier is not None  # resolve_classifier(None) is None, a spec is not


@pytest.mark.parametrize("spec", ["pdfinspector/other", "pdfinspector/detect_pdf:fast"])
def test_load_classifier_rejects_other_modes_and_variants(stub: _Stub, spec: str) -> None:
    with pytest.raises(ClassifierSpecError, match="supports only pdfinspector/detect_pdf"):
        provider.load_classifier(parse_classifier_spec(spec))


def test_bad_spec_string_is_a_spec_error(stub: _Stub) -> None:
    with pytest.raises(ClassifierSpecError, match="must look like"):
        resolve_classifier("pdfinspector")


def test_missing_extra_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("pdf_inspector")

    monkeypatch.setattr(provider, "import_module", _raise)
    with pytest.raises(ClassifierProviderUnavailableError, match="'pdf-inspector' extra") as excinfo:
        provider.load_classifier(parse_classifier_spec(_SPEC))
    assert (excinfo.value.provider, excinfo.value.module_name) == ("pdfinspector", "pdf_inspector")


def test_module_without_the_detection_entry_point_is_a_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pdf_inspector", importlib)
    with pytest.raises(ClassifierProviderLoadError, match="does not export detect_pdf_bytes"):
        provider.load_classifier(parse_classifier_spec(_SPEC))


# ── classify: IR-based facts, verbatim mapping ──────────────────────────────────


def test_classify_maps_detection_fields_verbatim(stub: _Stub) -> None:
    stub.detection = _Detection(pdf_type="mixed", confidence=0.75, pages_needing_ocr=(1, 3), pages_with_tables=(2,))
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    facts = classifier.classify(_PDF_SOURCE)
    # Verbatim: detect_pdf is already IR 1-based, so nothing converts here.
    assert facts.pages_needing_ocr == frozenset({1, 3})
    assert facts.pages_with_tables == frozenset({2})
    assert facts.pdf_type == "mixed"
    assert facts.confidence == 0.75
    assert facts.source == f"pdf-inspector {_VERSION}"
    assert stub.detect_calls == [_PDF_BYTES]


def test_classify_never_touches_the_ocr_or_extraction_entry_points(stub: _Stub) -> None:
    stub.detection = _Detection(pages_needing_ocr=(1,))
    provider.load_classifier(parse_classifier_spec(_SPEC)).classify(_PDF_SOURCE)
    assert (stub.ocr_calls, stub.extract_calls) == (0, 0)


def test_classify_rejects_zero_based_page_numbers(stub: _Stub) -> None:
    """A 0-based upstream answer is a contract violation, never a plan (ADR-0004 A4)."""
    stub.detection = _Detection(pages_needing_ocr=(0,))
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    with pytest.raises(ClassifierError, match="1-based by contract"):
        classifier.classify(_PDF_SOURCE)


def test_classify_wraps_invalid_input_as_a_typed_failure(stub: _Stub) -> None:
    stub.detect_error = ValueError("Not a PDF: file appears to be plain text")
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    with pytest.raises(ClassifierError, match="pdf-inspector cannot classify") as excinfo:
        classifier.classify(_PDF_SOURCE)
    assert "plain text" in str(excinfo.value)


def test_classify_wraps_unexpected_failures_as_a_typed_failure(stub: _Stub) -> None:
    stub.detect_error = RuntimeError("rust detector exploded")
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    with pytest.raises(ClassifierError, match="RuntimeError: rust detector exploded"):
        classifier.classify(_PDF_SOURCE)


def test_classify_reads_local_file_uris_and_reports_unreadable_ones(tmp_path: Path, stub: _Stub) -> None:
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    target = tmp_path / "scan.pdf"
    target.write_bytes(_PDF_BYTES)
    classifier.classify(SourceDocument(uri=target.absolute().as_uri(), media_type="application/pdf"))
    assert stub.detect_calls == [_PDF_BYTES]

    missing = SourceDocument(uri=(tmp_path / "missing.pdf").absolute().as_uri(), media_type="application/pdf")
    with pytest.raises(ClassifierError, match="cannot read source"):
        classifier.classify(missing)


# ── Integration: analysis fold → routing ────────────────────────────────────────


def test_classifier_adds_ocr_need_and_routes_pages_apart(stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    """One flag: page 1 gains OCR need, page 2 keeps its rule-table NATIVE route."""
    stub.detection = _Detection(pdf_type="mixed", pages_needing_ocr=(1,), pages_with_tables=())
    registry = _registry(monkeypatch)
    _register(registry, "native-pdf")
    _register(registry, "ocr-stub")
    classifier = resolve_classifier(_SPEC)
    analysis = analyze_source(
        _PDF_SOURCE,
        registry,
        media_type="application/pdf",
        installed_extras={"pdf-inspector"},
        classifier=classifier,
    )
    assert [signal.classifier_needs_ocr for signal in analysis.signals] == [True, None]
    assert any(diagnostic.code == CLASSIFIER_PROVENANCE_CODE for diagnostic in analysis.diagnostics)

    plan = plan_route(analysis, registry.list_backends(), _constraints())
    assert [route.intent for route in plan.pages] == [Intent.OCR_GENERAL, Intent.NATIVE]
    assert [route.chosen for route in plan.pages] == ["ocr-stub", "native-pdf"]


def test_flagged_page_without_ocr_permission_degrades_to_native(stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1 at plan level: the flag adds OCR need, hard constraints still rule.

    With `allow_ocr=False` the flagged page cannot reach the OCR family, so the
    planner degrades it to NATIVE with a recorded reason — the classifier widens
    candidacy, it never overrides a code-owned constraint.
    """
    stub.detection = _Detection(pages_needing_ocr=(1,))
    registry = _registry(monkeypatch)
    _register(registry, "native-pdf")
    _register(registry, "ocr-stub")
    analysis = analyze_source(
        _PDF_SOURCE,
        registry,
        media_type="application/pdf",
        installed_extras={"pdf-inspector"},
        classifier=resolve_classifier(_SPEC),
    )
    assert analysis.signals[0].classifier_needs_ocr is True

    plan = plan_route(analysis, registry.list_backends(), _constraints(allow_ocr=False))
    assert [route.intent for route in plan.pages] == [Intent.NATIVE, Intent.NATIVE]
    assert [route.candidates for route in plan.pages] == [["native-pdf"], ["native-pdf"]]
    assert plan.pages[0].degradation_code is not None  # recorded, not silent


def test_absent_classifier_keeps_the_rule_table_route(stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    """No classifier: the analysis and the plan are exactly the pre-seam behaviour."""
    stub.detection = _Detection(pages_needing_ocr=(1,))
    registry = _registry(monkeypatch)
    _register(registry, "native-pdf")
    _register(registry, "ocr-stub")
    analysis = analyze_source(_PDF_SOURCE, registry, media_type="application/pdf", installed_extras={"pdf-inspector"})
    assert [signal.classifier_needs_ocr for signal in analysis.signals] == [None, None]
    assert analysis.diagnostics == []

    plan = plan_route(analysis, registry.list_backends(), _constraints())
    assert [route.intent for route in plan.pages] == [Intent.NATIVE, Intent.NATIVE]
    assert stub.detect_calls == []  # the provider was never resolved or called


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _Stub:
    """Install the fake extension module plus a pinned distribution version."""
    instance = _Stub()
    monkeypatch.setitem(sys.modules, "pdf_inspector", instance)
    monkeypatch.setattr(provider, "_dist_version", lambda _name: _VERSION)
    return instance


# ── Live: the upstream 1-based contract, against the real wheel ─────────────────


def test_live_detect_pdf_reports_ir_based_pages(tmp_path: Path) -> None:
    """Ground truth for ADR-0004 A4: detect_pdf is 1-based, classify_pdf is not.

    Verified on an image-only first page: `detect_pdf` (via this provider) reports
    page 1, where `classify_pdf` reports page 0 for the same file. Skips when the
    `pdf-inspector` extra is not installed.
    """
    pytest.importorskip("pdf_inspector")
    classifier = provider.load_classifier(parse_classifier_spec(_SPEC))
    scanned = tmp_path / "mixed.pdf"
    scanned.write_bytes(mixed_scanned_pdf())
    facts = classifier.classify(SourceDocument(uri=scanned.absolute().as_uri(), media_type="application/pdf"))
    assert facts.pages_needing_ocr, "an image-only first page must need OCR"
    assert min(facts.pages_needing_ocr) >= 1
    assert 1 in facts.pages_needing_ocr
    assert facts.pdf_type in {"scanned", "image_based", "mixed"}

    digital = tmp_path / "digital.pdf"
    digital.write_bytes(_PDF_BYTES)
    text_facts = classifier.classify(SourceDocument(uri=digital.absolute().as_uri(), media_type="application/pdf"))
    assert text_facts.pages_needing_ocr == frozenset()  # no phantom OCR-need on digital text
    assert text_facts.pdf_type == "text_based"


def _registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    """Fresh registry with entry-point discovery stubbed out (offline, isolated)."""
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    return BackendRegistry()


def _register(registry: BackendRegistry, name: str) -> None:
    capabilities = BackendCapabilities(supported_formats=["application/pdf"], optional_dependency_group="pdf-inspector")
    registry.register(name, _StubFactory(BackendDescriptor(name=name, version="0.0.0", capabilities=capabilities)))


def _constraints(*, allow_ocr: bool = True) -> RoutingConstraints:
    return RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"pdf-inspector"},
        vram_budget_gb=8.0,
        max_passes=2,
        allow_ocr=allow_ocr,
    )
