"""Detected host facts as a frozen value object."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentInfo(BaseModel):
    """What the probe observed on this host — immutable, deterministic, offline.

    ``backends`` are entry-point names discovered through a fresh registry;
    ``installed_extras`` are the *backend* extras whose import packages are
    actually importable (detected, not declared); ``vram_budget_gb`` is 0.0
    when no NVIDIA GPU is visible; ``offline`` is the operator-declared flag
    (``PARSECRAFT_OFFLINE``) — connectivity is never probed.
    """

    model_config = ConfigDict(frozen=True)

    backends: tuple[str, ...] = ()
    installed_extras: frozenset[str] = Field(default_factory=frozenset)
    vram_budget_gb: float = Field(default=0.0, ge=0)
    offline: bool = False
