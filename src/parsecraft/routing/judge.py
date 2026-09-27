"""The judge seam and its deterministic default.

A judge only RE-RANKS candidates the planner already deemed eligible; it
cannot add candidates or override hard constraints. ``DeterministicJudge``
is the default: preferred model first, native before OCR, lowest VRAM,
stable name order. A Jev/System-One judge would be a future optional extra
implementing :class:`RoutingJudge` — this package never imports one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, override, runtime_checkable

from parsecraft.backends.protocol import BackendDescriptor
from parsecraft.routing.models import Intent
from parsecraft.routing.rules import is_ocr

#: Preferred backend per OCR flavor (code-owned naming contract).
PREFERRED_BACKENDS: dict[Intent, str | None] = {
    Intent.NATIVE: None,
    Intent.OCR_GENERAL: None,
    Intent.OCR_TABLES: "ocr-unlimited",
    Intent.OCR_VISION: "ocr-ovis",
}


@runtime_checkable
class RoutingJudge(Protocol):
    """Orders eligible candidates best-first; returns backend names."""

    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        """Return backend names, best pass first, as a subset of ``candidates``."""
        ...


class DeterministicJudge(RoutingJudge):
    """Cheapest eligible: preferred model → native before OCR → lowest VRAM → name."""

    @override
    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        ordered = sorted(candidates, key=lambda descriptor: _sort_key(descriptor, intent))
        return [descriptor.name for descriptor in ordered]


def _sort_key(descriptor: BackendDescriptor, intent: Intent) -> tuple[int, float, str]:
    preferred = PREFERRED_BACKENDS[intent]
    ocr = is_ocr(descriptor)
    if intent is Intent.NATIVE:
        family_rank = 1 if ocr else 0
    elif preferred is not None:
        family_rank = 0 if descriptor.name == preferred else (1 if ocr else 2)
    else:
        family_rank = 0 if ocr else 1
    vram = descriptor.capabilities.estimated_vram_gb
    return (family_rank, vram if vram is not None else float("inf"), descriptor.name)
