"""Plain-text native backend — dependency-free."""

from __future__ import annotations

import codecs

from parsecraft.adapters.classification import (
    TEXT_KIND_CODE,
    TextClassificationError,
    TextClassifierSpecError,
    parse_ambiguous_text,
    resolve_text_classifier,
)
from parsecraft.adapters.encoding import decode as decode_bytes
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

#: Diagnostic code for heuristic charset decisions (pc-9p6.14).
ENCODING_CODE = "text-encoding"

#: BOM prefix → exact codec, longest prefixes first (pc-9p6.14).
_BOM_CODECS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


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

    Decoding is deterministic-first (pc-9p6.14): a 16/32-bit BOM prefix
    decodes with the BOM-declared codec; BOM-less valid UTF-8 without NUL
    bytes takes today's exact path (a UTF-8 BOM stays preserved as U+FEFF,
    exactly as before). Everything else goes through charset-normalizer and
    ALWAYS carries a WARNING ``text-encoding`` diagnostic — a returned
    codec is a guess, never certainty, so the decode is announced rather
    than silent. A BOM-declared strict decode that fails falls through to
    the heuristic instead of being blindly approved as text.
    """
    text, encoding_note = _decode_source(data)
    try:
        classifier = resolve_text_classifier(None)
    except TextClassifierSpecError as exc:
        pages = [_bare_page(text, level=DiagnosticLevel.WARNING, message=f"text-kind classifier unusable ({exc}); plain fallback, no verdict stamped")]
    except TextClassificationError as exc:
        pages = [_bare_page(text, message=f"text-kind classifier unusable ({exc}); plain fallback, no verdict stamped")]
    else:
        if classifier is None:
            pages = [_bare_page(text)]
        else:
            try:
                pages = parse_ambiguous_text(text, source.uri, classifier=classifier).pages
            except Exception as exc:
                pages = [_bare_page(text, message=f"text-kind parse failed ({type(exc).__name__}: {exc}); plain fallback, no verdict stamped")]
    if encoding_note is not None:
        for page in pages:
            page.diagnostics.append(Diagnostic(level=DiagnosticLevel.WARNING, code=ENCODING_CODE, message=encoding_note))
    return pages


def _decode_source(data: bytes) -> tuple[str, str | None]:
    """Decode bytes, returning ``(text, warning or None)`` (pc-9p6.14)."""
    for bom, codec in _BOM_CODECS:
        if data.startswith(bom):
            try:
                text = data.decode(codec).removeprefix("\ufeff")
            except UnicodeDecodeError:
                break  # lying prefix: heuristic path, never blind approval
            return text, None
    if b"\x00" not in data:
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            pass  # heuristic path below; NUL bytes skip strict (UTF-16 is strict-valid)
    detected = decode_bytes(data)
    normalized = detected.charset.lower().replace("_", "").replace("-", "")
    if normalized in ("utf8", "utf8sig", "ascii"):
        # No better guess than UTF-8: decode strictly when possible so the note
        # never claims replacements that did not happen; NUL bytes stay explicit.
        try:
            exact = data.decode("utf-8")
        except UnicodeDecodeError:
            exact = None
        if exact is not None:
            return exact, "NUL bytes present; decoded as UTF-8 exactly with no replacements"
        note = "charset detection undecided for non-UTF-8 bytes; decoded with replacements (may be mojibake — verify the text if it looks wrong)"
    else:
        note = f"non-UTF-8 bytes decoded as {detected.charset}; charset detection is heuristic — verify the text if it looks wrong"
    return detected.text, note


def _bare_page(text: str, *, level: DiagnosticLevel = DiagnosticLevel.INFO, message: str | None = None) -> PageResult:
    """Today's output: paragraph chunks, no verdict key, ``confidence=None``."""
    page = PageResult(page_number=1, blocks=paragraph_chunks("native-text", 1, text))
    if message is not None:
        page.diagnostics.append(Diagnostic(level=level, code=TEXT_KIND_CODE, message=message))
    return page


factory: BackendFactory = _TextFactory()
