"""Offline tests for the trafilatura backend (the library is stubbed).

The behaviour contract these pin is the knox call shape — ``extract`` runs with
``output_format="markdown", include_tables=True, include_links=False,
no_fallback=False`` and an empty Markdown result falls back to plain text — plus
the projection of that Markdown into typed chunks.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
from parsecraft.backends.trafilatura.trafilatura import (
    DESCRIPTOR,
    TRAFILATURA_FORMATS,
    factory,
)
from parsecraft.ir.models import ChunkKind, FailureCode

_CONFIG = BackendConfig(name="trafilatura")
_IMPL = "parsecraft.backends.trafilatura._impl"


# ── descriptor: verified formats, no asset surface ─────────────────────────


def test_descriptor_declares_the_verified_formats() -> None:
    assert DESCRIPTOR.name == "trafilatura"
    assert DESCRIPTOR.version == "0.1.0"
    assert set(DESCRIPTOR.capabilities.supported_formats) == set(TRAFILATURA_FORMATS)
    assert TRAFILATURA_FORMATS == ("text/html",)  # only what was conversion-verified
    capabilities = DESCRIPTOR.capabilities
    assert capabilities.supports_page_ranges is False
    assert capabilities.supports_multi_page is False
    assert capabilities.estimated_vram_gb is None  # CPU-only
    assert capabilities.optional_dependency_group == "trafilatura"
    # asset-free: the whole dependency tree is text extraction, no weights
    assert capabilities.model_asset is None


# ── light factory: typed boundary errors ───────────────────────────────────


def test_factory_missing_extra_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError("No module named 'trafilatura'", name="trafilatura")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(DependencyUnavailableError) as exc_info:
        factory(_CONFIG)
    assert exc_info.value.module == "trafilatura"
    assert exc_info.value.extra == "trafilatura"


def test_factory_rejects_impl_without_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda name: ModuleType("bare.impl"))
    with pytest.raises(BackendError, match="must expose create"):
        factory(_CONFIG)


# ── analyze: a converter's honest minimum ──────────────────────────────────


def test_analyze_reports_single_text_bearing_page(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    payload = b"<html><body>hello</body></html>"
    analysis = backend.analyze(_source(payload))
    assert analysis.page_count == 1
    signal = analysis.signals[0]
    assert signal.page_number == 1
    assert signal.has_native_text is True
    assert signal.blank is False
    assert signal.text_chars == len(payload)
    assert signal.image_count == 0
    # analyze is a converter's minimum: it must not build a DOM or extract
    assert trafilatura_stub["calls"] == []


def test_analyze_marks_blank_html(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    analysis = backend.analyze(_source(b"   \n\t "))
    signal = analysis.signals[0]
    assert signal.has_native_text is False
    assert signal.blank is True


def test_analyze_reads_file_uri(tmp_path: Path, trafilatura_stub: dict[str, Any]) -> None:
    path = tmp_path / "page.html"
    path.write_bytes(b"<html>on disk</html>")
    backend = factory(_CONFIG)
    analysis = backend.analyze(SourceDocument(uri=f"file://{path}", media_type=None, content=None))
    assert analysis.signals[0].text_chars == len(b"<html>on disk</html>")


# ── convert: the knox call shape ───────────────────────────────────────────


def test_convert_uses_the_knox_call_shape(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.failures == []
    # verbatim contract: tables kept, links/images/formulas never rendered
    assert trafilatura_stub["calls"] == [
        {
            "output_format": "markdown",
            "include_tables": True,
            "include_links": False,
            "no_fallback": False,
        }
    ]
    # the raw bytes go in, not a decoded string: trafilatura reads the declared
    # charset itself, so a non-UTF-8 page keeps its accents
    assert trafilatura_stub["html_was_bytes"] is True
    assert trafilatura_stub["html"] == b"<html><body>hello</body></html>"


def test_convert_projects_markdown_into_typed_blocks(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["markdown"] = (
        "# The Real Title\n\nBody paragraph.\n\n## A Section\n\n```\nunsigned long mulMode;\n```\n- alpha\n- beta\n\n| k | v |\n| --- | --- |\n| 1 | 2 |\n"
    )
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.failures == []
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.page_number == 1
    assert [block.kind for block in page.blocks] == [
        ChunkKind.HEADING,
        ChunkKind.PARAGRAPH,
        ChunkKind.HEADING,
        ChunkKind.CODE,
        ChunkKind.LIST,
        ChunkKind.TABLE,
    ]
    heading, _, section, code, list_block, table = page.blocks
    assert heading.content == "The Real Title"
    assert heading.metadata == {"heading_level": "1"}
    assert section.metadata == {"heading_level": "2"}
    assert code.content == "unsigned long mulMode;"
    assert list_block.metadata == {"list_type": "bullet"}
    assert list_block.content == "alpha\nbeta"
    assert table.rows == (("k", "v"), ("1", "2"))
    # never one undifferentiated paragraph: this is the design's whole point
    assert len(page.blocks) > 1


def test_convert_falls_back_to_plain_text_when_markdown_is_empty(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["markdown"] = ""
    trafilatura_stub["text"] = "plain text fallback body"
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.failures == []
    assert [call.get("output_format") for call in trafilatura_stub["calls"]] == ["markdown", "text"]
    assert result.pages[0].blocks[0].content == "plain text fallback body"


def test_convert_falls_back_when_markdown_is_whitespace_only(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["markdown"] = "   \n\n "
    trafilatura_stub["text"] = "fallback wins"
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert [call.get("output_format") for call in trafilatura_stub["calls"]] == ["markdown", "text"]
    assert result.pages[0].blocks[0].content == "fallback wins"


def test_convert_none_markdown_falls_back(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["markdown"] = None
    trafilatura_stub["text"] = "fallback for None"
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.failures == []
    assert result.pages[0].blocks[0].content == "fallback for None"


def test_convert_passes_raw_bytes_so_non_utf8_pages_survive(
    trafilatura_stub: dict[str, Any],
) -> None:
    r"""The charset contract: bytes go in, so trafilatura detects the charset.

    Pre-decoding as UTF-8 with ``errors="replace"`` destroyed every non-ASCII
    character on a non-UTF-8 page — verified against the real library, where a
    latin-1 ``caf\xe9`` came back as ``caf�``. The fix is to never decode here.
    """
    payload = ('<html><head><meta charset="iso-8859-1"></head><body><article><p>caf\xe9</p></article></body></html>').encode("latin-1")
    backend = factory(_CONFIG)
    result = backend.convert(make_request(content=payload))
    assert result.failures == []
    assert trafilatura_stub["html_was_bytes"] is True
    assert trafilatura_stub["html"] == payload


def test_real_trafilatura_keeps_non_utf8_pages_intact(tmp_path: Path) -> None:
    """End-to-end proof of the same contract with the real library."""
    pytest.importorskip("trafilatura", reason="trafilatura extra not installed")
    sys.modules.pop(_IMPL, None)
    payload = (
        '<html><head><meta charset="iso-8859-1"></head><body><article>'
        "<h1>Caf\xe9</h1><p>cr\xe8me br\xfbl\xe9e na\xefve r\xe9sum\xe9.</p>"
        "</article></body></html>"
    ).encode("latin-1")
    path = tmp_path / "latin1.html"
    path.write_bytes(payload)
    backend = factory(_CONFIG)
    result = backend.convert(make_request(payload, uri=f"file://{path}"))
    assert result.failures == []
    joined = "\n".join(block.content for block in result.pages[0].blocks)
    assert "Caf\xe9" in joined
    assert "�" not in joined
    sys.modules.pop(_IMPL, None)


# ── convert: bounds and typed failures ─────────────────────────────────────


def test_convert_unsupported_media_type_is_typed(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(media_type="application/pdf"))
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.INVALID_INPUT]
    assert "unsupported media type" in result.failures[0].detail
    assert trafilatura_stub["calls"] == []  # extraction never attempted


def test_convert_empty_html_is_typed(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(content=b"   \n  "))
    assert [f.code for f in result.failures] == [FailureCode.INVALID_INPUT]
    assert "got no content" in result.failures[0].detail
    assert trafilatura_stub["calls"] == []


def test_convert_upstream_failure_is_typed(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["error"] = ValueError("malformed markup")
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert [f.code for f in result.failures] == [FailureCode.BACKEND_ERROR]
    assert "malformed markup" in result.failures[0].detail
    assert "ValueError" in result.failures[0].detail


def test_convert_empty_after_fallback_is_typed(trafilatura_stub: dict[str, Any]) -> None:
    trafilatura_stub["markdown"] = ""
    trafilatura_stub["text"] = ""
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.BACKEND_ERROR]
    assert "returned no content" in result.failures[0].detail


def test_convert_cancellation_before_extraction(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(cancellation=lambda: True))
    assert [f.code for f in result.failures] == [FailureCode.CANCELLED]
    assert result.pages == []
    assert trafilatura_stub["calls"] == []


def test_convert_records_the_backend_reference(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(timeout_s=2.5))
    assert result.backend.name == "trafilatura"
    assert result.backend.version == "0.1.0"
    assert result.failures == []


def test_failure_carries_the_budget_and_pass_kind(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(content=b"", timeout_s=2.5))
    failure = result.failures[0]
    assert failure.budget_s == 2.5
    assert failure.backend == "trafilatura"
    assert failure.backend_version == "0.1.0"


# ── source_bytes: the shared file:// boundary ──────────────────────────────


def test_source_bytes_rejects_non_file_uri(trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    with pytest.raises(BackendError, match="cannot read source"):
        backend.analyze(_source(None, uri="https://example.test/page.html"))


def test_source_bytes_missing_local_file_is_typed(tmp_path: Path, trafilatura_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    missing = tmp_path / "absent.html"
    with pytest.raises(BackendError, match="cannot read source"):
        backend.analyze(_source(None, uri=f"file://{missing}"))


# ── real-library contract (active only with the `trafilatura` extra) ───────


def test_real_trafilatura_projects_headings_code_lists_and_tables(tmp_path: Path) -> None:
    """The verified end-to-end contract: real extraction, real typed chunks.

    This is the empirical counterpart to the stub tests — it proves trafilatura's
    Markdown really does carry ``#``/``##`` headings, code fences, list items and
    Markdown tables, so the projection is not guessing at a shape that never
    occurs in practice.
    """
    pytest.importorskip("trafilatura", reason="trafilatura extra not installed")
    sys.modules.pop(_IMPL, None)
    html = (
        "<html><head><title>T</title></head><body>"
        "<nav>Home About Contact</nav><article>"
        "<h1>The Real Title</h1>"
        "<p>First paragraph of the article body with enough words to count as content.</p>"
        "<h2>A Section</h2>"
        "<p>Second paragraph, also reasonably long so the extractor keeps it as body text.</p>"
        "<pre><code>unsigned long mulMode;\nreturn;</code></pre>"
        "<ul><li>alpha</li><li>beta</li></ul>"
        "<table><thead><tr><th>k</th><th>v</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
        "</article></body></html>"
    )
    path = tmp_path / "article.html"
    path.write_text(html, encoding="utf-8")
    backend = factory(_CONFIG)
    result = backend.convert(make_request(path.read_bytes(), uri=f"file://{path}"))
    assert result.failures == []
    kinds = [block.kind for block in result.pages[0].blocks]
    assert ChunkKind.HEADING in kinds
    assert ChunkKind.CODE in kinds
    assert ChunkKind.TABLE in kinds
    assert ChunkKind.LIST in kinds
    # navigation chrome is not part of the extraction
    joined = "\n".join(block.content for block in result.pages[0].blocks)
    assert "Home About Contact" not in joined
    assert "The Real Title" in joined
    sys.modules.pop(_IMPL, None)


# ── fixtures and helpers ───────────────────────────────────────────────────


@pytest.fixture
def trafilatura_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Install a fake `trafilatura` and force a fresh heavy-impl import."""
    records: dict[str, Any] = {"calls": [], "markdown": "converted markdown", "text": "converted text", "error": None, "html_was_bytes": False}
    monkeypatch.setitem(sys.modules, "trafilatura", _make_fake_trafilatura(records))
    sys.modules.pop(_IMPL, None)
    yield records
    sys.modules.pop(_IMPL, None)


def _make_fake_trafilatura(records: dict[str, Any]) -> ModuleType:
    """A fake `trafilatura` that records every `extract` call and its kwargs."""

    def extract(html: str | bytes, **kwargs: Any) -> str | None:
        records["html"] = html
        records["html_was_bytes"] = isinstance(html, bytes)
        records["calls"].append(kwargs)
        if records["error"] is not None:
            raise records["error"]
        if kwargs.get("output_format") == "markdown":
            return records["markdown"]
        return records["text"]

    stub = ModuleType("trafilatura")
    stub.extract = extract  # ty: ignore[unresolved-attribute] — fake module
    return stub


def _source(
    content: bytes | None,
    *,
    media_type: str | None = "text/html",
    uri: str = "mem://page.html",
) -> SourceDocument:
    return SourceDocument(uri=uri, media_type=media_type, content=content)


def make_request(
    content: bytes | None = b"<html><body>hello</body></html>",
    *,
    media_type: str | None = "text/html",
    uri: str = "mem://page.html",
    **kwargs: Any,
) -> ConversionRequest:
    source = SourceDocument(uri=uri, media_type=media_type, content=content)
    return ConversionRequest(source=source, **kwargs)
