"""Optional language detection — an injectable seam, never imported by the core.

The deterministic core carries the *requested* language as plain data
(``RoutingConstraints.language``) and eligibility as a pure function. Filling
that field is the caller's job: pass a detector here (protocol below) or set
the constraint directly from configuration. ``parsecraft`` never imports a
detector implementation and ships none — implement the protocol and inject
it, or configure ``RoutingConstraints.language`` directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LanguageDetector(Protocol):
    """Derives a BCP-47 tag from document text, or ``None`` when unsure."""

    def detect_language(self, text: str) -> str | None:
        """Return a BCP-47 tag for ``text``; ``None`` means 'not identified'."""
        ...
