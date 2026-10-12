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
from parsecraft.assets import AssetError, AssetManager, AssetPin
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
    ModelAssetDescriptor,
    SourceDocument,
)
from parsecraft.cli.app import app
from parsecraft.environment import EnvironmentInfo
from parsecraft.ir import ChunkKind, PageResult, PageSignal, StructuredChunk
from parsecraft.pipeline.analysis import NoAnalyzerError, UnsupportedSourceError, choose_analyzer, media_type_for
from parsecraft.routing import Intent, MachineProfile, PageContext, RoutingJudge, RoutingPreference
from parsecraft.routing.classifier import (
    ClassifierSpec,
    OcrFacts,
    PageOcrClassifier,
    register_classifier_provider,
)
from parsecraft.routing.judge import DeterministicJudge, JudgeSpec
from parsecraft.routing.judge_providers import register_judge_provider
from tests.fixtures.documents import minimal_pdf

_LONG_TEXT = "A paragraph comfortably longer than the forty character routing threshold."

runner = CliRunner()


class _StubBackend:
    def __init__(
        self,
        name: str,
        capabilities: BackendCapabilities,
        content: str,
        *,
        pages: int = 1,
        convert_error: Exception | None = None,
        fail_on_page: int | None = None,
    ) -> None:
        self.name = name
        self.capabilities = capabilities
        self._content = content
        self._pages = pages
        self._convert_error = convert_error
        self._fail_on_page = fail_on_page

    def convert(self, request: ConversionRequest) -> BackendResult:
        numbers = self._requested_pages(request)
        if self._convert_error is not None:
            raise self._convert_error
        if self._fail_on_page is not None and self._fail_on_page in numbers:
            raise BackendError(f"stub cannot convert page {self._fail_on_page}")
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.0.0"),
            pages=[
                PageResult(
                    page_number=number,
                    blocks=[
                        StructuredChunk(
                            id=f"{self.name}-{number}-0",
                            kind=ChunkKind.PARAGRAPH,
                            content=self._content,
                            page_number=number,
                            reading_order=0,
                        )
                    ],
                )
                for number in numbers
            ],
            elapsed_s=0.0,
        )

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        data = source.content or b""
        chars = len(data)
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=self._pages,
            signals=[
                PageSignal(
                    page_number=page,
                    has_native_text=chars >= 40,
                    text_chars=chars,
                    image_count=0,
                    blank=chars == 0,
                )
                for page in range(1, self._pages + 1)
            ],
        )

    @staticmethod
    def _requested_pages(request: ConversionRequest) -> list[int]:
        page_range = request.page_range
        if page_range is None:
            return [1]
        return list(range(page_range.start, page_range.end + 1))


class _StubFactory:
    def __init__(
        self,
        descriptor: BackendDescriptor,
        *,
        content: str = "stub content",
        fail: bool = False,
        pages: int = 1,
        convert_error: Exception | None = None,
        fail_on_page: int | None = None,
    ) -> None:
        self.descriptor = descriptor
        self._content = content
        self._fail = fail
        self._pages = pages
        self._convert_error = convert_error
        self._fail_on_page = fail_on_page

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        if self._fail:
            raise BackendError("stub factory failed")
        return _StubBackend(
            self.descriptor.name,
            self.descriptor.capabilities,
            self._content,
            pages=self._pages,
            convert_error=self._convert_error,
            fail_on_page=self._fail_on_page,
        )


# ── --classifier / --judge spec flags ─────────────────────────────────────────


class _FlaggingClassifier:
    """Classifier stub: flags page 1 of every source (the augment-only direction)."""

    @staticmethod
    def classify(source: SourceDocument) -> OcrFacts:
        return OcrFacts(pages_needing_ocr=frozenset({1}), source="cli-fake")


class _ReversingJudge:
    """Judge stub: reverses the case within each flavor, native-capable first.

    Reordering must respect the NATIVE contract (an OCR backend may never lead a
    page the rules called native), so this reverses *inside* the two groups and
    keeps the groups themselves in order — the way a provider judge has to.
    """

    @staticmethod
    def rank(intent: Intent, candidates: Sequence[BackendDescriptor], context: PageContext | None = None) -> Sequence[str]:
        native = [descriptor.name for descriptor in candidates if not descriptor.name.startswith("ocr-")]
        ocr = [descriptor.name for descriptor in candidates if descriptor.name.startswith("ocr-")]
        return [*reversed(native), *reversed(ocr)]


class _OcrLeadingJudge:
    """Judge stub that promotes an OCR fallback over native candidates."""

    @staticmethod
    def rank(intent: Intent, candidates: Sequence[BackendDescriptor], context: PageContext | None = None) -> Sequence[str]:
        names = [descriptor.name for descriptor in candidates]
        ocr = next(name for name in names if name.startswith("ocr-"))
        return [ocr, *(name for name in names if name != ocr)]


class _ExplodingJudge:
    """Judge stub that fails the test if the planner ever consumes it."""

    @staticmethod
    def rank(intent: Intent, candidates: Sequence[BackendDescriptor], context: PageContext | None = None) -> Sequence[str]:
        raise AssertionError("no judge spec was given — the deterministic default must plan")


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


def test_convert_output_flag_writes_byte_identical_projection(tmp_path: Path, registry: BackendRegistry) -> None:
    """-o writes exactly what stdout would have received; absent -o is unchanged."""
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    source = _source_file(tmp_path)
    to_stdout = runner.invoke(app, ["convert", str(source)])
    assert to_stdout.exit_code == 0

    target = tmp_path / "out.md"
    target.write_text("stale bytes", encoding="utf-8")  # truncates like '>' redirection
    to_file = runner.invoke(app, ["convert", str(source), "-o", str(target)])
    assert to_file.exit_code == 0
    assert to_file.output == ""  # the sink switch moves ALL output, stdout stays empty
    assert target.read_text(encoding="utf-8") == to_stdout.output
    assert "stale bytes" not in target.read_text(encoding="utf-8")


def test_convert_output_json_composes(tmp_path: Path, registry: BackendRegistry) -> None:
    """-o composes with --json: the JSON DocumentResult lands in the file."""
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    target = tmp_path / "ir.json"
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--json", "-o", str(target)])
    assert result.exit_code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["metadata"]["page_count"] == 1


def test_convert_output_missing_directory_is_usage_error(tmp_path: Path, registry: BackendRegistry) -> None:
    """No auto-create: a missing parent dir is a fail-fast usage error (exit 2)."""
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    missing = tmp_path / "not-there"
    target = missing / "out.md"
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "-o", str(target)])
    assert result.exit_code == 2
    text = _text(result)
    assert str(missing) in text
    assert "create it first" in text
    assert not target.exists()  # nothing was converted or written


def test_convert_output_write_failure_is_typed_error(tmp_path: Path, registry: BackendRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real write failure is a typed exit-1 error, never a raw traceback."""
    _register(registry, _descriptor("native-text", ("text/plain",)), content="converted paragraph")
    source = _source_file(tmp_path)  # create BEFORE the patch — the source must stay writable
    target = tmp_path / "out.md"

    def _deny(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("disk says no")

    monkeypatch.setattr(Path, "write_text", _deny)
    result = runner.invoke(app, ["convert", str(source), "-o", str(target)])
    assert result.exit_code == 1
    text = _text(result)
    assert "cannot write" in text
    assert str(target) in text
    assert not target.exists()


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


def test_convert_offline_image_never_downloads(registry: BackendRegistry, tmp_path: Path) -> None:
    """Offline reaches create(): an image convert fails typed, naming the model, with zero downloader calls (pc-e38).

    The ``registry`` fixture declares an offline host, so the analyzer's factory
    must see ``options["offline"]`` and refuse the uncached download — the
    asset error names the model id and the downloader is never invoked.
    """

    class _FakeDownloader:
        """Records calls; stands in for the Hugging Face downloader."""

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []

        def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
            self.calls.append((model_id, revision, filename, dest_dir))
            return str(Path(dest_dir) / filename)

    downloader = _FakeDownloader()
    descriptor = _descriptor("ocr-image", ("image/png",))

    class _AssetFactory:
        """Factory that must acquire a model asset before it can convert."""

        def __init__(self, descriptor: BackendDescriptor) -> None:
            self.descriptor = descriptor

        def __call__(self, config: BackendConfig) -> DocumentBackend:
            manager = AssetManager(
                cache_dir=tmp_path / "cache",
                downloader=downloader,
                offline=bool(config.options.get("offline", False)),
            )
            manager.ensure(
                AssetPin(
                    descriptor=ModelAssetDescriptor(
                        model_id="ocr-model-x",
                        model_revision="v1",
                        model_license="MIT",
                        code_license="MIT",
                        asset_license="MIT",
                    ),
                    filenames=["weights.bin"],
                    expected_sha256={"weights.bin": "0" * 64},
                )
            )
            return _StubBackend(self.descriptor.name, self.descriptor.capabilities, "ocr content")

    registry.register("ocr-image", _AssetFactory(descriptor))
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = runner.invoke(app, ["convert", str(path)])
    assert result.exit_code == 1
    assert "ocr-model-x" in _text(result)
    assert downloader.calls == []


def test_convert_execution_asset_error_maps_to_convert_error(tmp_path: Path, registry: BackendRegistry) -> None:
    """The execution-path AssetError mapping (pc-e38): exit 1, model named.

    Analysis succeeds on a plain native stub; the plan's lead is a backend whose
    factory must acquire a model and refuses, so the executor's per-pass
    ``registry.create`` — not the analyzer's — is what surfaces the asset error.
    """

    class _AssetFactory:
        """Factory that must acquire a model asset before it can convert."""

        def __init__(self, descriptor: BackendDescriptor) -> None:
            self.descriptor = descriptor

        def __call__(self, config: BackendConfig) -> DocumentBackend:
            raise AssetError("ocr-demo", "r1", "local model dir not found: /nope")

    _register(registry, _descriptor("native-a", ("text/plain",)), content="converted paragraph")
    registry.register("native-b", _AssetFactory(_descriptor("native-b", ("text/plain",))))
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--backend", "native-b"])
    assert result.exit_code == 1
    assert "model assets unavailable" in _text(result)
    assert "ocr-demo" in _text(result)


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


def test_convert_projection_survives_a_codepage_stdout(tmp_path: Path, registry: BackendRegistry) -> None:
    """pc-edn: a glyph outside the platform codepage must not fail the conversion.

    A redirected Windows stdout defaults to cp1252, where U+25AA raised
    UnicodeEncodeError and lost the whole projection; the CLI boundary now forces
    UTF-8, so the character arrives intact. The runner's ``charset`` reproduces
    that stdout on every platform.
    """
    _register(registry, _descriptor("native-text", ("text/plain",)), content="bullet \u25aa and \u201equote\u201c")
    source = _source_file(tmp_path, text=f"{_LONG_TEXT}\n")

    result = CliRunner(charset="cp1252").invoke(app, ["convert", str(source)])

    assert result.exit_code == 0, result.output
    assert "bullet \u25aa and \u201equote\u201c".encode() in result.stdout_bytes


# ── unusable-GPU warning ─────────────────────────────────────────────────


def test_warn_unusable_gpu_explains_a_present_but_unusable_gpu(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_module, "cuda_runtime_note", lambda: "the installed torch build has no CUDA support")
    convert_module.warn_unusable_gpu(EnvironmentInfo(vram_budget_gb=8.0, gpu_usable=False))
    err = capsys.readouterr().err
    assert "8 GiB GPU detected but unusable" in err
    assert "no CUDA support" in err
    assert "GPU-only backends are excluded" in err


@pytest.mark.parametrize(
    ("vram", "usable"),
    [(0.0, False), (8.0, True)],  # no GPU at all, or a GPU that works: nothing to warn about
)
def test_warn_unusable_gpu_stays_silent(
    capsys: pytest.CaptureFixture[str],
    vram: float,
    usable: bool,
) -> None:
    convert_module.warn_unusable_gpu(EnvironmentInfo(vram_budget_gb=vram, gpu_usable=usable))
    assert capsys.readouterr().err == ""


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


def test_convert_classifier_spec_error_is_a_usage_error(pdf_registry: Path) -> None:
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "not-a-spec"])
    assert result.exit_code == 2
    assert "must look like" in _text(result)


def test_convert_unavailable_classifier_provider_is_a_runtime_error(pdf_registry: Path) -> None:
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "nowhere-thing/flag"])
    assert result.exit_code == 1
    assert "classifier unavailable" in _text(result)
    assert "call register_classifier_provider" in _text(result)


def test_convert_reports_two_unavailable_providers_in_one_diagnosis(pdf_registry: Path) -> None:
    """Both seams failing names both in one run — no second install round-trip."""
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "ghost-thing/flag", "--judge", "phantom-thing/j"])
    assert result.exit_code == 1
    lines = _unique_lines(result)
    header = lines.index("error: routing providers unavailable:")  # error-prefixed like every CLI failure
    assert lines[header + 1].startswith("  --classifier ghost-thing/flag: ")
    assert lines[header + 2].startswith("  --judge phantom-thing/j: ")
    assert len(lines) == header + 3  # the header plus one line per failure, nothing else
    assert "parsecraft.providers.ghost_thing" in lines[header + 1]  # the body names the module to install
    assert "parsecraft.providers.phantom_thing" in lines[header + 2]


def test_convert_single_provider_failure_keeps_the_single_line_shape(pdf_registry: Path) -> None:
    """One failing seam stays exactly the message it always was — no header, no bullets."""
    classifier_only = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "ghost-thing/flag"])
    assert classifier_only.exit_code == 1
    message = _single_message(classifier_only)
    assert message == f"error: classifier unavailable: {_single_body(classifier_only)}"
    assert "routing providers unavailable" not in _text(classifier_only)

    register_classifier_provider("cli-fake-classifier", _load_flagging_classifier)
    judge_only = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "cli-fake-classifier/flag", "--judge", "phantom-thing/j"])
    assert judge_only.exit_code == 1  # the classifier resolved; the judge is the only failure, so the shape is the old one
    assert _single_message(judge_only) == f"error: judge unavailable: {_single_body(judge_only)}"


def _load_flagging_classifier(spec: ClassifierSpec) -> PageOcrClassifier:
    return _FlaggingClassifier()


def test_convert_body_is_reused_verbatim_in_the_combined_diagnosis(pdf_registry: Path) -> None:
    """The bullets are the single-failure bodies, not a re-worded paraphrase."""
    single = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "ghost-thing/flag"])
    body = _single_body(single)
    combined = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "ghost-thing/flag", "--judge", "phantom-thing/j"])
    assert f"  --classifier ghost-thing/flag: {body}" in _text(combined)


def _single_body(result: Result) -> str:
    """The provider error body: the single message without its CLI and seam prefixes."""
    message = _single_message(result)
    for prefix in ("error: classifier unavailable: ", "error: judge unavailable: "):
        if message.startswith(prefix):
            return message.removeprefix(prefix)
    raise AssertionError(message)


def _single_message(result: Result) -> str:
    """The one ``error: …`` line of a single-failure run (asserts there is exactly one)."""
    messages = [line for line in _unique_lines(result) if line.startswith("error: ")]
    assert len(messages) == 1, messages
    return messages[0]


def _unique_lines(result: Result) -> list[str]:
    """CLI text lines, deduplicated: CliRunner can capture a stderr line in both streams."""
    return list(dict.fromkeys(_text(result).strip().splitlines()))


def test_convert_collects_a_spec_error_with_an_unavailable_provider(pdf_registry: Path) -> None:
    """A malformed spec is a caller error too: collect it, and the usage exit code wins."""
    result = runner.invoke(app, ["convert", str(pdf_registry), "--classifier", "BAD/provider", "--judge", "phantom-thing/j"])
    assert result.exit_code == 2  # a caller bug outranks environment state, as on the single-failure path
    text = _text(result)
    assert "error: routing providers unavailable:" in text
    assert "  --classifier BAD/provider: " in text
    assert "  --judge phantom-thing/j: " in text
    assert "parsecraft.providers.phantom_thing" in text  # the unavailability detail is not lost


def test_convert_judge_flag_reorders_candidates(pdf_registry: Path) -> None:
    """The resolved provider judge decides the lead — within the NATIVE contract."""
    register_judge_provider("cli-fake-judge", _load_reversing_judge)
    default = runner.invoke(app, ["convert", str(pdf_registry)])
    assert default.exit_code == 0
    assert "aaa native content" in default.output  # name order: aaa-native leads
    judged = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "cli-fake-judge/j"])
    assert judged.exit_code == 0
    assert "native content" in judged.output  # the judge's reversed native lead


def test_convert_rejects_a_judge_that_leads_a_native_page_with_ocr(pdf_registry: Path) -> None:
    """A judge crossing the native/OCR boundary is a usage error, not a plan."""
    register_judge_provider("cli-ocr-first-judge", _load_ocr_leading_judge)
    result = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "cli-ocr-first-judge/j"])
    assert result.exit_code == 2
    assert "may not lead a NATIVE page" in _text(result)


def test_convert_preference_reaches_the_resolved_judge(pdf_registry: Path) -> None:
    """The flag crosses into the seam: the loader is asked with the requested axis."""
    seen: list[RoutingPreference] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        seen.append(preference)
        return _ReversingJudge()

    register_judge_provider("cli-preference-judge", loader)
    result = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "cli-preference-judge/j", "--preference", "quality"])
    assert result.exit_code == 0
    assert seen == [RoutingPreference.QUALITY]


def test_convert_preference_defaults_to_balanced(pdf_registry: Path) -> None:
    seen: list[RoutingPreference] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        seen.append(preference)
        return _ReversingJudge()

    register_judge_provider("cli-preference-default-judge", loader)
    result = runner.invoke(app, ["convert", str(pdf_registry), "--judge", "cli-preference-default-judge/j"])
    assert result.exit_code == 0
    assert seen == [RoutingPreference.BALANCED]


def test_convert_rejects_an_unknown_preference(pdf_registry: Path) -> None:
    """A StrEnum option means the CLI validates the axis for free (typer usage error)."""
    result = runner.invoke(app, ["convert", str(pdf_registry), "--preference", "extreme"])
    assert result.exit_code == 2
    assert "extreme" in _text(result)


def test_convert_preference_quality_flips_the_lead_within_a_family(
    registry: BackendRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end with no judge: the planner's own judge honours the flag.

    Two OCR candidates with different declared size and an OCR-intent page (text
    under the native threshold), so the pair really is the family the flag
    orders — and the executed backend's own content shows who led.
    """
    _register(registry, _descriptor("ocr-ovis", ("text/plain",), group="ocr-ovis", gpu=True, vram=6.0), content="ovis output")
    _register(registry, _descriptor("ocr-unlimited", ("text/plain",), group="ocr-unlimited", gpu=True, vram=4.0), content="unlimited output")
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(
            installed_extras=frozenset({"ocr-ovis", "ocr-unlimited"}),
            vram_budget_gb=8.0,
            gpu_usable=True,
            offline=True,
        ),
    )
    source = str(_source_file(tmp_path, text="hi"))  # below NATIVE_MIN_TEXT_CHARS -> OCR intent
    balanced = runner.invoke(app, ["convert", source])
    quality = runner.invoke(app, ["convert", source, "--preference", "quality"])
    speed = runner.invoke(app, ["convert", source, "--preference", "speed"])
    assert balanced.exit_code == quality.exit_code == speed.exit_code == 0
    assert "unlimited output" in balanced.output  # smallest declared size first
    assert "unlimited output" in speed.output
    assert "ovis output" in quality.output  # largest declared size first


def _load_reversing_judge(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
    return _ReversingJudge()


def _load_ocr_leading_judge(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
    return _OcrLeadingJudge()


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


def test_convert_total_pass_failure_is_not_an_empty_success(registry: BackendRegistry, tmp_path: Path) -> None:
    """Every pass failing is exit 1 with a diagnosis — never an empty document and exit 0.

    Regression pin for the defect the POLQA manual exposed: a PDF whose converter
    extra is missing (analyzer present, converter absent) used to convert into
    empty pages and report success.
    """
    _register(
        registry,
        _descriptor("native-text", ("text/plain",)),
        convert_error=DependencyUnavailableError("parsecraft.backends.native.text_impl", "text"),
    )
    source = str(_source_file(tmp_path))
    result = runner.invoke(app, ["convert", source])
    assert result.exit_code == 1
    text = _text(result)
    assert "produced no content" in text
    assert "dependency_missing in native-text" in text
    assert "install the 'text' extra" in text  # the extra is named
    assert "stub content" not in text  # and no document is rendered
    assert runner.invoke(app, ["convert", source, "--json"]).exit_code == 1  # same verdict as JSON


def test_convert_reports_the_no_content_failure_on_stderr(registry: BackendRegistry, tmp_path: Path) -> None:
    """The diagnosis reaches the console once — stdout stays empty, nothing is rendered."""
    _register(
        registry,
        _descriptor("native-text", ("text/plain",)),
        convert_error=DependencyUnavailableError("parsecraft.backends.native.text_impl", "text"),
    )
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "produced no content" in (result.stderr or "")
    assert "<!-- page 1 -->" not in result.output  # nothing was rendered: no page markers


def test_convert_partial_page_failure_stays_a_success(registry: BackendRegistry, tmp_path: Path) -> None:
    """A document with content AND a failed page is degraded, not failed: exit 0.

    Pages dispatch alone here (the stub is range-incapable), so page 2 forms its
    own group, fails, and keeps its per-page warning — while the document stays a
    successful conversion with page 1's content.
    """
    _register(
        registry,
        _descriptor("native-text", ("text/plain",)),
        content="page content",
        pages=2,
        fail_on_page=2,
    )
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--json"])
    assert result.exit_code == 0
    payload = cast("dict[str, object]", json.loads(result.output))
    assert cast("dict[str, object]", payload["metadata"])["page_count"] == 2
    pages = cast("list[dict[str, object]]", payload["pages"])
    assert cast("list[dict[str, object]]", pages[0]["blocks"])  # page 1 converted
    assert [d["code"] for d in cast("list[dict[str, object]]", pages[0]["diagnostics"])] == []
    assert [d["code"] for d in cast("list[dict[str, object]]", pages[1]["diagnostics"])] == ["pipeline-all-passes-failed"]
    assert payload["quality"] == []  # no document-level failure verdict


def test_judge_seam_is_not_gated_by_the_offline_constraint(registry: BackendRegistry, tmp_path: Path) -> None:
    """`offline` excludes model-asset backends; naming a judge IS the network opt-in.

    The `registry` fixture declares an offline host, so a resolved judge here can
    only mean the constraint never governed the seam (ADR-0004 decision 3).
    """
    resolved: list[str] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        resolved.append(spec.model)
        return DeterministicJudge()

    register_judge_provider("cli-offline-judge", loader)
    _register(registry, _descriptor("native-text", ("text/plain",)), content="native content")
    assert convert_module.probe_environment().offline is True  # the fixture's premise
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path)), "--judge", "cli-offline-judge/j"])
    assert result.exit_code == 0
    assert resolved == ["j"]


def test_no_judge_spec_resolves_no_provider(registry: BackendRegistry, tmp_path: Path) -> None:
    """No spec: the deterministic default plans, so nothing reaches a provider."""
    resolved: list[str] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        resolved.append(spec.provider)
        return _ExplodingJudge()  # any use of it fails the test loudly

    register_judge_provider("cli-unused-judge", loader)
    _register(registry, _descriptor("native-text", ("text/plain",)), content="native content")
    result = runner.invoke(app, ["convert", str(_source_file(tmp_path))])
    assert result.exit_code == 0
    assert resolved == []


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


@pytest.fixture
def pdf_registry(registry: BackendRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PDF source plus two native candidates and one OCR candidate (all PDF).

    An OCR extra is reported as installed so ``allow_ocr`` derives ``True`` — the
    host shape in which an OCR route is reachable at all. Two native candidates
    let a judge demonstrate re-ranking without crossing the NATIVE contract.
    """
    _register(registry, _descriptor("aaa-native", ("application/pdf",)), content="aaa native content")
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
            gpu_requirement=1.0 if gpu else 0.0,
            estimated_vram_gb=vram,
            optional_dependency_group=group,
        ),
    )


def _register(
    registry: BackendRegistry,
    descriptor: BackendDescriptor,
    *,
    content: str = "stub content",
    fail: bool = False,
    pages: int = 1,
    convert_error: Exception | None = None,
    fail_on_page: int | None = None,
) -> None:
    registry.register(
        descriptor.name,
        _StubFactory(
            descriptor,
            content=content,
            fail=fail,
            pages=pages,
            convert_error=convert_error,
            fail_on_page=fail_on_page,
        ),
    )


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
