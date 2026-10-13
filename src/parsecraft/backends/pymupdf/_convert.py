"""PyMuPDF-based PDF text extraction (``pdf`` extra, AGPL — ADR-0003).

Loaded only by :func:`parsecraft.backends.pymupdf._extract_pdf` via
``import_module`` at conversion time; never imported at module import.

Extraction hands over the page *dictionary* (positioned lines and spans) rather
than plain text, because layout recovery needs geometry: which lines are
monospace, where they start horizontally, and how far apart they sit
(:mod:`parsecraft.backends.pymupdf.code_layout` owns every rule that reads it).
"""

from __future__ import annotations

import pymupdf  # ty: ignore[unresolved-import] — optional: "pdf" extra (AGPL, ADR-0003)

from parsecraft.backends.pymupdf.code_layout import PageText, page_text


def page_layouts(data: bytes, *, filetype: str = "pdf") -> list[PageText]:
    """Positioned lines of every page, in document order.

    ``filetype`` names the format for PyMuPDF's stream opener: the PDF importer
    can sniff a stream, but the office importers cannot, so they must be told
    (``"docx"``/``"pptx"``). A real python-docx file opened this way returns
    byte-identical pages to the temp-file route, so no path handler is needed.
    """
    document = pymupdf.open(stream=data, filetype=filetype)
    try:
        return [page_text(page.get_text("dict")) for page in document]
    finally:
        document.close()
