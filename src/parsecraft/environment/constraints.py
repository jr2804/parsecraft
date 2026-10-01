"""Bridge: detected host environment → the planner's hard routing constraints."""

from __future__ import annotations

from collections.abc import Collection

from parsecraft.environment.models import EnvironmentInfo
from parsecraft.routing.models import RoutingConstraints, RoutingPreference

#: Detected extras starting with this prefix mean an OCR backend can run here.
_OCR_EXTRA_PREFIX = "ocr-"


def constraints_from_environment(
    environment: EnvironmentInfo,
    *,
    formats: Collection[str] = (),
    allow_ocr: bool | None = None,
    max_passes: int = 1,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingConstraints:
    """Fill :class:`RoutingConstraints` from detected host facts.

    ``formats`` (empty = no format restriction), ``max_passes`` and
    ``preference`` are plan inputs; ``installed_extras``/``vram_budget_gb``/
    ``offline`` always come from the probe. ``allow_ocr=None`` derives OCR
    permission from the detected OCR extras; an explicit value overrides the
    derivation (e.g. an operator banning OCR on a GPU-capable host).
    """
    if allow_ocr is None:
        allow_ocr = any(extra.startswith(_OCR_EXTRA_PREFIX) for extra in environment.installed_extras)
    return RoutingConstraints(
        formats=set(formats),
        installed_extras=set(environment.installed_extras),
        vram_budget_gb=environment.vram_budget_gb,
        max_passes=max_passes,
        allow_ocr=allow_ocr,
        offline=environment.offline,
        preference=preference,
    )
