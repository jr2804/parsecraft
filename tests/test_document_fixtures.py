"""Scaffolded documents: determinism, cache placement, minimal-PDF validity."""

from __future__ import annotations

import csv
import io
import json
import re

import pytest

from tests.fixtures.documents import (
    FORMATS,
    DocumentFactory,
    document_bytes,
    minimal_pdf,
)

# ── Factory: formats, placement, determinism ─────────────────────────────


def test_factory_writes_every_scaffold_format(doc_factory: DocumentFactory) -> None:
    for suffix in FORMATS:
        path = doc_factory.write(suffix)
        assert path.is_file()
        assert path.suffix == f".{suffix}"
        assert path.parent == doc_factory.root
        assert path.read_bytes() == document_bytes(suffix)


def test_documents_land_in_the_pytest_cache_dir(doc_factory: DocumentFactory) -> None:
    root = doc_factory.root
    assert root.name == "documents"
    assert "test-cache" in root.parts
    assert root.is_dir()


def test_scaffold_bytes_are_identical_across_calls() -> None:
    for suffix in FORMATS:
        assert document_bytes(suffix) == document_bytes(suffix)


def test_written_documents_are_byte_identical_across_runs(doc_factory: DocumentFactory) -> None:
    for suffix in FORMATS:
        first = doc_factory.write(suffix, stem="stable").read_bytes()
        second = doc_factory.write(suffix, stem="stable").read_bytes()
        assert first == second


def test_suffix_normalization_is_case_and_dot_insensitive() -> None:
    assert document_bytes(".MD") == document_bytes("md")
    assert document_bytes("TXT") == document_bytes("txt")


def test_custom_content_overrides_scaffold(doc_factory: DocumentFactory) -> None:
    text_path = doc_factory.write("txt", stem="custom", content="my own text")
    assert text_path.read_bytes() == b"my own text"
    # Explicit content allows formats outside FORMATS (e.g. probing a new backend).
    xml_path = doc_factory.write("xml", stem="custom", content=b"<doc><a/></doc>")
    assert xml_path.suffix == ".xml"
    assert xml_path.read_bytes() == b"<doc><a/></doc>"


def test_unsupported_suffix_is_rejected(doc_factory: DocumentFactory) -> None:
    with pytest.raises(ValueError, match="unsupported document suffix 'docx'"):
        document_bytes("docx")
    with pytest.raises(ValueError, match="unsupported document suffix"):
        doc_factory.write("docx")


def test_pages_argument_is_pdf_only() -> None:
    with pytest.raises(ValueError, match="only valid for the pdf format, not 'txt'"):
        document_bytes("txt", pages=[["one"]])


# ── Text formats are real documents, not placeholders ────────────────────


def test_text_formats_decode_as_utf8() -> None:
    for suffix in ("txt", "md", "csv", "json", "html"):
        text = document_bytes(suffix).decode("utf-8")
        assert text.strip()


def test_json_fixture_parses() -> None:
    payload = json.loads(document_bytes("json").decode("utf-8"))
    assert payload["fixture"] == "parsecraft"
    assert [item["name"] for item in payload["items"]] == ["alpha", "beta"]


def test_csv_fixture_has_header_and_rows() -> None:
    rows = list(csv.reader(io.StringIO(document_bytes("csv").decode("utf-8"))))
    assert rows[0] == ["id", "name", "score"]
    assert [row[1] for row in rows[1:]] == ["alpha", "beta", "gamma"]


def test_html_fixture_is_a_full_document() -> None:
    html = document_bytes("html").decode("utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>" in html
    assert "</html>" in html


def test_markdown_fixture_starts_with_a_heading() -> None:
    assert document_bytes("md").decode("utf-8").startswith("# ")


# ── Minimal PDF: structural validity, no committed bytes ─────────────────


def test_minimal_pdf_is_deterministic() -> None:
    assert minimal_pdf() == minimal_pdf()
    assert document_bytes("pdf") == minimal_pdf()


def test_minimal_pdf_rejects_empty_page_list() -> None:
    with pytest.raises(ValueError, match="at least one page"):
        minimal_pdf([])


def test_minimal_pdf_structure_is_valid() -> None:
    _assert_valid_pdf(minimal_pdf())


def test_minimal_pdf_multi_page_structure_and_text() -> None:
    pdf = minimal_pdf([["first page line"], ["second page line"]])
    _assert_valid_pdf(pdf)
    assert b"(first page line)" in pdf
    assert b"(second page line)" in pdf
    assert pdf.count(b"/Type /Page ") == 2


def test_scaffold_pdf_is_the_generated_minimal_pdf(doc_factory: DocumentFactory) -> None:
    path = doc_factory.write("pdf")
    pdf = path.read_bytes()
    assert pdf.startswith(b"%PDF-1.4\n")
    _assert_valid_pdf(pdf)


def test_pdf_text_escaping_keeps_the_structure_intact() -> None:
    pdf = minimal_pdf([["paren (inside) and back\\slash"]])
    _assert_valid_pdf(pdf)
    assert b"(paren \\(inside\\) and back\\\\slash)" in pdf


# ── Structural validator (stdlib only — no PDF library in core) ──────────


def _assert_valid_pdf(pdf: bytes) -> None:
    """Assert byte-level PDF invariants: header, xref offsets, streams, trailer."""
    assert pdf.startswith(b"%PDF-1.4\n")
    assert pdf.endswith(b"%%EOF\n")

    startxref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[startxref : startxref + 4] == b"xref"

    lines = pdf[startxref:].split(b"\n")
    assert lines[0] == b"xref"
    first_object, size_token = lines[1].split()
    assert first_object == b"0"
    entries = lines[2 : 2 + int(size_token)]
    assert len(entries) == int(size_token)

    for number, entry in enumerate(entries):
        offset, generation, kind = entry.split()
        assert len(offset) == 10
        assert len(generation) == 5
        if number == 0:
            assert kind == b"f"
            assert generation == b"65535"
            continue
        assert kind == b"n"
        assert pdf[int(offset) :].startswith(f"{number} 0 obj".encode("ascii"))

    assert b"/Root 1 0 R" in pdf
    assert b"/Type /Catalog" in pdf
    assert b"/BaseFont /Helvetica" in pdf

    count_match = re.search(rb"/Count (\d+)", pdf)
    assert count_match is not None
    assert pdf.count(b"/Type /Page ") == int(count_match.group(1))

    _assert_stream_lengths(pdf)


def _assert_stream_lengths(pdf: bytes) -> None:
    """Every ``/Length N`` must equal the byte count between stream and endstream."""
    cursor = 0
    checked = 0
    marker = b"<< /Length "
    while True:
        start = pdf.find(marker, cursor)
        if start == -1:
            break
        length_end = pdf.index(b" >>\nstream\n", start)
        declared = int(pdf[start + len(marker) : length_end])
        data_start = length_end + len(b" >>\nstream\n")
        data_end = pdf.index(b"\nendstream", data_start)
        assert data_end - data_start == declared
        cursor = data_end
        checked += 1
    assert checked >= 1
