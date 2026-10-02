"""The judge seam, its deterministic default, and provider spec types.

A judge only RE-RANKS candidates the planner already deemed eligible; it
cannot add candidates or override hard constraints. ``DeterministicJudge``
is the default. Provider-prefixed model strings (``typesafe-ai/jev-latest``)
resolve to judges via :mod:`parsecraft.routing.judge_providers` — provider
modules import only at resolve time; this package never imports a provider
implementation. Shipped providers: ``typesafe-ai/<model>`` (the TypeSafe cloud
serving System One models), ``zen/<model>`` (OpenCode Zen) and
``ollama/<model>`` (local Ollama) — three System One endpoints sharing one
implementation. A token names the provider, never the model type.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, Field

from parsecraft.backends.protocol import BackendDescriptor
from parsecraft.routing.models import Intent, RoutingPreference
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
    """Cheapest sufficient, family first: preferred model → native before OCR → VRAM → name.

    ``preference`` turns the VRAM tiebreak inside one family: ``SPEED`` and
    ``BALANCED`` (the default) take the smallest declared
    ``estimated_vram_gb`` first, ``QUALITY`` the largest. **VRAM is a proxy,
    not a measurement** — parsecraft holds no quality metadata for backends, so
    the declared model size stands in for strength; it is a real if crude
    signal, not a benchmark. An undeclared size sorts last in *both*
    directions: unknown strength is not zero strength, and inventing a score
    for it would be worse than admitting we have none.

    Preference never reorders across families: for ``Intent.NATIVE`` a
    native-capable candidate stays ahead of every OCR one however large that
    model is, and a preferred backend for an OCR flavor stays first.
    """

    def __init__(self, preference: RoutingPreference = RoutingPreference.BALANCED) -> None:
        self._preference = preference

    @override
    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        ordered = sorted(candidates, key=lambda descriptor: _sort_key(descriptor, intent, self._preference))
        return [descriptor.name for descriptor in ordered]


class JudgeSpec(BaseModel):
    """Parsed ``provider/model[:variant]`` judge-spec string (config/CLI input)."""

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model: str = Field(min_length=1)
    variant: str | None = Field(default=None, min_length=1)


class MachineProfile(BaseModel):
    """Host facts a judge may use when ranking already-eligible candidates.

    Deliberately narrow: the judge sees the VRAM budget and GPU usability the
    planner itself enforces, so it can prefer a candidate that fits and runs on
    this machine. It is a ranking input only — a judge never widens, adds, or
    drops candidates (``plan_route``'s ``_validate_order`` keeps that
    authority), and ``None`` in the profile's place means the host is unknown,
    not that it has no GPU.
    """

    vram_budget_gb: float = Field(default=0.0, ge=0)
    #: Whether the installed runtime can use that VRAM at all. A GPU declared
    #: but unusable (``+cpu`` torch build) must reach the judge as such, or it
    #: reasonably prefers a GPU-only candidate that would crawl on this CPU.
    gpu_usable: bool = False


class JudgeProviderLoader(Protocol):
    """What a registered judge provider exports: spec, host facts, preference → judge.

    ``machine`` and ``preference`` are optional so a loader stays callable with
    the spec alone: ``machine=None`` means the caller had no host facts to offer
    (a library embedding), which is not the same as a host with no GPU, and the
    defaulted ``preference`` is the neutral one.
    """

    def __call__(
        self,
        spec: JudgeSpec,
        machine: MachineProfile | None = None,
        preference: RoutingPreference = RoutingPreference.BALANCED,
    ) -> RoutingJudge: ...


def _sort_key(descriptor: BackendDescriptor, intent: Intent, preference: RoutingPreference) -> tuple[int, float, str]:
    preferred = PREFERRED_BACKENDS[intent]
    ocr = is_ocr(descriptor)
    if intent is Intent.NATIVE:
        family_rank = 1 if ocr else 0
    elif preferred is not None:
        family_rank = 0 if descriptor.name == preferred else (1 if ocr else 2)
    else:
        family_rank = 0 if ocr else 1
    return (family_rank, _strength(descriptor.capabilities.estimated_vram_gb, preference), descriptor.name)


def _strength(vram: float | None, preference: RoutingPreference) -> float:
    """Sort value for a candidate's declared size: bigger is stronger for QUALITY.

    An undeclared size is unknown strength rather than zero strength, so it sorts
    last in both directions.
    """
    if vram is None:
        return float("inf")
    return -vram if preference is RoutingPreference.QUALITY else vram
