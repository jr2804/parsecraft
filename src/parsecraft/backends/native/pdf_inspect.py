"""pypdf-based PDF inspection (``pdf-lite`` extra) — analysis, no extraction.

Covers page count, encryption detection, per-page text statistics, and a
non-decoding image count so ``analyze()`` can distinguish a blank page from a
scanned one without the AGPL ``pdf`` extra (ADR-0003).
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PageObject, PasswordType, PdfReader

from parsecraft.backends.native._common import PageTextStats, PdfInspection

#: The PDF XObject subtype that marks an image.
_IMAGE_SUBTYPE = "/Image"


def inspect(data: bytes) -> PdfInspection:
    """Inspect a PDF's structure and per-page text statistics."""
    reader = PdfReader(BytesIO(data))
    encrypted = reader.is_encrypted
    if encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        return PdfInspection(encrypted=True, readable=False, page_count=0, pages=[])
    pages: list[PageTextStats] = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        ratio = (text.count("�") / len(text)) if text else None
        pages.append(
            PageTextStats(
                page_number=number,
                text_chars=len(text),
                replacement_char_ratio=ratio,
                blank=not text.strip(),
                image_count=_count_page_images(page),
            )
        )
    return PdfInspection(encrypted=encrypted, readable=True, page_count=len(pages), pages=pages)


def _count_page_images(page: PageObject) -> int:
    """Count the page's image XObjects WITHOUT decoding any of them.

    Deliberately not ``page.images``: that property materialises every image,
    which on a 300 dpi scan is exactly the rasterization analysis exists to avoid.
    Walking the resource dict costs ~0.001 ms/page (measured) and is enough to
    tell "nothing here" from "content with no text layer".

    Two known limits, stated rather than papered over: images nested inside a
    Form XObject and inline images are not visible at the page-resource level.
    Both are rare in scanned documents, and under-reporting keeps the existing
    OCR intent rather than inventing a vision one.
    """
    resources = page.get("/Resources", {})
    xobjects = resources.get("/XObject", {})
    return sum(1 for name in xobjects if xobjects[name].get_object().get("/Subtype") == _IMAGE_SUBTYPE)
