"""Charset detection via charset-normalizer (core dependency, ADR-0001 §4).

Loaded only through :func:`parsecraft.adapters.encoding`'s ``import_module``
seam, so ``parsecraft.adapters`` stays import-clean when the dependency is
absent.
"""

from __future__ import annotations

from charset_normalizer import from_bytes


def detect_charset(data: bytes) -> str | None:
    """Best-guess charset name for raw bytes, or ``None`` if undecided."""
    best = from_bytes(data).best()
    if best is None:
        return None
    return best.encoding
