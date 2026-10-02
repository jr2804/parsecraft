"""Detected host environment — the bridge into ``parsecraft.routing`` planning.

Descriptors *declare* what a backend can do (``backends/AGENTS.md``); this
package *detects* what this host actually has installed. Nothing here imports
torch/vLLM: GPU facts come from ``nvidia-smi`` via subprocess, extra presence
from ``importlib.util.find_spec`` (locates, never imports). See
``environment/AGENTS.md`` for the declared-vs-detected contract.
"""

from __future__ import annotations

from parsecraft.environment.constraints import constraints_from_environment
from parsecraft.environment.models import EnvironmentInfo
from parsecraft.environment.probe import EXTRA_IMPORTS, META_EXTRAS, cuda_runtime_note, extra_present, probe_environment

__all__ = [
    "EXTRA_IMPORTS",
    "META_EXTRAS",
    "EnvironmentInfo",
    "constraints_from_environment",
    "cuda_runtime_note",
    "extra_present",
    "probe_environment",
]
