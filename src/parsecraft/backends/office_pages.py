"""Truthful page counts for OOXML office sources — stdlib-only.

The Word/PPTX package carries its own page (or slide) count in
``docProps/app.xml``; reading it needs only ``zipfile`` and ElementTree, so a
base install can analyze office sources without any heavy dependency while
reporting a page model that matches the document instead of the size proxy a
single page used to be (pc-4u7.44: a 9-page Word file was analyzed as one page,
the executor then requested a whole-document conversion while expecting exactly
that one page, and the conversion failed its own contract check).

Every office-capable analyzer uses this one helper, so all of them publish the
SAME page model for the same bytes — whichever backend analyzes, any other can
convert against that model. The count is Word's own saved value; a renderer may
legitimately differ at the tail (a merged last page), which is why the pipeline
executor accepts a contiguous prefix of the expected pages rather than exact
equality.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

_APP_XML = "docProps/app.xml"
#: app.xml tags that carry a page-ish count, in preference order (a Word file
#: has Pages, a slide deck has Slides).
_COUNT_TAGS = ("Pages", "Slides")
#: One tag, whatever namespace it sits in, one integer. The part is a tiny
#: vendor-written properties file read from the zip we just opened; a targeted
#: regex avoids an XML parser entirely (S405), and every surprise still hits
#: the fallback.
_COUNT_PATTERN = "<{tag}[^>]*>\\s*(\\d+)\\s*</{tag}>"


def office_page_count(data: bytes) -> int:
    """Pages (or slides) declared in the package's own metadata, at least 1.

    Any structural surprise — not a zip, no ``docProps/app.xml``, unparsable
    XML, a non-numeric count — yields 1: the honest fallback that keeps the
    base-install analyzability contract (never an exception, never a guess
    above 1).
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            if _APP_XML not in archive.namelist():
                return 1
            raw = archive.read(_APP_XML)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return 1
    for tag in _COUNT_TAGS:
        match = re.search(_COUNT_PATTERN.format(tag=tag).encode(), raw)
        if match is not None:
            return max(int(match.group(1)), 1)
    return 1
