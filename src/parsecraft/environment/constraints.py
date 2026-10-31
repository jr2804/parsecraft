"""Bridge: detected host environment → the planner's hard routing constraints."""

from __future__ import annotations

from collections.abc import Collection

from parsecraft.environment.models import EnvironmentInfo
from parsecraft.routing.models import RoutingConstraints, RoutingPreference


def constraints_from_environment(
    environment: EnvironmentInfo,
    *,
    formats: Collection[str] = (),
    allow_ocr: bool | None = None,
    max_passes: int = 1,
    preference: RoutingPreference = RoutingPreference.BALANCED,
    preferred_backend: str | None = None,
    engines: Collection[str] | None = None,
    backend_options: dict[str, dict[str, str | int | float | bool]] | None = None,
    only_backend: str | None = None,
) -> RoutingConstraints:
    """Fill :class:`RoutingConstraints` from detected host facts.

    ``formats`` (empty = no format restriction), ``max_passes``, ``preference``
    and ``preferred_backend`` are plan inputs; the rest come from the probe.
    ``preferred_backend`` is a ranking preference (the CLI ``--backend``, or a
    library caller) — the planner moves it to the front of a page's valid order,
    and it enters the conversion-cache key so two requests for different
    preferred backends never share a cached artifact.

    ``engines`` overrides the probed external-engine set, so a caller can
    restrict which engine-backed backends are eligible without re-probing the
    host (``None`` keeps the probe's answer). ``backend_options`` are per-backend
    constructor options spread into ``BackendConfig.options`` at dispatch (e.g.
    ``{"mineru": {"effort": "high"}}``) and are part of the cache key.
    """
    if allow_ocr is None:
        allow_ocr = _ocr_possible(environment)
    return RoutingConstraints(
        formats=set(formats),
        installed_extras=set(environment.installed_extras),
        vram_budget_gb=environment.vram_budget_gb,
        gpu_usable=environment.gpu_usable,
        engines=environment.engines if engines is None else frozenset(engines),
        cached_assets=set(environment.cached_assets),
        max_passes=max_passes,
        allow_ocr=allow_ocr,
        offline=environment.offline,
        preference=preference,
        preferred_backend=preferred_backend,
        only_backend=only_backend,
        backend_options=dict(backend_options or {}),
    )


def _ocr_possible(environment: EnvironmentInfo) -> bool:
    """Whether an OCR pass could run here — read off the probed OCR backends.

    The fact is ``EnvironmentInfo.ocr_backends``: registered OCR-family backends
    whose dependency group is installed or whose required engine is usable
    (pc-r22). Two earlier derivations were wrong in opposite directions: asking
    for an ``ocr-`` extra prefix answered False on every real host (the extras are
    ``ocr`` and ``surya``), and the ``or bool(environment.engines)`` arm that
    replaced it answered True for a host carrying pandoc and no OCR backend at all.

    Nothing here judges eligibility. A backend whose group is installed may still
    be ineligible (no GPU, missing weights) — decision 13's eligibility gate is
    what keeps this permissive fact honest, so permitting OCR can never route a
    page into a family that then cannot run.
    """
    return bool(environment.ocr_backends)
