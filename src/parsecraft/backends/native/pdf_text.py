"""PyMuPDF-based PDF text extraction (``pdf`` extra, AGPL — ADR-0003).

Loaded only by :func:`parsecraft.backends.native.pdf._extract_pdf` via
``import_module`` at conversion time; never imported at module import.

Extraction hands over the page *dictionary* (positioned lines and spans) rather
than plain text, because layout recovery needs geometry: which lines are
monospace, where they start horizontally, and how far apart they sit
(:mod:`parsecraft.backends.native.code_layout` owns every rule that reads it).
"""

from __future__ import annotations

import pymupdf  # ty: ignore[unresolved-import] — optional: "pdf" extra (AGPL, ADR-0003)

from parsecraft.backends.native.code_layout import PageText, page_text


def page_layouts(data: bytes) -> list[PageText]:
    """Positioned lines of every page, in document order."""
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [page_text(page.get_text("dict")) for page in document]
    finally:
        document.close()
