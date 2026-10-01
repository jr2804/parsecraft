"""Tests for ``parsecraft convert`` (auto and non-auto paths)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner, Result

import parsecraft.cli.convert as convert_module
from parsecraft.backends import BackendRegistry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.cli.app import app
from parsecraft.environment import EnvironmentInfo
from parsecraft.ir import ChunkKind, PageResult, PageSignal, StructuredChunk
from parsecraft.pipeline.analysis import NoAnalyzerError, UnsupportedSourceError, choose_analyzer, media_type_for
from parsecraft.routing import Intent, RoutingJudge
from parsecraft.routing.classifier import (
    ClassifierSpec,
    OcrFacts,
    PageOcrClassifier,
    register_classifier_provider,
)
from parsecraft.routing.judge import JudgeSpec
from parsecraft.routing.judge_providers import register_judge_provider
from tests.fixtures.documents import minimal_pdf

_LONG_TEXT = "A paragraph comfortably longer than the forty character routing threshold."

runner = CliRunner()


class _StubBackend:
    def __init__(self, name: str, capabilities: BackendCapabilities, content: str) -> None:
        self.name = name
        self.capabilities = capabilities
        self._content = content

    def convert(self, request: ConversionRequest) -> BackendResult:
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.0.0"),
            pages=[
                PageResult(
                    page_number=1,
                    blocks=[
                        StructuredChunk(
                            id=f"{self.name}-1-0",
                            kind=ChunkKind.PARAGRAPH,
                            content=self._content,
                            page_number=1,
                            reading_order=0,
                        )
                    ],
                )
            ],
            elapsed_s=0.0,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        data = source.content or b""
        chars = len(data)
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=1,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=chars >= 40,
                    text_chars=chars,
                    image_count=0,
                    blank=chars == 0,
                )
            ],
        )


class _StubFactory:
    def __init__(self, descriptor: BackendDescriptor, *, content: str = "stub content", fail: bool = False) -> None:
        self.descriptor = descriptor
        self._content = content
        self._fail = fail

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        if self._fail:
            raise BackendError("stub factory failed")
        return _StubBackend(self.descriptor.name, self.descriptor.capabilities, self._content)


# ── --classifier / --judge spec flags ─────────────────────────────────────────


class _FlaggingClassifier:
    """Classifier stub: flags page 1 of every source (the augment-only direction)."""

    @staticmethod
    def classify(source: SourceDocument) -> OcrFacts:
        return OcrFacts(pages_needing_ocr=frozenset({1}), source="cli-fake")


class _ReversingJudge:
    """Judge stub: reverses the eligible candidate order (re-rank only)."""

    @staticmethod
    def rank(intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        return [descriptor.name for descriptor in reversed(candidates)]


def test_media_type_for_supported_and_unsupported(tmp_path: Path) -> None:
    assert media_type_for(tmp_path / "a.TXT") == "text/plain"
    assert media_type_for(tmp_path / "a.md") == "text/markdown"
    with pytest.raises(UnsupportedSourceError) as error:
        media_type_for(tmp_path / "a.xyz")
    assert "unsupported source" in str(error.value)


def test_read_source_reads_bytes_and_reports_missing(tmp_path: Path) -> None:
    path = _source_file(tmp_path)
    source = convert_module.read_source(path, "text/plain")
    assert source.media_type == "text/plain"
    assert source.content == path.read_bytes()
    assert source.uri.startswith("file:")

    with pytest.raises(convert_module.ConvertError, match="cannot read source"):
        convert_module.read_source(tmp_path / "missing.txt", "text/plain")


def test_analysis_backend_prefers_native_then_name_and_errors() -> None:
    descriptors = [
        _descriptor("ocr-ovis", ("text/plain",)),
        _descriptor("native-text", ("text/plain",)),
        _descriptor("native-html", ("text/html",)),
    ]
    assert choose_analyzer(descriptors, "text/plain").name == "native-text"
    with pytest.raises(NoAnalyzerError) as error:
        choose_analyzer(descriptors, "application/pdf")
    assert "no installed backend can analyze" in str(error.value)


def test_build_constraints_from_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset({"ocr-ovis"}), vram_budget_gb=8.0, offline=False),
    )
    derived = convert_module.build_constraints("application/pdf", max_passes=2, allow_ocr=None)
    assert derived.formats == {"application/pdf"}
    assert derived.installed_extras == {"ocr-ovis"}
    assert derived.vram_budget_gb == 8.0
    assert derived.offline is False
    assert derived.allow_ocr is True
    assert derived.max_passes == 2

    overridden = convert_module.build_constraints("text/plain", max_passes=1, allow_ocr=False)
    assert overridden.allow_ocr is False


def test_preferred_backend_judge_orders_and_rejects() -> None:
    candidates = [_descriptor("native-text", ("text/plain",)), _descriptor("other", ("text/plain",))]
    judge = convert_module.PreferredBackendJudge("other")
    assert list(judge.rank(convert_module.Intent.NATIVE, candidates)) == ["other", "native-text"]

    missing = convert_module.PreferredBackendJudge("nope")
    with pytest.raises(convert_module.JudgeViolationError):
        missing.rank(convert_module.Intent.NATIVE, candidates)


def test_convert_auto_native_markdown(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 0
    assert "<!-- page 1 -->" in result.output
    assert "converted paragraph" in result.output


def test_convert_auto_json_emits_ir(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["metadata"]["format"] == "text/plain"
    assert payload["metadata"]["page_count"] == 1
    assert payload["pages"][0]["blocks"][0]["content"] == "converted paragraph"


def test_convert_no_auto_requires_backend(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)))
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--no-auto"])
    assert result.exit_code == 2
    assert "--no-auto requires --backend" in _text(result)


def test_convert_unknown_suffix_is_usage_error(tmp_path: Path, registry: BackendRegistry) -> None:
    path = tmp_path / "doc.xyz"
    path.write_text("data", encoding="utf-8")
    result = runner.invoke(app, ["convert", str(path)])
    assert result.exit_code == 2
    assert "unsupported source" in _text(result)


def test_convert_missing_file_is_usage_error(tmp_path: Path, registry: BackendRegistry) -> None:
    result = runner.invoke(app, ["convert", str(tmp_path / "missing.txt")])
    assert result.exit_code == 2


def test_convert_backend_path_prefers_named_backend(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-html", ("text/plain",)), content="html stub")
    _register(registry, _descriptor("native-text", ("text/plain",)), content="text stub")
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--backend", "native-html"])
    assert result.exit_code == 0
    assert "html stub" in result.output


def test_convert_backend_not_eligible_is_usage_error(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)))
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--backend", "native-html"])
    assert result.exit_code == 2
    assert "not eligible" in _text(result)


def test_convert_without_eligible_backend_fails(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",), group="missing-extra"))
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "routing failed" in _text(result)


def test_convert_analysis_failure_fails(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)), fail=True)
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "analysis with 'native-text' failed" in _text(result)


def test_convert_missing_optional_dependency_is_classified(tmp_path: Path, registry: BackendRegistry) -> None:
    """DependencyUnavailableError keeps exit 1 with its own actionable message (pc-4u7.28)."""

    class _MissingExtraFactory:
        descriptor = _descriptor("native-text", ("text/plain",))

        @staticmethod
        def __call__(config: BackendConfig) -> DocumentBackend:
            raise DependencyUnavailableError("some.module", "some-extra")

    registry.register("native-text", _MissingExtraFactory())
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "optional dependency missing" in _text(result)
    assert "backend dependency 'some.module' is not installed" in _text(result)


def test_convert_image_prefers_installed_claimer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed claimer beats a name-order-earlier backend whose extra is missing (pc-4u7.27)."""
    registry = BackendRegistry()
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])  # isolate: no real backends
    _register(registry, _descriptor("aaa-liteparse", ("image/png",), group="liteparse"), content="missing extra")
    _register(registry, _descriptor("zzz-ocr", ("image/png",), group="ocr-ovis"), content="installed claimer")
    monkeypatch.setattr(convert_module, "default_registry", registry)
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset({"ocr-ovis"}), vram_budget_gb=8.0, offline=False),
    )
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = runner.invoke(app, ["convert", str(path)])
    assert result.exit_code == 0
    assert "installed claimer" in result.output
    assert "missing extra" not in result.output


def test_convert_unsupported_dependency_version_is_classified(tmp_path: Path, registry: BackendRegistry) -> None:
    """UnsupportedDependencyVersionError keeps exit 1 with its own actionable message (pc-4u7.37)."""

    class _WrongVersionFactory:
        descriptor = _descriptor("native-text", ("text/plain",))

        @staticmethod
        def __call__(config: BackendConfig) -> DocumentBackend:
            raise UnsupportedDependencyVersionError("transformers", "4.57.1", ">=5.17,<6")

    registry.register("native-text", _WrongVersionFactory())
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "unsupported dependency version" in _text(result)
    assert "transformers==4.57.1 does not satisfy the required range" in _text(result)


def test_convert_unclaimed_media_type_is_usage_error(tmp_path: Path, registry: BackendRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    # PNG is a supported suffix, but no backend here claims image/png:
    # the public NoAnalyzerError must map to exit 2 with the verbatim message.
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])  # isolate: no real backends
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    _register(registry, _descriptor("native-text", ("text/plain",)))
    result = runner.invoke(app, ["convert", str(path)])
    assert result.exit_code == 2
    assert "no installed backend can analyze image/png" in _text(result)


def test_convert_no_ocr_flag(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)))
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--no-ocr"])
    assert result.exit_code == 0


def test_convert_classifier_flag_folds_ocr_need_into_routing(pdf_registry: Path) -> None:
    register_classifier_provider("cli-fake-classifier", _load_flagging_classifier)
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "cli-fake-classifier/flag"])
    assert result.exit_code == 0
    assert "ocr content" in result.output
    assert "native content" not in result.output


def test_convert_without_classifier_flag_keeps_the_rule_table_route(pdf_registry: Path) -> None:
    register_classifier_provider("cli-fake-classifier", _load_flagging_classifier)
    result = runner.invoke(app, ["convert", str(pdf_registry)])
    assert result.exit_code == 0
    assert "native content" in result.output
    assert "ocr content" not in result.output


def _load_flagging_classifier(spec: ClassifierSpec) -> PageOcrClassifier:
    return _FlaggingClassifier()


def test_convert_classifier_spec_error_is_a_usage_error(pdf_registry: Path) -> None:
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "not-a-spec"])
    assert result.exit_code == 2
    assert "must look like" in _text(result)


def test_convert_unavailable_classifier_provider_is_a_runtime_error(pdf_registry: Path) -> None:
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "nowhere-thing/flag"])
    assert result.exit_code == 1
    assert "classifier unavailable" in _text(result)
    assert "call register_classifier_provider" in _text(result)


def test_convert_judge_flag_reorders_candidates(pdf_registry: Path) -> None:
    register_judge_provider("cli-fake-judge", _load_reversing_judge)
    result = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "cli-fake-judge/j"])
    assert result.exit_code == 0
    assert "ocr content" in result.output


def _load_reversing_judge(spec: JudgeSpec) -> RoutingJudge:
    return _ReversingJudge()


def test_convert_judge_error_specs_map_to_typed_exit_codes(pdf_registry: Path) -> None:
    bad_spec = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "not-a-spec"])
    assert bad_spec.exit_code == 2
    assert "must look like" in _text(bad_spec)

    unavailable = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "nowhere-thing/j"])
    assert unavailable.exit_code == 1
    assert "judge unavailable" in _text(unavailable)


def test_convert_backend_and_judge_are_mutually_exclusive(pdf_registry: Path) -> None:
    result = runner.invoke(app, ["convert", str(pdf_registry), "--backend", "native-pdf", "--judge", "cli-fake-judge/j"])
    assert result.exit_code == 2
    assert "mutually exclusive" in _text(result)


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


@pytest.fixture
def pdf_registry(registry: BackendRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PDF source plus a native and an OCR candidate claiming application/pdf.

    An OCR extra is reported as installed so ``allow_ocr`` derives ``True`` — the
    host shape in which an OCR route is actually reachable.
    """
    _register(registry, _descriptor("native-pdf", ("application/pdf",)), content="native content")
    _register(registry, _descriptor("ocr-stub", ("application/pdf",)), content="ocr content")
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset({"ocr-stub"}), vram_budget_gb=0.0, offline=False),
    )
    path = tmp_path / "doc.pdf"
    path.write_bytes(minimal_pdf([[_LONG_TEXT]]))
    return path


def test_render_json_and_markdown(tmp_path: Path, registry: BackendRegistry) -> None:
    _register(registry, _descriptor("native-text", ("text/plain",)), content="render me")
    document = convert_module.convert_source(_source_file(tmp_path))
    assert "<!-- page 1 -->" in convert_module.render(document, as_json=False)
    assert "render me" in convert_module.render(document, as_json=False)
    as_json = cast("dict[str, object]", json.loads(convert_module.render(document, as_json=True)))
    assert "pages" in as_json


def _descriptor(
    name: str,
    formats: tuple[str, ...],
    *,
    group: str | None = None,
    gpu: bool = False,
    vram: float | None = None,
    ranges: bool = False,
    multi: bool = False,
) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(
            supported_formats=list(formats),
            supports_page_ranges=ranges,
            supports_multi_page=multi,
            requires_gpu=gpu,
            estimated_vram_gb=vram,
            optional_dependency_group=group,
        ),
    )


def _register(registry: BackendRegistry, descriptor: BackendDescriptor, *, content: str = "stub content", fail: bool = False) -> None:
    registry.register(descriptor.name, _StubFactory(descriptor, content=content, fail=fail))


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    """An isolated registry plus a fixed offline environment probe."""
    instance = BackendRegistry()
    monkeypatch.setattr(convert_module, "default_registry", instance)
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset(), vram_budget_gb=0.0, offline=True),
    )
    return instance


def _source_file(tmp_path: Path, name: str = "doc.txt", text: str = _LONG_TEXT) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path
