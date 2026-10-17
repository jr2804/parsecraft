"""The OOXML page model: app.xml counts, honest fallbacks (pc-4u7.44)."""

import io
import zipfile

from parsecraft.backends.office_pages import office_page_count

_APP = '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">{body}</Properties>'


def test_pages_and_slides_are_read_with_and_without_the_namespace() -> None:
    pages = _APP.format(body="<Pages>10</Pages>")
    slides = _APP.format(body="<Slides>7</Slides>")
    plain = "<Properties><Pages>3</Pages></Properties>"
    assert office_page_count(_zip_with_app(pages)) == 10
    assert office_page_count(_zip_with_app(slides)) == 7
    assert office_page_count(_zip_with_app(plain)) == 3


def test_structural_surprises_fall_back_to_one() -> None:
    assert office_page_count(_zip_with_app(None)) == 1  # no app.xml part
    assert office_page_count(_zip_with_app("<not-xml")) == 1  # unparsable
    assert office_page_count(_zip_with_app(_APP.format(body="<Pages>later</Pages>"))) == 1  # non-numeric
    assert office_page_count(_zip_with_app(_APP.format(body="<Pages>0</Pages>"))) == 1  # zero clamps
    assert office_page_count(b"not a zip at all") == 1  # not an OOXML package


def _zip_with_app(app_xml: str | None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if app_xml is not None:
            archive.writestr("docProps/app.xml", app_xml)
    return buffer.getvalue()
