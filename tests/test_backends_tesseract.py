"""The tesseract backend: discovery shape, engine invocation, wiring.

Fully offline — the engine is never executed. Discovery is driven through a
synthetic host (a temporary tree plus the environment variables that point at
it), which is the only way to exercise the Windows install-root branch on a
POSIX runner and the PATH branch on a Windows one (memory #1127): each host
reaches only one of the two branches, so neither can be asserted by running on
the other.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr import tesseract
from parsecraft.backends.ocr._models import TESSERACT_CAPABILITIES, TESSERACT_ENV
from parsecraft.backends.ocr.tesseract import (
    TesseractBackend,
    TesseractUnavailableError,
    find_tesseract_cmd,
    resolve_tesseract_cmd,
    transcribe_page,
)
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument

_PNG = b"\x89PNG\r\n\x1a\nfake-png"


# ── engine invocation ───────────────────────────────────────────────────────


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "page text", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── discovery: declaration first, never probed ─────────────────────────────


def test_a_declaration_is_used_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared path is a decision, never a fact to second-guess (decision 2)."""
    _clear_declaration(monkeypatch)
    monkeypatch.setenv(TESSERACT_ENV, "/somewhere/else/my-tesseract")
    monkeypatch.setattr(tesseract, "_windows_scan", lambda: "C:/scanned/tesseract.exe")
    monkeypatch.setattr(tesseract, "_path_lookup", lambda: "/usr/bin/tesseract")

    assert find_tesseract_cmd() == "/somewhere/else/my-tesseract"


def test_a_declaration_is_not_probed_for_existence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The string is returned even when nothing is there — it is the operator's call."""
    _clear_declaration(monkeypatch)
    monkeypatch.setenv(TESSERACT_ENV, "/nonexistent/tesseract")
    monkeypatch.setattr(tesseract, "_path_lookup", lambda: "/usr/bin/tesseract")

    assert find_tesseract_cmd() == "/nonexistent/tesseract"


def test_posix_hosts_use_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_declaration(monkeypatch)
    monkeypatch.setattr(tesseract.sys, "platform", "linux")
    monkeypatch.setattr(tesseract.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)

    assert find_tesseract_cmd() == "/usr/bin/tesseract"


def test_windows_hosts_scan_the_standard_install_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The installer lets a user decline adding itself to PATH, so PATH is not enough."""
    _clear_declaration(monkeypatch)
    monkeypatch.setattr(tesseract.sys, "platform", "win32")
    root = tmp_path / "Program Files"
    (root / "Tesseract-OCR").mkdir(parents=True)
    executable = root / "Tesseract-OCR" / "tesseract.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(root))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(tesseract.shutil, "which", lambda name: None)  # not on PATH

    assert find_tesseract_cmd() == str(executable)


def test_windows_scan_skips_roots_without_an_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing standard root is not an error; the scan simply finds nothing."""
    _clear_declaration(monkeypatch)
    monkeypatch.setattr(tesseract.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "absent"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    assert find_tesseract_cmd() is None


def test_resolving_returns_the_discovered_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The success path is the same string discovery produced — never re-derived."""
    _clear_declaration(monkeypatch)
    monkeypatch.setattr(tesseract.sys, "platform", "linux")
    monkeypatch.setattr(tesseract.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)

    assert resolve_tesseract_cmd() == "/usr/bin/tesseract"


def test_the_typed_error_names_the_os_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine is an OS package: an operator cannot fix this with pip."""
    _clear_declaration(monkeypatch)
    monkeypatch.setattr(tesseract.sys, "platform", "linux")
    monkeypatch.setattr(tesseract.shutil, "which", lambda name: None)

    with pytest.raises(TesseractUnavailableError) as raised:
        resolve_tesseract_cmd()

    detail = str(raised.value)
    assert "apt install tesseract-ocr" in detail
    assert "brew install tesseract" in detail
    assert TESSERACT_ENV in detail


def _clear_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TESSERACT_ENV, raising=False)


def test_a_page_is_handed_to_the_engine_as_a_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file, not stdin: a binary pipe is where a Windows run goes wrong."""
    pages = _capture_run(monkeypatch, _Completed(stdout="recognized text"))

    text = transcribe_page(1, _request())

    assert text == "recognized text"
    assert pages == [_PNG]


def test_the_engine_is_asked_for_the_declared_languages_and_psm(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tesseract, "resolve_tesseract_cmd", lambda: "/usr/bin/tesseract")

    def fake_run(argv: list[str], **kwargs: Any) -> _Completed:  # noqa: ARG001
        calls.append(argv)
        Path(argv[1]).write_bytes(_PNG)
        return _Completed()

    monkeypatch.setattr(tesseract, "run", fake_run)
    transcribe_page(1, _request())

    argv = calls[0]
    assert argv[0] == "/usr/bin/tesseract"
    assert argv[2] == "stdout"  # text to stdout, no output file
    assert argv[argv.index("-l") + 1] == "eng+deu"
    assert argv[argv.index("--psm") + 1] == "3"


def test_a_pdf_page_is_rasterized_before_the_engine_sees_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tesseract reads no PDF, so the shared raster surface goes in between."""
    _capture_run(monkeypatch, _Completed())
    rasterized = b"\x89PNG\r\n\x1a\nrasterized-pdf-page"
    monkeypatch.setattr(tesseract._common, "rasterize_page", lambda source, number: rasterized)

    pages: list[bytes] = []
    monkeypatch.setattr(tesseract, "run", lambda argv, **kwargs: (pages.append(Path(argv[1]).read_bytes()), _Completed())[1])
    transcribe_page(2, _request(SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=b"%PDF-1.7")))

    assert pages == [rasterized]


def test_a_failing_engine_becomes_a_typed_error_naming_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run(monkeypatch, _Completed(returncode=1, stderr="Error in pixReadStream"))

    with pytest.raises(BackendError, match="failed on page 1: Error in pixReadStream"):
        transcribe_page(1, _request())


def test_an_engine_failure_without_stderr_still_reports_the_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run(monkeypatch, _Completed(returncode=2, stderr="   "))

    with pytest.raises(BackendError, match="exit code 2"):
        transcribe_page(1, _request())


def test_a_wedged_engine_is_bounded_by_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run(monkeypatch, subprocess.TimeoutExpired(cmd="tesseract", timeout=120))

    with pytest.raises(BackendError, match="timed out"):
        transcribe_page(1, _request())


def test_a_missing_engine_binary_is_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run(monkeypatch, OSError("permission denied"))

    with pytest.raises(BackendError, match="could not run the tesseract engine"):
        transcribe_page(1, _request())


# ── wiring ──────────────────────────────────────────────────────────────────


def test_the_descriptor_states_a_base_install_engine_backend() -> None:
    """No extra, no weights, no GPU — that is what makes it a base backend."""
    capabilities = TESSERACT_CAPABILITIES

    assert capabilities.optional_dependency_group is None
    assert capabilities.model_asset is None
    assert capabilities.gpu_requirement == 0.0
    assert capabilities.estimated_vram_gb is None
    assert capabilities.required_engine == "tesseract"
    assert "application/pdf" in capabilities.supported_formats
    assert "image/tiff" in capabilities.supported_formats


def test_the_factory_needs_no_heavy_module() -> None:
    """There is no ``_impl`` split: the engine is a binary, so nothing defers."""
    backend = tesseract.factory(BackendConfig(name="ocr-tesseract"))

    assert isinstance(backend, TesseractBackend)
    assert backend.name == "ocr-tesseract"


def test_analysis_never_runs_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("analyze must not touch the engine")

    monkeypatch.setattr(tesseract, "run", explode)
    analysis = TesseractBackend().analyze(_image_source())

    assert analysis.page_count == 1
    assert analysis.signals[0].has_native_text is False


def test_conversion_carries_the_engine_as_its_only_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """No weights means no model to attribute a page to — the trace says so."""
    _capture_run(monkeypatch, _Completed(stdout="hello"))

    result = TesseractBackend().convert(_request())

    assert result.backend.name == "ocr-tesseract"
    assert result.backend.model_id is None
    assert result.backend.model_revision is None
    assert result.pages[0].blocks[0].content == "hello"


def _request(source: SourceDocument | None = None) -> ConversionRequest:
    return ConversionRequest(source=source if source is not None else _image_source())


def _image_source() -> SourceDocument:
    return SourceDocument(uri="file:///page.png", media_type="image/png", content=_PNG)


def _capture_run(monkeypatch: pytest.MonkeyPatch, result: _Completed | Exception) -> list[bytes]:
    """Record every argv; return the ``page.png`` bytes each call was handed."""
    calls: list[list[str]] = []
    pages: list[bytes] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _Completed:  # noqa: ARG001 - timeout/stdin asserted elsewhere
        calls.append(argv)
        pages.append(Path(argv[1]).read_bytes())
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(tesseract, "run", fake_run)
    monkeypatch.setattr(tesseract, "resolve_tesseract_cmd", lambda: "/usr/bin/tesseract")
    return pages
