"""The tesseract backend: discovery shape, engine invocation, wiring.

Fully offline — the engine is never executed. Discovery is driven through a
synthetic host (a temporary tree plus the environment variables that point at
it), which is the only way to exercise the Windows install-root branch on a
POSIX runner and the PATH branch on a Windows one (memory #1127): each host
reaches only one of the two branches, so neither can be asserted by running on
the other.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from parsecraft.assets import AssetManager, AssetPin, downloader_for, sha256_of
from parsecraft.assets.errors import OfflineModeError
from parsecraft.assets.github import GitHubDownloader
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.ocr import tesseract
from parsecraft.backends.ocr._models import TESSDATA_FILES, TESSERACT_ASSET, TESSERACT_CAPABILITIES, TESSERACT_ENV, TESSERACT_LANGUAGES
from parsecraft.backends.ocr.tesseract import (
    TesseractBackend,
    TesseractUnavailableError,
    find_tesseract_cmd,
    resolve_tessdata_dir,
    resolve_tesseract_cmd,
    transcribe_page,
)
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, ModelSource, SourceDocument

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


def test_the_missing_engine_is_a_dependency_failure_not_a_generic_backend_error() -> None:
    """The failure CODE is the point: DEPENDENCY_MISSING, not BACKEND_ERROR.

    ``executor._exception_failure`` maps ``DependencyUnavailableError`` to
    ``DEPENDENCY_MISSING``. A plain ``BackendError`` here would report a generic
    backend failure and send the operator looking at the backend instead of at
    the missing OS package the message names.
    """
    assert issubclass(TesseractUnavailableError, DependencyUnavailableError)


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
    analysis = TesseractBackend(_config()).analyze(_image_source())

    assert analysis.page_count == 1
    assert analysis.signals[0].has_native_text is False


def test_conversion_carries_the_engine_as_its_only_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """No weights means no model to attribute a page to — the trace says so."""
    _capture_run(monkeypatch, _Completed(stdout="hello"))

    result = TesseractBackend(_config()).convert(_request())

    assert result.backend.name == "ocr-tesseract"
    assert result.backend.model_id is None
    assert result.backend.model_revision is None
    assert result.pages[0].blocks[0].content == "hello"


# ── tessdata resolution (ADR-0008 decision 3) ───────────────────────────────


def test_complete_system_tessdata_wins_and_nothing_is_fetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An OS lang pack means zero downloads (decision 3).

    The directory deliberately holds ONLY the requested languages: ``equ`` and
    ``osd`` are in the PULLED set, and requiring them here would defeat the
    purpose since no OS package ships ``equ``.
    """
    _isolate_system_tessdata(monkeypatch, tmp_path)
    directory = _system_tessdata(tmp_path, TESSERACT_LANGUAGES)
    monkeypatch.setenv("TESSDATA_PREFIX", str(directory))
    fetched: list[object] = []
    monkeypatch.setattr(tesseract._common, "ensure_assets", lambda d, c: fetched.append(d))

    assert resolve_tessdata_dir(_config()) == str(directory)
    assert fetched == []


def test_an_incomplete_system_tessdata_falls_through_to_the_managed_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_system_tessdata(monkeypatch, tmp_path)
    directory = _system_tessdata(tmp_path, ("eng",))  # deu missing
    monkeypatch.setenv("TESSDATA_PREFIX", str(directory))
    fetched: list[object] = []
    monkeypatch.setattr(tesseract._common, "ensure_assets", lambda d, c: fetched.append(d) or "managed/tessdata")

    assert resolve_tessdata_dir(_config()) == "managed/tessdata"
    assert len(fetched) == 1


def test_an_incomplete_declaration_still_falls_through_to_the_packaging_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A declaration is consulted alone, but an incomplete one is not the last word."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(_system_tessdata(tmp_path, ("eng",))))
    # ``pkg/5/tessdata`` mirrors the real packaging layout the glob expects.
    packaged = _system_tessdata(tmp_path / "pkg" / "5", TESSERACT_LANGUAGES)
    monkeypatch.setattr(tesseract, "_POSIX_TESSDATA_ROOTS", (f"{tmp_path.as_posix()}/pkg/*/tessdata",))
    monkeypatch.setattr(tesseract._common, "ensure_assets", lambda d, c: "managed/tessdata")
    assert resolve_tessdata_dir(_config()) == str(packaged)


def _system_tessdata(root: Path, languages: tuple[str, ...]) -> Path:
    directory = root / "tessdata"
    directory.mkdir(parents=True)
    for language in languages:
        (directory / f"{language}.traineddata").write_bytes(b"stub-model")
    return directory


def test_no_system_tessdata_uses_the_managed_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_system_tessdata(monkeypatch, tmp_path)
    monkeypatch.setattr(tesseract._common, "ensure_assets", lambda d, c: "managed/tessdata")

    assert resolve_tessdata_dir(_config()) == "managed/tessdata"


def test_an_offline_run_refuses_before_attempting_a_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The real code path: OfflineModeError, raised before any transfer (pc-eoa).

    Nothing is created — ``ensure`` checks offline before it makes a directory —
    so this leaves no trace in the cache.
    """
    _isolate_system_tessdata(monkeypatch, tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setattr("parsecraft.assets.manager.default_cache_dir", lambda: cache)

    with pytest.raises(OfflineModeError):
        resolve_tessdata_dir(_config(offline=True))

    assert not cache.exists()


def _config(**options: str | int | float | bool) -> BackendConfig:
    return BackendConfig(name="ocr-tesseract", options=dict(options))


@pytest.fixture(autouse=True)
def _no_network_tessdata(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this module may reach the network or the real managed cache.

    Found the hard way: this module's docstring claims the suite is offline, but
    calling ``convert()`` resolves tessdata for real. On a host with no system
    tessdata that means ``ensure_assets`` downloads ~17.6 MiB from GitHub into
    the USER's cache — which one test did, silently, on every gate run.

    Two doors are closed. ``resolve_tessdata_dir`` is stubbed so ``convert()``
    stays hermetic, and the GitHub download primitive itself raises, so any
    FUTURE unpatched path fails the test loudly instead of quietly fetching.
    The tests that exercise tessdata resolution call the function through their
    own module-level name, which still binds the original — so they are
    unaffected.

    The ``network`` tier is exempt, and deliberately so: those tests exist
    precisely to reach the real upstream, and guarding them here would make the
    opt-in tier impossible to write in this module.
    """
    if "network" in request.keywords:
        return

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test reached the network for tessdata — mark it 'network' and run it under --run-downloads, or stub the call")

    monkeypatch.setattr(tesseract, "resolve_tessdata_dir", lambda config: None)
    monkeypatch.setattr(GitHubDownloader, "download", staticmethod(_forbidden))


def _isolate_system_tessdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove both system-tessdata sources so resolution is deterministic.

    The packaging glob would otherwise find a REAL install on a CI runner that
    has one, which is exactly the kind of host-dependent assertion memory #1127
    warns about.
    """
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(tesseract, "_POSIX_TESSDATA_ROOTS", (str(tmp_path / "no-such-tesseract" / "*" / "tessdata"),))


def test_the_resolved_tessdata_dir_reaches_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tesseract, "resolve_tesseract_cmd", lambda: "/usr/bin/tesseract")

    def fake_run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        Path(argv[1]).write_bytes(_PNG)
        return _Completed()

    monkeypatch.setattr(tesseract, "run", fake_run)
    transcribe_page(1, _request(), "/managed/tessdata")

    argv = calls[0]
    assert argv[argv.index("--tessdata-dir") + 1] == "/managed/tessdata"


def test_no_tessdata_argument_when_nothing_was_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence of a resolved dir means the engine's own default stands."""
    calls: list[list[str]] = []
    monkeypatch.setattr(tesseract, "resolve_tesseract_cmd", lambda: "/usr/bin/tesseract")

    def fake_run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        Path(argv[1]).write_bytes(_PNG)
        return _Completed()

    monkeypatch.setattr(tesseract, "run", fake_run)
    transcribe_page(1, _request())

    assert "--tessdata-dir" not in calls[0]


def test_the_tessdata_pins_are_real_content_hashes() -> None:
    """A pinned revision must be immutable, and the sizes must agree with the pins."""
    asset = TESSERACT_ASSET

    assert asset.model_source is ModelSource.GITHUB
    assert re.fullmatch(r"[0-9a-f]{40}", asset.model_revision), "revision must be a commit, not a branch"
    assert asset.model_revision != "main"
    assert {pin.path for pin in asset.file_pins} == {f"{name}.traineddata" for name in TESSDATA_FILES}
    assert all(re.fullmatch(r"[0-9a-f]{64}", pin.sha256) for pin in asset.file_pins)
    assert all(pin.size is not None and pin.size > 0 for pin in asset.file_pins)
    assert sum(pin.size or 0 for pin in asset.file_pins) == asset.size_bytes


def test_the_capability_record_declares_no_asset() -> None:
    """Decision: the asset is fetched at convert time, so routing never excludes it."""
    assert TESSERACT_CAPABILITIES.model_asset is None


# ── network tier (opt-in: -m network --run-downloads) ────────────────────────


@pytest.mark.network
def test_the_pinned_tessdata_still_downloads_from_the_real_upstream(tmp_path: Path) -> None:
    """The one thing the offline suite cannot prove: that the pin still resolves.

    Every other test here stubs the network, so nothing would catch a changed URL
    template, an upstream rename, or a regression in the per-segment encoding —
    the downloader would keep "succeeding" against nothing and the suite would
    stay green. That is not hypothetical: an offline gate is exactly what let a
    17.6 MiB silent fetch through for a whole arc.

    So this fetches the smallest pinned file (~3.9 MB, not the full 17.6 MB set)
    through the REAL downloader and asserts the bytes against the pin we ship.
    ``tmp_path`` keeps it out of the user's managed cache — which matters,
    because the model cache root has no environment override at all.

    Opt-in via the ``network`` marker: the default gate must never touch the
    network. Run with ``mise run test-network``.
    """
    pin = next(p for p in TESSERACT_ASSET.file_pins if p.path == "eng.traineddata")
    single = TESSERACT_ASSET.model_copy(update={"file_pins": (pin,), "size_bytes": pin.size})

    manager = AssetManager(cache_dir=tmp_path, downloader=downloader_for(single.model_source))
    paths = manager.ensure(AssetPin(descriptor=single, filenames=[pin.path], expected_sha256={pin.path: pin.sha256}))

    fetched = Path(paths[0])
    assert fetched.is_file()
    assert fetched.stat().st_size == pin.size
    assert sha256_of(fetched) == pin.sha256


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
