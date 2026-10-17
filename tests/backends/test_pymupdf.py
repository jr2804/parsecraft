"""Offline tests for the pymupdf backend (pypdf analysis, PyMuPDF extraction — ADR-0003)."""

from __future__ import annotations

import importlib
import io
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from types import ModuleType
from typing import Any, cast

import pytest

from parsecraft.backends import pymupdf as pymupdf_module
from parsecraft.backends.protocol import (
    BackendConfig,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.pymupdf._analyze import inspect as inspect_impl  # noqa: F401 — re-exported for tests
from parsecraft.backends.pymupdf.code_layout import PageText, page_text
from parsecraft.ir.models import FailureCode, PassKind
from tests.fixtures.documents import (
    DOCX_TEXTS,
    PPTX_TEXTS,
    document_bytes,
    docx_bytes,
    image_heavy_pdf,
    mixed_scanned_pdf,
    pptx_bytes,
)

#: Office media types as the backend declares them (kept in the test so a typo
#: in either place fails here).
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_PYMUPDF_CONVERT_MODULE = "parsecraft.backends.pymupdf._convert"


# ── pdf_inspect coverage (fake pypdf; real pypdf is a core dependency) ──────


class _FakePasswordType:
    NOT_DECRYPTED = 0
    USER_PASSWORD = 1


class _FakePdfPage:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        return self._text

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """pypdf-grade resource lookup: these synthetic pages carry no image XObjects."""
        return {} if key == "/Resources" else default


class _FakePdfReader:
    def __init__(self, *, encrypted: bool, decrypt_result: int, texts: list[str | None]) -> None:
        self.is_encrypted = encrypted
        self._decrypt_result = decrypt_result
        self.pages = [_FakePdfPage(text) for text in texts]

    def decrypt(self, password: str) -> int:
        return self._decrypt_result


# ── descriptor + analyze ──────────────────────────────────────────────────


def test_pymupdf_descriptor_contract() -> None:
    descriptor = pymupdf_module.factory.descriptor
    assert descriptor.name == "pymupdf"
    assert descriptor.capabilities.supported_formats == [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ]
    assert descriptor.capabilities.supports_page_ranges is True
    assert descriptor.capabilities.supports_multi_page is True
    assert descriptor.capabilities.optional_dependency_group is None


def test_pymupdf_factory_and_analyze_with_real_pypdf() -> None:
    backend = pymupdf_module.factory(_config("pymupdf"))
    data = document_bytes("pdf", pages=[["page one text"], ["page two text", "more"]])
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 2
    assert [s.page_number for s in analysis.signals] == [1, 2]
    assert all(s.text_chars > 0 and not s.blank for s in analysis.signals)
    assert analysis.diagnostics == []


def test_pymupdf_analyze_encrypted_pdf_reports_warning() -> None:
    backend = pymupdf_module.factory(_config("pymupdf"))
    data = _encrypted_pdf(document_bytes("pdf"))
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 0
    assert analysis.signals == []
    assert [d.code for d in analysis.diagnostics] == ["pdf-encrypted"]
    assert analysis.diagnostics[0].message.endswith("without a password")


def test_pymupdf_analyze_blank_page_reports_no_extractable_text() -> None:
    backend = pymupdf_module.factory(_config("pymupdf"))
    data = document_bytes("pdf", pages=[[]])
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 1
    assert analysis.signals[0].blank is True
    assert analysis.signals[0].replacement_char_ratio is None
    assert [d.code for d in analysis.diagnostics] == ["pdf-no-extractable-text"]


def test_pymupdf_analyze_counts_image_objects_on_a_scanned_page() -> None:
    """pc-svr: a scanned page must report its image XObjects, not a constant 0.

    The pymupdf path used to write ``image_count=0`` unconditionally, so a
    scan read as "blank" and the image-mass routing trigger stayed dead. This
    runs the real pypdf inspection against the repo's own scanned fixture; if
    the counting is ever re-hardcoded to a constant, this assertion fails.
    """
    backend = pymupdf_module.factory(_config("pymupdf"))
    analysis = backend.analyze(_source(mixed_scanned_pdf()))
    scan_page, first_text_page = analysis.signals[0], analysis.signals[1]
    assert scan_page.image_count >= 1
    assert scan_page.blank is True
    assert first_text_page.image_count == 0


def test_pymupdf_analyze_image_mass_counts_exactly() -> None:
    """pc-svr: page-level image XObjects count one-for-one, pinning the >=5 cases."""
    backend = pymupdf_module.factory(_config("pymupdf"))
    for count in (1, 4, 5, 8):
        analysis = backend.analyze(_source(image_heavy_pdf(count)))
        assert analysis.page_count == 1
        assert analysis.signals[0].image_count == count, f"expected {count} image XObjects"


def test_pymupdf_analyze_image_mass_requires_at_least_one_image() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        image_heavy_pdf(0)


# ── convert / extraction ─────────────────────────────────────────────────


def test_pymupdf_factory_raises_when_inspect_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> ModuleType:
        if name.endswith("_analyze"):
            raise ImportError(name)
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.pymupdf.import_module", raiser)
    with pytest.raises(ImportError, match="_analyze"):
        pymupdf_module.factory(_config("pymupdf"))


def test_pymupdf_convert_missing_pymupdf_yields_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> ModuleType:
        if name == _PYMUPDF_CONVERT_MODULE:
            raise ImportError(name)
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.pymupdf.import_module", raiser)
    backend = pymupdf_module.factory(_config("pymupdf"))
    data = document_bytes("pdf")
    result = backend.convert(_request(data))
    assert result.pages == []
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.code is FailureCode.DEPENDENCY_MISSING
    assert failure.pass_kind is PassKind.NATIVE
    assert failure.backend == "pymupdf"
    assert "pdf" in failure.detail


def test_pymupdf_convert_with_fake_extraction_impl(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImpl:
        @staticmethod
        def page_layouts(data: bytes, *, filetype: str = "pdf") -> list[PageText]:
            # Two pages of prose: one line, then a blank-line-separated pair.
            return [_page("first page block"), _page("second page", "", "line two")]

    def loader(name: str) -> Any:
        if name == _PYMUPDF_CONVERT_MODULE:
            return FakeImpl()
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.pymupdf.import_module", loader)
    backend = pymupdf_module.factory(_config("pymupdf"))
    result = backend.convert(_request(document_bytes("pdf")))
    assert len(result.pages) == 2
    assert _contents(result, page=1) == ["first page block"]
    assert _contents(result, page=2) == ["second page", "line two"]
    assert [p.page_number for p in result.pages] == [1, 2]
    assert result.failures == []


def test_pymupdf_text_impl_module_loads_with_fake_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def get_text(self, kind: str) -> dict[str, object]:
            assert kind == "dict"  # extraction hands over geometry, not plain text
            return {
                "blocks": [
                    {
                        "type": 0,
                        "lines": [
                            {
                                "bbox": (0.0, 0.0, 40.0, 12.0),
                                "spans": [{"font": "Helvetica", "size": 12.0, "bbox": (0.0, 0.0, 40.0, 12.0), "text": self._text}],
                            }
                        ],
                    }
                ]
            }

    class FakeDocument:
        def __init__(self) -> None:
            self._pages = [FakePage("alpha"), FakePage("beta")]

        def __iter__(self) -> Iterator[FakePage]:
            return iter(self._pages)

        @staticmethod
        def close() -> None:
            closed.append(True)

    class FakePymupdf:
        @staticmethod
        def open(*, stream: bytes, filetype: str) -> FakeDocument:
            assert stream.startswith(b"%PDF")
            assert filetype == "pdf"
            return FakeDocument()

    monkeypatch.setitem(sys.modules, "pymupdf", FakePymupdf)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.pymupdf._convert", raising=False)
    try:
        impl = cast(Any, importlib.import_module("parsecraft.backends.pymupdf._convert"))
        layouts = impl.page_layouts(b"%PDF-fake")
    finally:
        # The module was imported against the FAKE pymupdf; drop it again so
        # later tests re-import against the real library (full-env gates).
        sys.modules.pop("parsecraft.backends.pymupdf._convert", None)
    assert [line.text for page in layouts for line in page.lines] == ["alpha", "beta"]
    assert closed == [True]


# ── pdf_inspect against fakes ──────────────────────────────────────────────


def test_pymupdf_inspect_statistics_against_fake_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # plain PDF: text, blank page, garbled page, and an extract_text() None
    reader = _FakePdfReader(encrypted=False, decrypt_result=0, texts=["Hello world.", "", "bad � data", None])
    module = _import_pymupdf_inspect(monkeypatch, reader)
    report = module.inspect(b"%PDF-fake")
    assert report.encrypted is False
    assert report.readable is True
    assert report.page_count == 4
    assert [stats.blank for stats in report.pages] == [False, True, False, True]
    assert report.pages[0].replacement_char_ratio == 0.0
    assert report.pages[1].replacement_char_ratio is None
    assert report.pages[2].replacement_char_ratio is not None
    assert report.pages[2].replacement_char_ratio > 0
    assert report.pages[3].replacement_char_ratio is None  # extract_text() -> None


def test_pymupdf_inspect_encrypted_paths_against_fake_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # encrypted, empty password rejected → unreadable, no page stats
    blocked = _FakePdfReader(encrypted=True, decrypt_result=_FakePasswordType.NOT_DECRYPTED, texts=[])
    report = _import_pymupdf_inspect(monkeypatch, blocked).inspect(b"%PDF-fake")
    assert report.encrypted is True
    assert report.readable is False
    assert report.page_count == 0
    assert report.pages == []

    # encrypted, empty password accepted → readable with stats
    openable = _FakePdfReader(encrypted=True, decrypt_result=_FakePasswordType.USER_PASSWORD, texts=["secret text"])
    report = _import_pymupdf_inspect(monkeypatch, openable).inspect(b"%PDF-fake")
    assert report.encrypted is True
    assert report.readable is True
    assert report.page_count == 1
    assert report.pages[0].text_chars == len("secret text")


# ── helpers ──────────────────────────────────────────────────────────────


def _encrypted_pdf(data: bytes) -> bytes:
    pypdf = importlib.import_module("pypdf")
    writer = pypdf.PdfWriter()
    writer.append(io.BytesIO(data))
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ── office: DOCX + PPTX (pc-16y) ───────────────────────────────────────────


def test_office_analysis_is_the_size_proxy_without_the_pdf_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Office analysis must not need the ``pdf`` extra, and reports real pages.

    The declared surface stays analyzable on a base install: only conversion
    crosses that boundary, so the extra's module is made unimportable here and
    the office branch still answers. The page model is the package's own count
    (docProps/app.xml - pc-4u7.44): a docx declaring 10 pages analyzes as 10
    pages with one signal each, not as the old single-page size proxy.
    """

    def refusing_loader(name: str) -> Any:
        if name == _PYMUPDF_CONVERT_MODULE:
            raise ImportError(name)
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.pymupdf.import_module", refusing_loader)
    backend = pymupdf_module.factory(_config("pymupdf"))
    data = docx_bytes()

    analysis = backend.analyze(_source(data, media_type=_DOCX_MIME))

    assert analysis.page_count >= 1  # the generated fixture carries app.xml or falls back to 1
    assert [signal.page_number for signal in analysis.signals] == list(range(1, analysis.page_count + 1))
    assert all(signal.has_native_text for signal in analysis.signals)


def test_office_analysis_reads_the_declared_page_count() -> None:
    """A package declaring 10 pages yields a 10-page model (pc-4u7.44)."""
    app = '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Pages>10</Pages></Properties>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docProps/app.xml", app)
    backend = pymupdf_module.factory(_config("pymupdf"))
    analysis = backend.analyze(_source(buffer.getvalue(), media_type=_DOCX_MIME))
    assert analysis.page_count == 10
    assert len(analysis.signals) == 10


def test_office_conversion_names_the_format_to_the_stream_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    """Office conversion reuses the PDF stream route, told which format it is.

    No temp file exists to clean up: measured 2026-10-13 against real
    python-docx/python-pptx files, ``open(stream=...)`` returns byte-identical
    pages to the temp-file route, so ``NamedTemporaryFile``/``mkstemp`` are made
    to raise here and conversion still succeeds. A source with no declared
    media type keeps the PDF filetype — the behaviour every earlier fixture
    relied on.
    """
    pytest.importorskip("pptx", reason="the office scaffold needs python-pptx (test extra)")
    seen: list[tuple[int, str]] = []

    class FakeImpl:
        @staticmethod
        def page_layouts(data: bytes, *, filetype: str = "pdf") -> list[PageText]:
            seen.append((len(data), filetype))
            return [_page("office block")]

    def loader(name: str) -> Any:
        if name == _PYMUPDF_CONVERT_MODULE:
            return FakeImpl()
        return importlib.import_module(name)

    def no_temp_file(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the office route must not need a temp file")

    monkeypatch.setattr("parsecraft.backends.pymupdf.import_module", loader)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_temp_file)
    monkeypatch.setattr(tempfile, "mkstemp", no_temp_file)
    backend = pymupdf_module.factory(_config("pymupdf"))

    office_files = {"docx": docx_bytes, "pptx": pptx_bytes, "pdf": lambda: document_bytes("pdf")}
    for suffix, media_type, expected in (("docx", _DOCX_MIME, "docx"), ("pptx", _PPTX_MIME, "pptx"), ("pdf", None, "pdf")):
        seen.clear()
        content = office_files[suffix]()
        result = backend.convert(ConversionRequest(source=_source(content, media_type=media_type)))
        assert seen == [(len(content), expected)]  # the original bytes, not a copy on disk
        assert _contents(result) == ["office block"]
        assert result.failures == []


def test_real_pymupdf_extracts_generated_docx_and_pptx_text() -> None:
    """End-to-end with the real engine over real, generated office files.

    Skipped without the ``pdf`` extra (the default suite runs light); run it as
    ``uv run --extra pymupdf pytest tests/backends/test_pymupdf.py -k real_pymupdf``.
    The fixtures are produced by python-docx/python-pptx because mupdf rejects
    hand-assembled OOXML, so a fabricated package could not exercise this path
    at all.
    """
    pytest.importorskip("pymupdf", reason="the pdf extra is not installed")
    sys.modules.pop(_PYMUPDF_CONVERT_MODULE, None)
    try:
        backend = pymupdf_module.factory(_config("pymupdf"))
        for suffix, media_type, texts in (("docx", _DOCX_MIME, DOCX_TEXTS), ("pptx", _PPTX_MIME, PPTX_TEXTS)):
            content = docx_bytes() if suffix == "docx" else pptx_bytes()
            result = backend.convert(ConversionRequest(source=_source(content, media_type=media_type)))
            joined = "\n".join(block.content for page in result.pages for block in page.blocks)
            for text in texts:
                assert text in joined, f"{suffix}: {text!r} missing from extraction"
            assert result.failures == []
    finally:
        sys.modules.pop(_PYMUPDF_CONVERT_MODULE, None)


def _page(*texts: str) -> PageText:
    """A page of 12 pt single-span lines; an empty entry leaves a blank line.

    Blank lines carry no glyphs, so the text layer shows them only as extra
    vertical space — which is exactly what this helper models.
    """
    lines: list[dict[str, object]] = []
    y = 10.0
    for text in texts:
        if not text:
            y += 24.0  # one blank line of vertical space
            continue
        lines.append(
            {
                "bbox": (0.0, y, 100.0, y + 12.0),
                "spans": [{"font": "Helvetica", "size": 12.0, "bbox": (0.0, y, 100.0, y + 12.0), "text": text}],
            }
        )
        y += 12.0
    return page_text({"blocks": [{"type": 0, "lines": lines}]})


def _import_pymupdf_inspect(monkeypatch: pytest.MonkeyPatch, reader: _FakePdfReader) -> Any:
    fake = ModuleType("pypdf")
    fake.PasswordType = _FakePasswordType  # ty: ignore[unresolved-attribute] — fake module
    fake.PdfReader = lambda stream: reader  # ty: ignore[unresolved-attribute] — fake module
    fake.PageObject = object  # ty: ignore[unresolved-attribute] — fake module; annotation-only import
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.pymupdf._analyze", raising=False)
    return importlib.import_module("parsecraft.backends.pymupdf._analyze")


def _request(content: bytes, **kwargs: Any) -> ConversionRequest:
    return ConversionRequest(source=_source(content), **kwargs)


def _source(content: bytes, uri: str = "mem://pymupdf", media_type: str | None = None) -> SourceDocument:
    return SourceDocument(uri=uri, content=content, media_type=media_type)


def _config(name: str) -> BackendConfig:
    return BackendConfig(name=name)


def _contents(result: Any, page: int = 1) -> list[str]:
    return [block.content for block in result.pages[page - 1].blocks]
