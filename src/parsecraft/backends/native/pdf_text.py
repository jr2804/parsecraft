"""PyMuPDF-based PDF text extraction (``pdf`` extra, AGPL — ADR-0003).

Loaded only by :func:`parsecraft.backends.native.pdf._extract_pdf` via
``import_module`` at conversion time; never imported at module import.
"""

from __future__ import annotations

import pymupdf  # ty: ignore[unresolved-import] — optional: "pdf" extra (AGPL, ADR-0003)


def page_texts(data: bytes) -> list[str]:
    """Extract the raw text of every page, in document order."""
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [page.get_text() for page in document]
    finally:
        document.close()
