"""Light factory for the pandoc backend (heavy work in ``_impl``).

Discovery imports this module, never ``pypandoc`` itself; the heavy impl
loads through :func:`importlib.import_module` at instantiation.

``supported_formats`` lists only inputs verified against a **real pandoc
3.11 binary** on 2026-09-28 (generate + round-trip, ``pandoc --version``):

- ``docx``, ``odt``, ``pptx``, ``rtf``, ``epub`` — full round-trips (pptx
  tables flatten to bullets, rtf/odt reorder blocks, epub wraps in section
  markup — all lossy-but-present, documented in ``backends/pandoc/AGENTS.md``);
- ``xlsx`` — read-only (pandoc has no xlsx writer); verified against a
  generated workbook (sharedStrings layout);
- ``ods``/``odp`` — **not supported by pandoc 3.11 and deliberately absent**
  from this list (input+output format lists checked).

Licence: the ``pypandoc`` wrapper is MIT; the pandoc **binary is
GPL-2.0-or-later** — optional extra + user's decision, ADR-0005.
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

#: Backend version (adapter code, not the pandoc release).
PANDOC_BACKEND_VERSION = "0.1.0"

#: Verified MIME inputs (pandoc 3.11, 2026-09-28) — see module docstring.
PANDOC_FORMATS: tuple[str, ...] = (
    "application/epub+zip",
    "application/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

#: MIME → pandoc reader name (only verified readers; see docstring).
PANDOC_READERS: dict[str, str] = {
    "application/epub+zip": "epub",
    "application/rtf": "rtf",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

_EXTRA = "pandoc"
_IMPL_MODULE = "parsecraft.backends.pandoc._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(PANDOC_FORMATS),
    # Pandoc sources are one logical page (no layout/pagination).
    supports_page_ranges=False,
    supports_multi_page=False,
    requires_gpu=False,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # system binary — no model weights to pin
)

DESCRIPTOR = BackendDescriptor(
    name="pandoc",
    version=PANDOC_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class PandocFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "pypandoc", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = PandocFactory()
