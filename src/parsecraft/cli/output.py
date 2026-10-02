"""CLI output encoding: our writes are UTF-8, never the platform codepage.

A document's glyphs *are* the product surface, so they must not be able to crash
the CLI. On Windows a redirected stdout defaults to the legacy codepage (cp1252
here), where a single U+25AA in a projection raised ``UnicodeEncodeError`` and
lost the whole conversion; ``--json`` was unaffected only because
``json.dumps`` escapes non-ASCII.

``errors="replace"`` is a last-resort guard rather than a licence to lose text:
UTF-8 represents every code point, so it can only ever substitute an unpaired
surrogate from an already-broken decode. Nothing extra is written to stdout, so
its output stays parseable (the JSON projection stays pure ASCII).
"""

from __future__ import annotations

import sys
from typing import Protocol, cast

#: The encoding every parsecraft output write uses — file writes already pass it
#: explicitly (``benchmark/writers.py``, ``cache/store.py``, …); this module
#: covers the streams, which the platform decides for us.
OUTPUT_ENCODING = "utf-8"
#: Substitute, never raise: an unrepresentable glyph must not fail a conversion.
OUTPUT_ERRORS = "replace"


class ReconfigurableStream(Protocol):
    """The slice of ``io.TextIOWrapper`` this module needs."""

    encoding: str | None

    def reconfigure(self, *, encoding: str | None = None, errors: str | None = None) -> None:
        """Switch the stream's encoding/error policy in place."""
        ...


def ensure_utf8_streams() -> None:
    """Reconfigure ``sys.stdout`` and ``sys.stderr`` to UTF-8 where they are not.

    Called once per CLI invocation (``app._callback``), so every command shares
    one policy. A stream that cannot be reconfigured is left alone: a replaced
    stream (a test capture, a wrapper without ``reconfigure``) or one whose
    reconfigure refuses (detached, already closed) is not ours to fix, and
    failing here would be worse than the platform default it keeps.
    """
    for stream in (sys.stdout, sys.stderr):
        _reconfigure(stream)


def _reconfigure(stream: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    if _is_utf8(getattr(stream, "encoding", None)):
        return
    try:
        cast("ReconfigurableStream", stream).reconfigure(encoding=OUTPUT_ENCODING, errors=OUTPUT_ERRORS)
    except (ValueError, OSError):
        return


def _is_utf8(encoding: str | None) -> bool:
    """Whether a stream already writes UTF-8 (``utf-8``, ``UTF8``, ``utf_8``)."""
    return encoding is not None and encoding.lower().replace("-", "").replace("_", "") == "utf8"
