"""pdf-inspector-backed OCR-need classifier — a local structural text-layer scan.

Provider entry point for :mod:`parsecraft.routing.classifier`:
``load_classifier(spec)`` returns a :class:`PageOcrClassifier` backed by
pdf-inspector's ``detect_pdf`` (MIT, Rust/PyO3, prebuilt abi3 wheel).

Contract (ADR-0004 A4):

- **Local-only and model-free.** ``detect_pdf`` is a pure structural scan of
  the PDF text layer (tens of milliseconds): no network, no model downloads,
  and **no model loads** — the OCR runtime is never started. This provider
  therefore calls the detection path only, never ``process_pdf_with_ocr*`` or
  ``extract_pages_markdown*``. Sources come from memory or a local ``file://``
  path; nothing is fetched.
- **IR-1-based facts, normalized once.** ``detect_pdf`` reports
  ``pages_needing_ocr``/``pages_with_tables`` **1-indexed** — verified against
  pdf-inspector 1.25.2 two ways: the same file returned ``[1, 2]`` from
  ``detect_pdf`` and ``[0, 1]`` from ``classify_pdf``, and upstream
  ``lib.rs`` maps the classify path with ``p - 1`` while the detection path
  pushes ``page_0idx + 1``. Facts therefore pass through verbatim; nothing here
  or downstream converts a page number. ``classify_pdf`` is deliberately never
  used.
- **Failures are typed.** Every upstream failure (unreadable source, non-PDF
  bytes, unexpected runtime error) becomes a :class:`ClassifierError` — never
  ``None`` and never a silently empty fact set. The analysis boundary then
  falls back to the rule table (ADR-0004 A3).

Spec: ``pdfinspector/<mode>`` where the mode token names the upstream detection
call this provider makes — currently exactly ``detect_pdf``. The provider is
model-free, so a variant (``:something``) is rejected rather than ignored.
``pdfinspector`` is the provider token (hyphens are legal spec characters but
would not resolve to a module name).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import version as _dist_version
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from parsecraft.backends.protocol import SourceDocument
from parsecraft.backends.source import path_from_file_uri
from parsecraft.routing.classifier import (
    ClassifierError,
    ClassifierProviderLoadError,
    ClassifierProviderUnavailableError,
    ClassifierSpec,
    ClassifierSpecError,
    OcrFacts,
    PageOcrClassifier,
)

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "pdfinspector"
#: Upstream distribution name — the spec names ``pdf-inspector``, the import ``pdf_inspector``.
DISTRIBUTION_NAME = "pdf-inspector"
_MODULE_NAME = "pdf_inspector"
_EXTRA = "[project.optional-dependencies].pdf-inspector"

#: The one supported mode token: names pdf-inspector's detection call.
SUPPORTED_MODE = "detect_pdf"
#: Byte-input form of that call — in-memory sources need no temporary file.
#: Same detection scan, never the OCR entry points (see the module docstring).
_DETECT_BYTES = "detect_pdf_bytes"


class Detection(Protocol):
    """The pdf-inspector detection fields this provider reads (1-indexed pages)."""

    pdf_type: str
    confidence: float | None
    pages_needing_ocr: list[int]
    pages_with_tables: list[int]


@runtime_checkable
class _DetectorModule(Protocol):
    """Shape the provider needs from the ``pdf_inspector`` module."""

    def detect_pdf_bytes(self, data: bytes) -> Detection:
        """Detect pdf_type, confidence and per-page OCR-need from PDF bytes."""
        ...


class PdfInspectorClassifier:
    """OCR-need facts from pdf-inspector's structural detection scan."""

    def __init__(self, *, detect: Callable[[bytes], Detection], version: str) -> None:
        self._detect = detect
        self._version = version

    def classify(self, source: SourceDocument) -> OcrFacts:
        """IR-1-based OCR-need facts for ``source`` (local-only, no model loads)."""
        data = _source_bytes(source)
        try:
            detection = self._detect(data)
        except ValueError as exc:  # upstream's invalid-input surface (not a PDF, empty)
            msg = f"pdf-inspector cannot classify {source.uri!r}: {exc}"
            raise ClassifierError(msg) from exc
        except Exception as exc:  # library boundary — typed, never raw
            msg = f"pdf-inspector classification failed for {source.uri!r}: {type(exc).__name__}: {exc}"
            raise ClassifierError(msg) from exc
        return _facts(detection, version=self._version)


def load_classifier(spec: ClassifierSpec) -> PageOcrClassifier:
    """Provider entry point required by ``routing.classifier`` (pc-3's contract)."""
    if spec.model != SUPPORTED_MODE or spec.variant is not None:
        msg = f"pdfinspector supports only {PROVIDER_NAME}/{SUPPORTED_MODE} — got {_spec_text(spec)!r} (it is model-free, so no variant)"
        raise ClassifierSpecError(msg)
    try:
        module = import_module(_MODULE_NAME)
    except ImportError as exc:
        hint = f"{DISTRIBUTION_NAME!r} is not installed — install the {DISTRIBUTION_NAME!r} extra ({_EXTRA})"
        raise ClassifierProviderUnavailableError(PROVIDER_NAME, _MODULE_NAME, hint) from exc
    if not isinstance(module, _DetectorModule):
        raise ClassifierProviderLoadError(PROVIDER_NAME, f"{_MODULE_NAME} does not export {_DETECT_BYTES}(data)")
    return PdfInspectorClassifier(detect=module.detect_pdf_bytes, version=_dist_version(DISTRIBUTION_NAME))


def _facts(detection: Detection, *, version: str) -> OcrFacts:
    """Map detection fields onto IR-1-based facts (verbatim, no page arithmetic)."""
    try:
        return OcrFacts(
            pages_needing_ocr=frozenset(detection.pages_needing_ocr),
            pages_with_tables=frozenset(detection.pages_with_tables),
            pdf_type=detection.pdf_type,
            confidence=detection.confidence,
            source=f"{DISTRIBUTION_NAME} {version}",
        )
    except ValidationError as exc:
        # A 0-based or out-of-range upstream answer is a contract violation, not
        # a plan: the IR validator turns it into a typed failure, so the analysis
        # boundary degrades to the rule table instead of mis-routing pages.
        msg = f"pdf-inspector facts violate the IR contract (page numbers are 1-based by contract): {exc}"
        raise ClassifierError(msg) from exc


def _spec_text(spec: ClassifierSpec) -> str:
    """Render a parsed spec back to its ``provider/model[:variant]`` spelling."""
    variant = "" if spec.variant is None else f":{spec.variant}"
    return f"{spec.provider}/{spec.model}{variant}"


def _source_bytes(source: SourceDocument) -> bytes:
    """Local bytes of ``source``: in-memory content, else the ``file://`` path."""
    if source.content is not None:
        return source.content
    try:
        return path_from_file_uri(source.uri).read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri!r}: {exc}"
        raise ClassifierError(msg) from exc
