"""Host probe: installed backends, importable extras, GPU VRAM, offline flag.

Detected facts only — no network, no torch/vLLM imports. The GPU comes from a
bounded ``nvidia-smi`` subprocess call, extras from ``importlib.util.find_spec``
(which locates a module without importing it), backends from a *fresh*
``BackendRegistry`` so the process-wide default registry is never polluted.
"""

from __future__ import annotations

import os
import re
from importlib.util import find_spec
from subprocess import TimeoutExpired, run

from parsecraft.backends.registry import BackendRegistry
from parsecraft.environment.models import EnvironmentInfo

#: Declared map: extra name -> import packages whose presence proves it installed.
EXTRA_IMPORTS: dict[str, tuple[str, ...]] = {
    "download": ("huggingface_hub",),
    "liteparse": ("liteparse",),
    "ocr-ovis": ("transformers",),
    "ocr-qianfan": ("transformers",),
    "ocr-tele": ("transformers",),
    "ocr-unlimited": ("transformers",),
    "pdf": ("pymupdf",),
    "pdf-inspector": ("pdf_inspector",),
    "pdf-lite": ("pypdf",),
    "vllm": ("vllm",),
    "web": ("httpx",),
}

_OFFLINE_ENV = "PARSECRAFT_OFFLINE"
_OFFLINE_TRUE_VALUES = frozenset({"1", "true", "yes"})
_NVIDIA_SMI_QUERY = ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"]
_SMI_TIMEOUT_S = 10.0
_MIB_PER_GIB = 1024
_MIB_PATTERN = re.compile(r"(\d+)\s*MiB")


def probe_environment() -> EnvironmentInfo:
    """Detect this host's routing-relevant facts — offline, deterministic.

    Never imports torch/vLLM and never touches the network: the offline state
    is operator-declared via ``PARSECRAFT_OFFLINE``, not probed.
    """
    registry = BackendRegistry()  # fresh instance: the default registry stays untouched
    descriptors = registry.list_backends()
    backends = tuple(sorted(descriptor.name for descriptor in descriptors))
    groups = (descriptor.capabilities.optional_dependency_group for descriptor in descriptors)
    installed = frozenset(group for group in groups if group is not None and _extra_present(group))
    return EnvironmentInfo(
        backends=backends,
        installed_extras=installed,
        vram_budget_gb=_detect_vram_gb(),
        offline=_declared_offline(),
    )


def _extra_present(group: str) -> bool:
    """Detected, not declared: every import package of the extra resolves."""
    modules = EXTRA_IMPORTS.get(group)
    if modules is None:  # unknown group is not detectable — extend EXTRA_IMPORTS
        return False
    return all(find_spec(module) is not None for module in modules)


def _declared_offline() -> bool:
    """Operator-declared offline flag; connectivity is never probed."""
    return os.environ.get(_OFFLINE_ENV, "").strip().lower() in _OFFLINE_TRUE_VALUES


def _detect_vram_gb() -> float:
    """First NVIDIA GPU's total VRAM in GiB; missing/failing nvidia-smi → 0.0."""
    try:
        # Fixed argv, no shell, bounded timeout — nothing untrusted reaches the exec.
        completed = run(_NVIDIA_SMI_QUERY, capture_output=True, text=True, timeout=_SMI_TIMEOUT_S, check=False)  # noqa: S603
    except (OSError, TimeoutExpired):
        return 0.0
    if completed.returncode != 0:
        return 0.0
    return _parse_vram_gb(completed.stdout)


def _parse_vram_gb(stdout: str) -> float:
    """First ``<n> MiB`` total in the CSV output; malformed/empty → 0.0."""
    for line in stdout.splitlines():
        match = _MIB_PATTERN.search(line)
        if match is not None:
            return int(match.group(1)) / _MIB_PER_GIB
    return 0.0
