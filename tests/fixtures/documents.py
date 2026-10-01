"""Deterministic synthetic test documents — the generator *is* the fixture.

No document binaries are committed (ADR-0001 §8): every format below is
scaffolded at test time into the pytest cache directory (``cache_dir =
"tests/test-cache"``, whose own ``.gitignore`` keeps it out of version
control). The same input always produces byte-identical output, so tests may
assert on hashes and diffs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: Suffixes the scaffold can generate without explicit caller content.
FORMATS: tuple[str, ...] = ("csv", "html", "json", "md", "pdf", "txt")

_TEXT_CONTENT: dict[str, str] = {
    "csv": "id,name,score\n1,alpha,0.5\n2,beta,0.75\n3,gamma,1.0\n",
    "html": (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>ParseCraft synthetic HTML fixture</title>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>ParseCraft synthetic HTML fixture</h1>\n"
        "  <p>A short paragraph of generated body text.</p>\n"
        "  <table>\n"
        "    <thead><tr><th>id</th><th>name</th></tr></thead>\n"
        "    <tbody><tr><td>1</td><td>alpha</td></tr></tbody>\n"
        "  </table>\n"
        "</body>\n"
        "</html>\n"
    ),
    "json": (
        "{\n"
        '  "fixture": "parsecraft",\n'
        '  "generated": false,\n'
        '  "items": [\n'
        '    {"id": 1, "name": "alpha", "score": 0.5},\n'
        '    {"id": 2, "name": "beta", "score": 0.75}\n'
        "  ]\n"
        "}\n"
    ),
    "md": ("# ParseCraft synthetic Markdown fixture\n\nA short paragraph of body text.\n\n## Second section\n\n- item one\n- item two\n"),
    "txt": (
        "ParseCraft synthetic text fixture.\n"
        "\n"
        "This file is generated at test time by tests/fixtures/documents.py.\n"
        "It is deterministic: every run writes these exact bytes.\n"
    ),
}

_DEFAULT_PAGE_LINES: tuple[str, ...] = (
    "ParseCraft synthetic PDF fixture",
    "Generated at test time by tests/fixtures/documents.py.",
    "No PDF bytes are ever committed to the repository.",
)

_PDF_PAGE_WIDTH = 612
_PDF_PAGE_HEIGHT = 792
_PDF_FONT_SIZE = 18
_PDF_LEADING = 24
#: Side of the grayscale image object in :func:`mixed_scanned_pdf` (square pixels).
_IMAGE_SIDE = 8


class DocumentFactory:
    """Writes deterministic synthetic documents into one cache subdirectory."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Directory documents are written to."""
        return self._root

    def write(self, suffix: str, *, stem: str = "sample", content: str | bytes | None = None) -> Path:
        """Write one document and return its path; ``content`` overrides the scaffold.

        Any suffix is accepted when explicit ``content`` is given, so callers
        can probe formats outside :data:`FORMATS` with their own bytes.
        """
        key = suffix.removeprefix(".").lower()
        if content is None:
            data = document_bytes(key)
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = content
        path = self._root / f"{stem}.{key}"
        path.write_bytes(data)
        return path


def document_bytes(suffix: str, *, pages: Sequence[Sequence[str]] | None = None) -> bytes:
    """Return the deterministic bytes for a synthetic document of ``suffix``.

    ``pages`` only applies to ``pdf``; any other format with ``pages`` set
    raises :class:`ValueError`.
    """
    key = suffix.removeprefix(".").lower()
    if key == "pdf":
        return minimal_pdf(pages)
    if pages is not None:
        msg = f"pages= is only valid for the pdf format, not {key!r}"
        raise ValueError(msg)
    try:
        text = _TEXT_CONTENT[key]
    except KeyError:
        msg = f"unsupported document suffix {suffix!r}; scaffold covers: {', '.join(FORMATS)}"
        raise ValueError(msg) from None
    return text.encode("utf-8")


def minimal_pdf(pages: Sequence[Sequence[str]] | None = None) -> bytes:
    """Build a minimal, structurally valid PDF with stdlib bytes only.

    One page by default; ``pages`` gives one text block per page. Object
    offsets and the xref table are computed exactly, so the result opens in a
    real PDF reader and is byte-identical between calls.
    """
    page_lines = [list(lines) for lines in (pages if pages is not None else [_DEFAULT_PAGE_LINES])]
    if not page_lines:
        msg = "pages must contain at least one page"
        raise ValueError(msg)

    objects: list[bytes] = []
    kids = " ".join(f"{3 + index} 0 R" for index in range(len(page_lines)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_lines)} >>".encode("ascii"))
    font_number = 3 + 2 * len(page_lines)
    for index in range(len(page_lines)):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PDF_PAGE_WIDTH} {_PDF_PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
                f"/Contents {3 + len(page_lines) + index} 0 R >>"
            ).encode("ascii")
        )
    for lines in page_lines:
        stream = _page_content(lines)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _assemble(objects)


def mixed_scanned_pdf(text_pages: Sequence[Sequence[str]] | None = None) -> bytes:
    """Build a PDF whose first page is an image-only scan and the rest carry text.

    Page 1 has no text layer at all (one grayscale image), so a text-layer scan
    reports page 1 as needing OCR while the text pages do not — the fixture for
    mixed-intent routing and for pinning 1-based page indexing. Text pages
    default to two pages, as in :func:`minimal_pdf`.
    """
    page_lines = [list(lines) for lines in (text_pages if text_pages is not None else [_DEFAULT_PAGE_LINES, ["Second text page."]])]
    if not page_lines:
        msg = "text_pages must contain at least one page"
        raise ValueError(msg)

    page_count = len(page_lines) + 1
    kids = " ".join(f"{3 + index} 0 R" for index in range(page_count))
    image_number = 3 + page_count
    first_content = image_number + 1
    font_number = first_content + page_count
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"),
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PDF_PAGE_WIDTH} {_PDF_PAGE_HEIGHT}] "
            f"/Resources << /XObject << /Im0 {image_number} 0 R >> >> /Contents {first_content} 0 R >>"
        ).encode("ascii"),
    ]
    for index in range(len(page_lines)):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PDF_PAGE_WIDTH} {_PDF_PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
                f"/Contents {first_content + 1 + index} 0 R >>"
            ).encode("ascii")
        )
    image_data = bytes(range(_IMAGE_SIDE * _IMAGE_SIDE))
    objects.append(
        (
            f"<< /Type /XObject /Subtype /Image /Width {_IMAGE_SIDE} /Height {_IMAGE_SIDE} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length {len(image_data)} >>"
        ).encode("ascii")
        + b"\nstream\n"
        + image_data
        + b"\nendstream"
    )
    image_stream = f"q {_PDF_PAGE_WIDTH - 72} 0 0 {_PDF_PAGE_HEIGHT - 144} 36 72 cm /Im0 Do Q".encode("ascii")
    objects.append(f"<< /Length {len(image_stream)} >>\nstream\n".encode("ascii") + image_stream + b"\nendstream")
    for lines in page_lines:
        stream = _page_content(lines)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _assemble(objects)


def _assemble(objects: Sequence[bytes]) -> bytes:
    """Wrap numbered objects into a PDF with an exact xref table (byte-identical per call)."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n").encode("ascii")
    return bytes(out)


def _page_content(lines: Sequence[str]) -> bytes:
    """Render the content stream for one page: one text-showing operator per line."""
    commands = ["BT", f"/F1 {_PDF_FONT_SIZE} Tf", "1 0 0 1 72 720 Tm", f"{_PDF_LEADING} TL"]
    for line in lines:
        commands.append(f"({_escape_pdf_text(line)}) Tj T*")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", "replace")


def _escape_pdf_text(text: str) -> str:
    """Escape a string for a PDF literal ``(...)`` sequence."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
