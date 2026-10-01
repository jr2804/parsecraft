"""The judge seam, its deterministic default, and provider spec types.

A judge only RE-RANKS candidates the planner already deemed eligible; it
cannot add candidates or override hard constraints. ``DeterministicJudge``
is the default. Provider-prefixed model strings (``ollaya/laya:typed-decisions``)
resolve to judges via :mod:`parsecraft.routing.judge_providers` — provider
modules import only at resolve time; this package never imports a provider
implementation. Shipped providers: ``ollaya/laya`` (local daemon),
``systemone/<model>`` (TypeSafe cloud), ``zen/<model>`` (OpenCode Zen) and
``ollama/<model>`` (local Ollama), the last three System One endpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, Field

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


class JudgeSpec(BaseModel):
    """Parsed ``provider/model[:variant]`` judge-spec string (config/CLI input)."""

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model: str = Field(min_length=1)
    variant: str | None = Field(default=None, min_length=1)


class MachineProfile(BaseModel):
    """Host facts a judge may use when ranking already-eligible candidates.

    Deliberately narrow: the judge sees the VRAM budget the planner itself
    enforces, so it can prefer a candidate that fits this machine. It is a
    ranking input only — a judge never widens, adds, or drops candidates
    (``plan_route``'s ``_validate_order`` keeps that authority), and ``None``
    in its place means the host is unknown, not that it has no GPU.
    """

    vram_budget_gb: float = Field(default=0.0, ge=0)


class JudgeProviderLoader(Protocol):
    """What a registered judge provider exports: spec plus host facts → judge.

    ``machine`` is optional so a loader stays callable with the spec alone:
    ``None`` means the caller had no host facts to offer (a library embedding),
    which is not the same as a host with no GPU.
    """

    def __call__(self, spec: JudgeSpec, machine: MachineProfile | None = None) -> RoutingJudge: ...


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
