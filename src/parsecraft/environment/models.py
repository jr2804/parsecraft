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

    ``engines`` names the external engine binaries the registered backends
    require that this host can actually use — probed from the PATH, or declared
    outright through the engine's environment variable (ADR-0008 decision 2). It
    is a routing fact, not an inventory: only engines some backend declares as
    required are probed, so a host never pays for a fact nobody asked for.

    ``cached_assets`` names the model assets ("model_id@revision") whose pinned
    weights are VERIFIED present in the managed cache (pc-u4q semantics: the
    marker's digest matches the pin and size+mtime are unchanged). Only assets
    some backend declares are checked; probing is a pure cache read — no
    network, no re-hash — and an unverifiable asset counts as absent
    (fail-safe).
    """

    model_config = ConfigDict(frozen=True)

    backends: tuple[str, ...] = ()
    installed_extras: frozenset[str] = Field(default_factory=frozenset)
    vram_budget_gb: float = Field(default=0.0, ge=0)
    gpu_usable: bool = False
    offline: bool = False
    engines: frozenset[str] = Field(default_factory=frozenset)
    cached_assets: frozenset[str] = Field(default_factory=frozenset)
