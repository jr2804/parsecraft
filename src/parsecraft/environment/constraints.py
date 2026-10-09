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
    ``gpu_usable``/``engines``/``offline`` always come from the probe.
    ``allow_ocr=None`` derives OCR permission from the detected OCR extras **or**
    a usable engine (ADR-0008 decision 12 — see :func:`_ocr_possible`); an
    explicit value overrides the derivation (e.g. an operator banning OCR on a
    GPU-capable host).
    """
    if allow_ocr is None:
        allow_ocr = _ocr_possible(environment)
    return RoutingConstraints(
        formats=set(formats),
        installed_extras=set(environment.installed_extras),
        vram_budget_gb=environment.vram_budget_gb,
        gpu_usable=environment.gpu_usable,
        engines=environment.engines,
        cached_assets=set(environment.cached_assets),
        max_passes=max_passes,
        allow_ocr=allow_ocr,
        offline=environment.offline,
        preference=preference,
    )


def _ocr_possible(environment: EnvironmentInfo) -> bool:
    """Whether an OCR pass could run here: an ``ocr-*`` extra, or a usable engine.

    Deriving this from the heavyweight extras alone excluded the base backend on
    exactly the hosts ADR-0008 decision 1 exists for: a base-only consumer has no
    ``ocr-*`` extra, so OCR was switched off before the engine was ever consulted
    and a scanned page degraded with nothing to degrade to.

    The OR is deliberately permissive, and decision 13's eligibility gate is what
    keeps it honest — a backend whose engine is absent is ineligible whatever
    ``allow_ocr`` says, so permitting OCR can never route a page into a family
    that then cannot run. That is also why this bridge needs no OCR-specific
    filter over ``engines``: such a filter would have to know which backends are
    OCR, which is the name special-casing decision 13 rules out.
    """
    return any(extra.startswith(_OCR_EXTRA_PREFIX) for extra in environment.installed_extras) or bool(environment.engines)
