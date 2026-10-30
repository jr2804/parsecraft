"""Plain-text native backend — dependency-free."""

from __future__ import annotations

from parsecraft.adapters.classification import (
    TEXT_KIND_CODE,
    TextClassificationError,
    TextClassifierSpecError,
    parse_ambiguous_text,
    resolve_text_classifier,
)
from parsecraft.backends.native._common import NATIVE_BACKEND_VERSION, NativeBackendBase, paragraph_chunks
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import Diagnostic, DiagnosticLevel, PageResult

BACKEND_NAME = "native-text"
SUPPORTED_FORMATS = ["text/plain"]


class TextBackend(NativeBackendBase):
    """Plain text: one PARAGRAPH chunk per blank-line-separated block."""

    name = BACKEND_NAME


class _TextFactory:
    """Light entry-point object: descriptor + build."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=False,
            supports_multi_page=False,
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return TextBackend(config, self.descriptor.capabilities, extract=extract_text_pages)


def extract_text_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """One page of paragraph chunks; classified only when a classifier resolves.

    Ruling (pc-9p6.3): stamp ``detected_kind``/``confidence`` if and only if
    :func:`resolve_text_classifier` returns an instance. Unconfigured runs
    take today's path (byte-identical). Resolution or parse failures fall
    back to today's path with an INFO diagnostic — ingestion is never
    blocked (epic item 7). A malformed spec (fixable by the operator) keeps
    a WARNING; an unavailable provider is our wiring gap, not the user's
    mistake, so it stays quiet at INFO. Classification only improves
    structure *within* plain text; routing by extension in ``media_type_for``
    is untouched.
    """
    text = data.decode("utf-8", errors="replace")
    try:
        classifier = resolve_text_classifier(None)
    except TextClassifierSpecError as exc:
        return [_bare_page(text, level=DiagnosticLevel.WARNING, message=f"text-kind classifier unusable ({exc}); plain fallback, no verdict stamped")]
    except TextClassificationError as exc:
        return [_bare_page(text, message=f"text-kind classifier unusable ({exc}); plain fallback, no verdict stamped")]
    if classifier is None:
        return [_bare_page(text)]
    try:
        return parse_ambiguous_text(text, source.uri, classifier=classifier).pages
    except Exception as exc:
        return [_bare_page(text, message=f"text-kind parse failed ({type(exc).__name__}: {exc}); plain fallback, no verdict stamped")]


def _bare_page(text: str, *, level: DiagnosticLevel = DiagnosticLevel.INFO, message: str | None = None) -> PageResult:
    """Today's output: paragraph chunks, no verdict key, ``confidence=None``."""
    page = PageResult(page_number=1, blocks=paragraph_chunks("native-text", 1, text))
    if message is not None:
        page.diagnostics.append(Diagnostic(level=level, code=TEXT_KIND_CODE, message=message))
    return page


factory: BackendFactory = _TextFactory()
