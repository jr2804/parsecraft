"""Content-type and character-encoding detection for document input.

``parse_content_type`` is stdlib-only; ``decode`` uses charset-normalizer
(core dependency) behind an ``import_module`` seam so importing this module
never fails and never pulls optional stacks.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol, cast

from pydantic import BaseModel

from parsecraft.adapters.errors import MissingDependencyError

_CHARSET_IMPL_MODULE = "parsecraft.adapters.charset_impl"

_CHARSET_HINT = "charset-normalizer is a core dependency (ADR-0001 §4)"


class EncodingDetection(BaseModel):
    """Decoded text plus the charset it was decoded with."""

    charset: str
    text: str


class _CharsetImpl(Protocol):
    """What the encoding adapter needs from the charset module."""

    def detect_charset(self, data: bytes) -> str | None: ...


def parse_content_type(header: str) -> tuple[str | None, str | None]:
    """Split a ``Content-Type`` header into ``(media_type, charset)``."""
    parts = [part.strip() for part in header.split(";")]
    media_type = parts[0].lower() or None
    charset: str | None = None
    for param in parts[1:]:
        if "=" not in param:
            continue
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip('"').lower() or None
            break
    return media_type, charset


def decode(data: bytes, declared_charset: str | None = None) -> EncodingDetection:
    """Decode bytes, honoring a declared charset before detecting one."""
    if declared_charset is not None:
        decoded = _try_decode(data, declared_charset)
        if decoded is not None:
            return EncodingDetection(charset=declared_charset, text=decoded)
    return _detect(data)


def _try_decode(data: bytes, charset: str) -> str | None:
    try:
        return data.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return None


def _detect(data: bytes) -> EncodingDetection:
    try:
        impl = cast(_CharsetImpl, import_module(_CHARSET_IMPL_MODULE))
    except ImportError as exc:
        raise MissingDependencyError(_CHARSET_IMPL_MODULE, _CHARSET_HINT) from exc
    detected = impl.detect_charset(data)
    charset = detected if detected is not None else "utf-8"
    return EncodingDetection(charset=charset, text=data.decode(charset, errors="replace"))
