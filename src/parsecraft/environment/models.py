"""Detected host facts as a frozen value object."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentInfo(BaseModel):
    """What the probe observed on this host — immutable, deterministic, offline.

    ``backends`` are entry-point names discovered through a fresh registry;
    ``installed_extras`` are the *backend* extras whose import packages are
    actually importable (detected, not declared); ``vram_budget_gb`` is the
    first NVIDIA GPU's total memory (0.0 when none is visible); ``gpu_usable``
    says whether the *installed runtime* could use that GPU — a CUDA-capable
    torch build (see ``cuda_runtime_note`` for why it cannot); ``offline`` is
    the operator-declared flag (``PARSECRAFT_OFFLINE``) — connectivity is never
    probed.

    Hardware presence and runtime usability are deliberately separate facts:
    a host can report 8 GiB of VRAM through ``nvidia-smi`` while the installed
    torch is a ``+cpu`` build, and only the runtime matters for routing.
    """

    model_config = ConfigDict(frozen=True)

    backends: tuple[str, ...] = ()
    installed_extras: frozenset[str] = Field(default_factory=frozenset)
    vram_budget_gb: float = Field(default=0.0, ge=0)
    gpu_usable: bool = False
    offline: bool = False
