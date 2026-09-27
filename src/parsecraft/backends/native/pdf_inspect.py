"""pypdf-based PDF inspection (``pdf-lite`` extra) — analysis, no extraction.

Covers page count, encryption detection, and per-page text statistics so
``analyze()`` works without the AGPL ``pdf`` extra (ADR-0003).
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PasswordType, PdfReader

from parsecraft.backends.native._common import PageTextStats, PdfInspection


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
            )
        )
    return PdfInspection(encrypted=encrypted, readable=True, page_count=len(pages), pages=pages)
