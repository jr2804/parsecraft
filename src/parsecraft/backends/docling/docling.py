"""Light factory for the Docling backend (heavy work in ``_impl``).

Discovery imports this module, never ``docling_rs`` itself; the heavy impl loads
through :func:`importlib.import_module` at instantiation.

``supported_formats`` lists only inputs **conversion-verified** against the
real engine, now docling.rs (PDF, HTML and DOCX re-verified 2026-10-31
through the real ``docling_rs`` package: a 9-page Word-generated report reads
10 433 chars and 11 headings through this backend's own CLI path, same as
under the Python engine). Markdown and plain text were verified under the
Python engine, but that claim is WITHDRAWN as of 2026-10-30: a 9 KiB
pipe-heavy Markdown file spins the marko table matcher with no timeout (CPU
spin over an hour, isolated repro via plain ``DocumentConverter``); the same
marko path serves plain text, so both types route to the dependency-free
native family instead (``native-markdown`` / ``native-text``). The guard is
kept unchanged until it is retested against the Rust engine. The library also
registers pptx/xlsx/odt/ods/odp/rtf/xls and
image
formats, but those stay deliberately undeclared until they are verified the same
way - the same contract LiteParse follows.

DOCX arrived here because a consumer's Word sources had only two candidates, both
broken for them: pandoc converts Word but drops every table (0 tables in that same
9-page guide), and PyMuPDF's office importer ignores the page-range contract (it
returned all 9 pages for a one-page request). docling.rs reads Word natively in Rust (no LibreOffice; verified with a
garbage ``DOCLING_LIBREOFFICE_CMD``), and the
consumer already installs ``parsecraft[docling]``.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)

#: Backend version (adapter code, not the upstream library).
DOCLING_BACKEND_VERSION = "0.2.0"

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: Conversion-verified MIME inputs (PDF/HTML/DOCX: docling.rs 2.55.4,
#: 2026-10-31 through the real package - see the module docstring).
#: Markdown and plain text
#: are deliberately ABSENT (withdrawn 2026-10-30): both reach docling's marko
#: table matcher, which spins without timeout on pipe-heavy content, so they
#: route to ``native-markdown`` / ``native-text`` instead.
DOCLING_FORMATS: tuple[str, ...] = (
    "application/pdf",
    "text/html",
    _DOCX,
)

_EXTRA = "docling"
_IMPL_MODULE = "parsecraft.backends.docling._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(DOCLING_FORMATS),
    supports_page_ranges=True,
    supports_multi_page=True,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    convert_extra=_EXTRA,
    # No bundled asset: the ONNX layout models are a manual one-time fetch via
    # docling_rs.download_models() (about 1.4 GB measured) — never shipped with
    # the package and never downloaded implicitly by this backend.
    model_asset=None,
)

DESCRIPTOR = BackendDescriptor(
    name="docling",
    version=DOCLING_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module (OCR-style seam)."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class DoclingFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "docling", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = DoclingFactory()
