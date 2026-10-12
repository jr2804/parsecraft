"""Offline tests for the OCR-need classifier seam, fold, and rule OR-term."""

from __future__ import annotations

import subprocess
import sys
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import ValidationError

from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import Diagnostic, DiagnosticLevel, PageSignal
from parsecraft.pipeline.analysis import analyze_source, apply_classifier
from parsecraft.routing import Intent
from parsecraft.routing.classifier import (
    CLASSIFIER_PROVENANCE_CODE,
    DEFAULT_PROVIDER_MODULE,
    ClassifierError,
    ClassifierProviderLoadError,
    ClassifierProviderUnavailableError,
    ClassifierSpec,
    ClassifierSpecError,
    OcrFacts,
    PageOcrClassifier,
    parse_classifier_spec,
    register_classifier_provider,
    resolve_classifier,
)
from parsecraft.routing.rules import (
    FEATURE_FIGURES_CODE,
    FEATURE_TABLE_CODE,
    classify_page,
    extract_hints,
    page_needs_ocr,
)

PROVIDERS_MODULE = "parsecraft.routing.classifier"


class _StubClassifier:
    """Records the sources it saw and returns canned facts."""

    def __init__(self, facts: OcrFacts) -> None:
        self._facts = facts
        self.sources: list[SourceDocument] = []

    def classify(self, source: SourceDocument) -> OcrFacts:
        self.sources.append(source)
        return self._facts


class _RaisingClassifier:
    """Raises the given error from ``classify`` (seam-error vs provider-bug)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def classify(self, source: SourceDocument) -> OcrFacts:
        raise self._error


class _StubBackend:
    name = "native-text"
    capabilities = BackendCapabilities(supported_formats=["text/plain"])

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return make_analysis([make_signal(1)])

    @staticmethod
    def convert(request: ConversionRequest) -> BackendResult:
        raise NotImplementedError


class _StubFactory:
    descriptor = BackendDescriptor(name="native-text", capabilities=BackendCapabilities(supported_formats=["text/plain"]))

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return cast("DocumentBackend", _StubBackend())


# ── OcrFacts ───────────────────────────────────────────────────────────────


def test_ocr_facts_defaults_are_empty_and_unopinionated() -> None:
    empty = facts()
    assert empty.pages_needing_ocr == frozenset()
    assert empty.pages_with_tables == frozenset()
    assert empty.pdf_type is None
    assert empty.confidence is None


def test_ocr_facts_are_frozen() -> None:
    with pytest.raises(ValidationError):
        facts().source = "other"  # ty: ignore[invalid-assignment]


def test_ocr_facts_reject_non_ir_page_numbers() -> None:
    with pytest.raises(ValidationError, match="IR 1-based"):
        facts(pages_needing_ocr=frozenset({0}))
    with pytest.raises(ValidationError, match="IR 1-based"):
        facts(pages_with_tables=frozenset({0, 2}))


def test_ocr_facts_reject_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        facts(confidence=1.5)


# ── the seam Protocol ──────────────────────────────────────────────────────


def test_stub_satisfies_the_classifier_protocol() -> None:
    assert isinstance(_StubClassifier(facts()), PageOcrClassifier)


def test_core_never_imports_a_provider() -> None:
    """routing/ imports no provider module — the classifier is injectable, not baked in."""
    proc = subprocess.run(  # noqa: S603 — fixed interpreter, repo-local imports
        [
            sys.executable,
            "-c",
            "import sys; import parsecraft.routing; assert 'parsecraft.providers.pdfinspector' not in sys.modules; print('CORE_OK')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CORE_OK" in proc.stdout


# ── parse_classifier_spec ──────────────────────────────────────────────────


def test_parse_full_spec_with_variant() -> None:
    spec = parse_classifier_spec("pdfinspector/detect:fast")
    assert spec == ClassifierSpec(provider="pdfinspector", model="detect", variant="fast")


def test_parse_spec_without_variant_and_with_whitespace() -> None:
    assert parse_classifier_spec("pdfinspector/detect").variant is None
    assert parse_classifier_spec("  pdfinspector/detect \n").provider == "pdfinspector"


@pytest.mark.parametrize("bad_spec", ["nodash", "", "a/", "/b", "a/b:", "a.b/c", "A/B"])
def test_parse_rejects_malformed_specs(bad_spec: str) -> None:
    with pytest.raises(ClassifierSpecError):
        parse_classifier_spec(bad_spec)


def test_default_provider_module_path_convention() -> None:
    assert DEFAULT_PROVIDER_MODULE.format(provider="pdfinspector") == "parsecraft.providers.pdfinspector"


# ── resolve_classifier ─────────────────────────────────────────────────────


def test_resolve_none_returns_none() -> None:
    assert resolve_classifier(None) is None


def test_resolve_instance_passes_through() -> None:
    stub = _StubClassifier(facts())
    assert resolve_classifier(stub) is stub


def test_resolve_rejects_unsupported_type() -> None:
    with pytest.raises(ClassifierSpecError, match="unsupported classifier spec type"):
        resolve_classifier(cast("Any", object()))


def test_registered_loader_receives_typed_spec() -> None:
    captured: list[ClassifierSpec] = []

    def loader(spec: ClassifierSpec) -> PageOcrClassifier:
        captured.append(spec)
        return _StubClassifier(facts())

    register_classifier_provider("fakec", loader)
    classifier = resolve_classifier("fakec/model:v1")
    assert isinstance(classifier, _StubClassifier)
    assert captured == [ClassifierSpec(provider="fakec", model="model", variant="v1")]


def test_register_rejects_bad_name_and_loader() -> None:
    with pytest.raises(ClassifierSpecError, match="slash-free"):
        register_classifier_provider("bad/name", lambda spec: _StubClassifier(facts()))
    with pytest.raises(ClassifierSpecError, match="slash-free"):
        register_classifier_provider("", lambda spec: _StubClassifier(facts()))
    with pytest.raises(ClassifierError, match="must be callable"):
        register_classifier_provider("noloadc", cast("Any", "not-callable"))


def test_lazy_module_loads_on_first_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.lazyc")

    def load_classifier(spec: ClassifierSpec) -> PageOcrClassifier:
        assert spec.model == "m"
        return _StubClassifier(facts())

    module.load_classifier = load_classifier  # ty: ignore[unresolved-attribute] — ModuleType is dynamically extended
    monkeypatch.setitem(sys.modules, "parsecraft.providers.lazyc", module)
    assert isinstance(resolve_classifier("lazyc/m"), _StubClassifier)


def test_explicit_registration_beats_lazy_module_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.prefc")

    def module_loader(spec: ClassifierSpec) -> PageOcrClassifier:
        raise AssertionError("lazy module must not load when a loader is registered")

    module.load_classifier = module_loader  # ty: ignore[unresolved-attribute] — ModuleType is dynamically extended
    monkeypatch.setitem(sys.modules, "parsecraft.providers.prefc", module)
    register_classifier_provider("prefc", lambda spec: _StubClassifier(facts()))
    assert isinstance(resolve_classifier("prefc/m"), _StubClassifier)


def test_lazy_module_missing_names_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(f"{PROVIDERS_MODULE}.import_module", raiser)
    with pytest.raises(ClassifierProviderUnavailableError) as exc_info:
        resolve_classifier("ghost/m")
    assert exc_info.value.provider == "ghost"
    assert "parsecraft.providers.ghost" in exc_info.value.module_name
    assert "install the extra that ships it" in exc_info.value.hint
    assert "register_classifier_provider('ghost', loader)" in exc_info.value.hint


def test_lazy_module_without_loader_export(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.barec")
    monkeypatch.setitem(sys.modules, "parsecraft.providers.barec", module)
    with pytest.raises(ClassifierProviderUnavailableError, match="does not export load_classifier"):
        resolve_classifier("barec/m")


def test_loader_exception_is_wrapped() -> None:
    def broken(spec: ClassifierSpec) -> PageOcrClassifier:
        raise ValueError("upstream exploded")

    register_classifier_provider("brokenc", broken)
    with pytest.raises(ClassifierProviderLoadError, match="upstream exploded") as exc_info:
        resolve_classifier("brokenc/m")
    assert exc_info.value.provider == "brokenc"


def test_loader_classifier_error_passes_through() -> None:
    def raising(spec: ClassifierSpec) -> PageOcrClassifier:
        raise ClassifierSpecError("provider-side spec problem")

    register_classifier_provider("raiserc", raising)
    with pytest.raises(ClassifierSpecError, match="provider-side spec problem"):
        resolve_classifier("raiserc/m")


def test_loader_returning_non_classifier_is_rejected() -> None:
    register_classifier_provider("junkc", lambda spec: "not a classifier")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ClassifierProviderLoadError, match="not a PageOcrClassifier"):
        resolve_classifier("junkc/m")


# ── rules: the augment-only OR-term ────────────────────────────────────────


def test_page_needs_ocr_or_term_is_augment_only() -> None:
    assert page_needs_ocr(make_signal(classifier=True)) is True  # adds OCR-need
    assert page_needs_ocr(make_signal(classifier=None)) is False
    assert page_needs_ocr(make_signal(classifier=False)) is False  # False is never a verdict


def test_classifier_false_never_removes_text_statistics_ocr_need() -> None:
    assert page_needs_ocr(make_signal(native=False, classifier=False)) is True
    assert page_needs_ocr(make_signal(blank=True, classifier=False)) is True


# ── the analysis-boundary fold ─────────────────────────────────────────────


def test_fold_sets_true_only_for_flagged_pages() -> None:
    analysis = make_analysis([make_signal(1), make_signal(2), make_signal(3)])
    folded = apply_classifier(analysis, facts(pages_needing_ocr=frozenset({2})))
    assert [signal.classifier_needs_ocr for signal in folded.signals] == [None, True, None]


def test_fold_is_pure() -> None:
    analysis = make_analysis([make_signal(1)])
    before = analysis.model_dump()
    apply_classifier(analysis, facts(pages_needing_ocr=frozenset({1})))
    assert analysis.model_dump() == before


def test_fold_appends_provenance_and_preserves_existing_diagnostics() -> None:
    existing = Diagnostic(level=DiagnosticLevel.INFO, code="pdf-encrypted", message="kept")
    analysis = make_analysis([make_signal(1)], diagnostics=(existing,))
    folded = apply_classifier(analysis, facts(pdf_type="scanned", confidence=0.875))
    assert folded.diagnostics[0] == existing
    provenance = folded.diagnostics[1]
    assert provenance.code == CLASSIFIER_PROVENANCE_CODE
    assert provenance.level is DiagnosticLevel.INFO
    assert provenance.message == "classifier pdf-inspector 1.25.2; pdf_type=scanned; confidence=0.875"


def test_fold_provenance_is_minimal_without_optional_fields() -> None:
    folded = apply_classifier(make_analysis([make_signal(1)]), facts())
    assert folded.diagnostics[0].message == "classifier pdf-inspector 1.25.2"


def test_fold_emits_feature_tables_when_present() -> None:
    folded = apply_classifier(make_analysis([make_signal(1)]), facts(pages_with_tables=frozenset({3, 1})))
    codes = [diagnostic.code for diagnostic in folded.diagnostics]
    assert codes == [CLASSIFIER_PROVENANCE_CODE, FEATURE_TABLE_CODE]
    assert "pages 1, 3" in folded.diagnostics[1].message


def test_fold_never_synthesizes_feature_figures() -> None:
    folded = apply_classifier(make_analysis([make_signal(1)]), facts(pages_needing_ocr=frozenset({1}), pages_with_tables=frozenset({1})))
    assert FEATURE_FIGURES_CODE not in {diagnostic.code for diagnostic in folded.diagnostics}


def test_fold_ignores_pages_not_in_the_analysis() -> None:
    # A provider indexing bug cannot invent a page: unknown numbers are inert.
    folded = apply_classifier(make_analysis([make_signal(1)]), facts(pages_needing_ocr=frozenset({9})))
    assert folded.signals[0].classifier_needs_ocr is None


def test_fold_flag_reclassifies_page_via_the_rule_table() -> None:
    analysis = make_analysis([make_signal(1)])  # good native text → NATIVE
    folded = apply_classifier(analysis, facts(pages_needing_ocr=frozenset({1})))
    assert classify_page(folded.signals[0], 1, extract_hints(folded)) is Intent.OCR_GENERAL


# ── analyze_source threading ───────────────────────────────────────────────


def test_analyze_source_without_classifier_matches_backend_analysis(registry: BackendRegistry) -> None:
    source = _source()
    assert analyze_source(source, registry, media_type="text/plain") == _StubBackend().analyze(source)


def test_analyze_source_folds_classifier_facts(registry: BackendRegistry) -> None:
    source = _source()
    classifier = _StubClassifier(facts(pages_needing_ocr=frozenset({1}), pdf_type="mixed", confidence=0.9))
    result = analyze_source(source, registry, media_type="text/plain", classifier=classifier)
    assert result.signals[0].classifier_needs_ocr is True
    assert [diagnostic.code for diagnostic in result.diagnostics] == [CLASSIFIER_PROVENANCE_CODE]
    assert classifier.sources == [source]


def test_analyze_source_classifier_error_falls_back_to_rule_table(registry: BackendRegistry) -> None:
    source = _source()
    classifier = _RaisingClassifier(ClassifierError("classifier exploded"))
    result = analyze_source(source, registry, media_type="text/plain", classifier=classifier)
    assert result == _StubBackend().analyze(source)
    assert result.signals[0].classifier_needs_ocr is None


def test_analyze_source_does_not_swallow_provider_bugs(registry: BackendRegistry) -> None:
    classifier = _RaisingClassifier(ValueError("provider bug"))
    with pytest.raises(ValueError, match="provider bug"):
        analyze_source(_source(), registry, media_type="text/plain", classifier=classifier)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    registry = BackendRegistry()
    registry.register("native-text", _StubFactory())
    return registry


def _source() -> SourceDocument:
    return SourceDocument(uri="file:///doc.txt", media_type="text/plain", content=b"hi")


def facts(**overrides: Any) -> OcrFacts:
    base: dict[str, Any] = {"source": "pdf-inspector 1.25.2"}
    base.update(overrides)
    return OcrFacts(**base)


def make_signal(page: int = 1, *, native: bool = True, text_chars: int = 500, blank: bool = False, classifier: bool | None = None) -> PageSignal:
    return PageSignal(
        page_number=page,
        has_native_text=native,
        text_chars=text_chars,
        image_count=0,
        blank=blank,
        classifier_needs_ocr=classifier,
    )


def make_analysis(signals: list[PageSignal], diagnostics: tuple[Diagnostic, ...] = ()) -> AnalysisResult:
    return AnalysisResult(source_hash="0" * 64, page_count=len(signals), signals=signals, diagnostics=list(diagnostics))
