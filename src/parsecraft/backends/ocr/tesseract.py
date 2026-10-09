"""The tesseract OCR backend — the base-install OCR path (ADR-0008).

Unlike the rest of the OCR family this backend loads **no model and no Python
runtime**: it drives the ``tesseract`` OS binary through a subprocess. That is
the whole point of ADR-0008 decision 1 — a base install gets a working OCR path
from one OS package, and because the engine is a binary rather than a wheel, no
interpreter cell on the support matrix can fail to resolve it.

Consequences of being engine-driven rather than model-driven:

- there is no ``_<name>_impl`` split. There is nothing heavy to defer: discovery
  and the subprocess call are stdlib, so this module is light by construction
  rather than by convention.
- ``gpu_requirement`` is NOT_NEEDED and ``estimated_vram_gb`` is ``None`` — no
  weights, no accelerator. The backend is eligible on a CPU-only host through the
  existing ``is_hard_eligible`` path, with no new routing primitive.
- ``model_asset`` is ``None`` until the tessdata asset lands, so the trace names
  the engine rather than a model that does not exist.

**Discovery** follows the ADR-0005 pattern that
``backends/docling/libreoffice.py`` established, unchanged in shape:

1. **The operator's ``PARSECRAFT_TESSERACT``** — taken verbatim, never probed.
   A declared path is a decision, not a fact to second-guess.
2. **Windows**: a bounded scan of the standard install roots
   (``%ProgramFiles%`` / ``%ProgramFiles(x86)%`` → ``Tesseract-OCR/tesseract.exe``).
   Two roots, one non-recursive listing each, no drive-wide walk. This step is
   why discovery cannot live in the generic probe alone: the Tesseract installer
   offers to add itself to PATH and users decline, so PATH alone would report a
   perfectly working install as absent.
3. **Everywhere else**: PATH lookup only. Linux/macOS packaging owns the location;
   there is nothing to scan.

Detection, never estimation: the result is the operator's own string or a path
that exists on this host, never a predicted install location.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from subprocess import DEVNULL, TimeoutExpired, run

from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr import _common
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    TESSERACT_CAPABILITIES,
    TESSERACT_ENV,
    TESSERACT_LANGUAGES,
    TESSERACT_NAME,
)
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendDescriptor,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)

#: Windows environment variables holding the two Program Files roots.
_WINDOWS_ROOTS_ENV: tuple[str, ...] = ("ProgramFiles", "ProgramFiles(x86)")
#: Windows executable name (POSIX installs use the command below).
_WINDOWS_EXECUTABLE = "tesseract.exe"
_WINDOWS_DIRECTORY = "Tesseract-OCR"
#: The POSIX command name, and the engine name routing matches on.
_POSIX_COMMAND = "tesseract"

#: Automatic page segmentation: tesseract's own default (3 = fully automatic,
#: no OSD and no page layout analysis). Stated rather than inherited so a
#: behaviour change is visible in the diff.
_PAGE_SEGMENTATION_MODE = 3

#: Per-page subprocess ceiling. A 300 dpi page at the pixel cap is the slow end;
#: this is a backstop against a wedged engine, not the expected cost.
_ENGINE_TIMEOUT_S = 120.0

#: What to install when the engine is missing, per platform family. The message
#: names the package because the binary is an OS package — an operator cannot
#: fix this with pip, and saying so is the whole point of the typed error.
_INSTALL_HINTS: tuple[str, ...] = (
    "Debian/Ubuntu: apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu",
    "macOS: brew install tesseract",
    "Windows: choco install tesseract (or winget install UB-Mannheim.TesseractOCR)",
)


DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=TESSERACT_NAME,
    capabilities=TESSERACT_CAPABILITIES,
)


class TesseractUnavailableError(BackendError):
    """No tesseract installation could be located on this host."""


# ── backend ─────────────────────────────────────────────────────────────────


class TesseractBackend:
    """OCR through the ``tesseract`` binary — no weights, no accelerator."""

    name = TESSERACT_NAME
    capabilities = TESSERACT_CAPABILITIES

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Transcribe the requested window through the shared bounds machinery."""
        return _common.convert_pages(
            backend_name=self.name,
            backend_version=OCR_BACKEND_VERSION,
            asset=None,
            source=request.source,
            request=request,
            infer_page=transcribe_page,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Deterministic page signals; never runs the engine.

        A ``staticmethod`` like every other backend here: analysis reads no
        instance state, and nothing loads until a page is converted.
        """
        return _common.analyze_source(source)


class TesseractFactory:
    """Light factory. There is no impl module to defer to — the engine is a binary."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return TesseractBackend()


# ── engine invocation ───────────────────────────────────────────────────────


def transcribe_page(page_number: int, request: ConversionRequest) -> str:
    """One page of ``request.source`` through the engine, as plain text.

    Tesseract reads no PDF, so a PDF page goes through the shared raster surface
    first; an image source passes through untouched. The image is handed over as
    a temporary file rather than on stdin: a binary pipe is exactly where a
    Windows run gets subtly wrong, and a file is also inspectable when a page
    comes back empty.
    """
    image = _common.rasterize_page(request.source, page_number)
    command = resolve_tesseract_cmd()
    with tempfile.TemporaryDirectory(prefix="parsecraft-tesseract-") as work:
        page_file = Path(work) / "page.png"
        page_file.write_bytes(image)
        argv = [
            command,
            str(page_file),
            "stdout",
            "-l",
            "+".join(TESSERACT_LANGUAGES),
            "--psm",
            str(_PAGE_SEGMENTATION_MODE),
        ]
        try:
            completed = run(  # noqa: S603 - fixed argv, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=_ENGINE_TIMEOUT_S,
                check=False,
                stdin=DEVNULL,
            )
        except TimeoutExpired as exc:
            msg = f"the tesseract engine timed out after {_ENGINE_TIMEOUT_S:.0f}s on page {page_number}"
            raise BackendError(msg) from exc
        except OSError as exc:
            msg = f"could not run the tesseract engine at {command!r}: {exc}"
            raise BackendError(msg) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"exit code {completed.returncode}"
        msg = f"the tesseract engine failed on page {page_number}: {detail}"
        raise BackendError(msg)
    return completed.stdout


def resolve_tesseract_cmd() -> str:
    """Like :func:`find_tesseract_cmd`, but a typed failure naming the OS package."""
    found = find_tesseract_cmd()
    if found is None:
        hints = "; ".join(_INSTALL_HINTS)
        msg = f"the tesseract engine was not found on this host — install it ({hints}), or set {TESSERACT_ENV} to its path"
        raise TesseractUnavailableError(msg)
    return found


# ── discovery (ADR-0008 decision 2) ─────────────────────────────────────────


def find_tesseract_cmd() -> str | None:
    """The ``tesseract`` command for this host, or ``None`` when there is none.

    This is the single source of engine presence: the environment probe calls it
    through ``ENGINE_DISCOVERY`` so routing eligibility and the executable this
    backend actually runs can never disagree.
    """
    declared = os.environ.get(TESSERACT_ENV, "").strip()
    if declared:  # the operator's declaration wins and is used verbatim
        return declared
    if sys.platform == "win32":
        return _windows_scan()
    return _path_lookup()


def _windows_scan() -> str | None:
    """Bounded Program Files lookup for ``tesseract.exe`` (Windows only)."""
    for variable in _WINDOWS_ROOTS_ENV:
        root = os.environ.get(variable, "").strip()
        if not root:
            continue
        candidate = Path(root) / _WINDOWS_DIRECTORY / _WINDOWS_EXECUTABLE
        if candidate.is_file():
            return str(candidate)
    return None


def _path_lookup() -> str | None:
    """PATH-only lookup; packaging owns the location on POSIX hosts."""
    return shutil.which(_POSIX_COMMAND)


factory = TesseractFactory()
