"""Deterministic synthetic test documents — the generator *is* the fixture.

No document binaries are committed (ADR-0001 §8): every format below is
scaffolded at test time into the pytest cache directory (``cache_dir =
"tests/test-cache"``, whose own ``.gitignore`` keeps it out of version
control). The text formats and the PDF are byte-deterministic; the OFFICE
scaffolds are not (pc-93d — their producers stamp wall-clock times into ZIP
entries and core properties), so office tests assert on extracted content.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
from docx import Document
from pptx import Presentation
from pptx.util import Inches

#: Suffixes the scaffold can generate without explicit caller content.
#: Office formats are deliberately NOT here: this tuple is the vocabulary the
#: manifest must cover, and DOCX/PPTX are synthetic (built by their real
#: producers, see :func:`docx_bytes`) rather than downloaded real documents.
FORMATS: tuple[str, ...] = ("csv", "html", "json", "md", "pdf", "txt")

#: Text the office scaffolds are built from. Exported so a test asserts the
#: strings the generator wrote rather than a second copy that could drift.
DOCX_TEXTS: tuple[str, ...] = (
    "ParseCraft synthetic docx heading",
    "ParseCraft synthetic docx paragraph body text.",
    "ParseCraft synthetic docx cell alpha",
    "ParseCraft synthetic docx cell beta",
)
PPTX_TEXTS: tuple[str, ...] = (
    "ParseCraft synthetic pptx slide one",
    "ParseCraft synthetic pptx slide one body.",
    "ParseCraft synthetic pptx slide two",
    "ParseCraft synthetic pptx slide two body.",
)

#: Text the workbook scaffold is built from (header cells then data cells).
XLSX_SHEET = "SyntheticSheet"
XLSX_TEXTS: tuple[str, ...] = ("Region", "Score", "north", "0.5", "south", "0.75")

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


def image_heavy_pdf(images: int = 5) -> bytes:
    """Build a single textless page carrying ``images`` distinct image XObjects.

    A page-level resource walker must count exactly ``images`` — this is the
    fixture for pinning the image-mass routing trigger (``HEAVY_IMAGE_COUNT``)
    against *real* counting, not a literal. If production ever re-hardcodes
    ``image_count`` to a constant 0, a test asserting ``image_count == images``
    against this fixture fails.
    """
    if images < 1:
        msg = "images must be at least 1"
        raise ValueError(msg)
    # Object numbers: 1 catalog, 2 pages, 3 the page, then the image
    # objects (4..), and the content stream last — each XObject ref distinct.
    content_number = 4 + images
    image_numbers = tuple(range(4, 4 + images))
    xobject_refs = " ".join(f"/Im{index} {number} 0 R" for index, number in enumerate(image_numbers))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PDF_PAGE_WIDTH} {_PDF_PAGE_HEIGHT}] "
            f"/Resources << /XObject << {xobject_refs} >> >> /Contents {content_number} 0 R >>"
        ).encode("ascii"),
    ]
    for _ in image_numbers:
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
    draws = " ".join(f"q /Im{index} Do Q" for index in range(images))
    content_stream = draws.encode("ascii")
    objects.append(f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii") + content_stream + b"\nendstream")
    return _assemble(objects)


def code_pdf() -> bytes:
    """Build a one-page PDF with prose and a positioned monospace code block.

    The code sits in Courier at explicit x offsets, which is what a real code
    listing looks like in a PDF: the text layer carries glyph positions, not
    spaces. The block exercises layout recovery v1 end to end — relative
    indentation (``return;`` two columns in), a wrapped declaration
    (``unsigned long `` continuing on the next line), a terminated line that
    must NOT be joined, and a closing brace that dedents.

    Page 1 layout, top to bottom::

        A paragraph of prose above the listing.
        void f(void) {
          return;
        unsigned long mulMode;
        }
        A paragraph of prose below the listing.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PDF_PAGE_WIDTH} {_PDF_PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
    ]
    stream = _code_page_content()
    objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    return _assemble(objects)


def docx_bytes() -> bytes:
    """A real ``.docx``: heading, paragraph, and a 1×2 table.

    Built by python-docx rather than hand-assembled because mupdf rejects
    fabricated OOXML — a *complete* hand-built package still fails with
    ``FileDataError`` (measured 2026-10-13) — so the format's own producer is
    the only scaffold PyMuPDF will read. NOT byte-deterministic (pc-93d):
    python-docx stamps the template's core properties with the wall clock.
    """
    document = Document()
    document.add_heading(DOCX_TEXTS[0], level=1)
    document.add_paragraph(DOCX_TEXTS[1])
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = DOCX_TEXTS[2]
    table.cell(0, 1).text = DOCX_TEXTS[3]
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pptx_bytes() -> bytes:
    """A real ``.pptx``: two slides, each a title plus a body text box.

    NOT byte-deterministic (pc-93d): the archive carries wall-clock timestamps.
    Repacking the entries with a fixed ``date_time`` did make it deterministic,
    and was removed — nothing consumes that guarantee, and a needless repack of
    OOXML is a needless risk. Tests assert on extracted content.
    """
    presentation = Presentation()
    for title, body in ((PPTX_TEXTS[0], PPTX_TEXTS[1]), (PPTX_TEXTS[2], PPTX_TEXTS[3])):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
        box.text_frame.text = body
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def xlsx_bytes() -> bytes:
    """A real ``.xlsx``: one sheet, a header row and two data rows.

    Built by openpyxl, which is a **base dependency** — so this is an ordinary
    module-level import, the way the office dev-group helpers are imported. The
    backend module itself still defers openpyxl to call time (137 ms cold, and
    registry discovery imports every factory eagerly), but a fixture runs once
    per test session and may simply import it.

    NOT byte-deterministic, measured (pc-93d): openpyxl rewrites the core
    properties' ``modified`` inside ``save()`` and stamps each ZIP entry with
    the wall clock, so two calls in different seconds differ. Setting
    ``properties.created/modified`` does not survive the save. Tests assert on
    the CONTENT the backend extracts, never on bytes.
    """
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = XLSX_SHEET
    sheet.append([XLSX_TEXTS[0], XLSX_TEXTS[1]])
    sheet.append([XLSX_TEXTS[2], XLSX_TEXTS[3]])
    sheet.append([XLSX_TEXTS[4], XLSX_TEXTS[5]])
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    workbook.properties.created = fixed
    workbook.properties.modified = fixed
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _code_page_content() -> bytes:
    """Content stream: prose in Helvetica, code in Courier at explicit offsets."""
    prose = _PDF_FONT_SIZE
    code = 10
    #: (font, size, x, y, text) — y decreases down the page.
    items: tuple[tuple[str, int, int, int, str], ...] = (
        ("F1", prose, 72, 720, "A paragraph of prose above the listing."),
        ("F2", code, 72, 700, "void f(void) {"),
        ("F2", code, 84, 688, "return;"),
        ("F2", code, 72, 676, "unsigned long "),
        ("F2", code, 84, 664, "mulMode; "),
        ("F2", code, 72, 652, "}"),
        ("F1", prose, 72, 620, "A paragraph of prose below the listing."),
    )
    commands = ["BT"]
    for font, size, x, y, text in items:
        commands.append(f"/{font} {size} Tf 1 0 0 1 {x} {y} Tm ({_escape_pdf_text(text)}) Tj")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", "replace")


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
