"""Host probe: installed backends, importable extras, GPU VRAM, offline flag.

Detected facts only — no network, no torch/vLLM imports. The GPU comes from a
bounded ``nvidia-smi`` subprocess call plus a metadata read of the installed
torch build, extras from ``importlib.util.find_spec`` (which locates a module
without importing it), backends from a *fresh* ``BackendRegistry`` so the
process-wide default registry is never polluted.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Collection
from importlib.util import find_spec
from pathlib import Path
from subprocess import TimeoutExpired, run

from parsecraft.assets.manager import AssetManager
from parsecraft.assets.models import AssetPin
from parsecraft.backends.ocr._models import TESSERACT_ENGINE, TESSERACT_ENV
from parsecraft.backends.ocr.tesseract import find_tesseract_cmd
from parsecraft.backends.protocol import ModelAssetDescriptor
from parsecraft.backends.registry import BackendRegistry
from parsecraft.environment.models import EnvironmentInfo

_ABSENT_CUDA_VALUES = frozenset({"None", "''", '""'})


#: Declared map: extra name -> import packages whose presence proves it installed.
EXTRA_IMPORTS: dict[str, tuple[str, ...]] = {
    "docling": ("docling",),
    "download": ("huggingface_hub",),
    "liteparse": ("liteparse",),
    "marker": ("marker",),  # bring-your-own dependency (ADR-0006): no declared extra
    "mineru": ("mineru",),
    "ocr-ovis": ("transformers",),
    "ocr-qianfan": ("transformers",),
    "ocr-tele": ("transformers",),
    "ocr-unlimited": ("transformers",),
    "pandoc": ("pypandoc",),
    "pdf": ("pymupdf",),
    "pdf-inspector": ("pdf_inspector",),
    "pdf-lite": ("pypdf",),
    "systemone": ("typesafe_sdk",),
    "trafilatura": ("trafilatura",),
    "vllm": ("vllm",),
    "web": ("httpx",),
}

#: Extras with nothing to import: they exist to pull other extras in (a
#: self-referential *meta* extra). Detectability is not a property of them —
#: installing one installs the extras it names, and those are detected
#: individually — so they must never appear in ``EXTRA_IMPORTS``. Declared here
#: so the "every declared extra is either import-detectable or explicitly meta"
#: contract stays checkable (``tests/test_environment.py``).
META_EXTRAS: frozenset[str] = frozenset({"auto"})

#: Engine name -> the environment variable that DECLARES it. A declaration is
#: honoured verbatim and never probed (ADR-0008 decision 2): an operator who
#: points at an engine outside PATH gets it counted. Keyed by ENGINE name, not
#: backend name, so no backend is special-cased (decision 13). Pandoc and the
#: docling LibreOffice path are the obvious future members; they are
#: deliberately not wired here.
ENGINE_DECLARATIONS: dict[str, str] = {TESSERACT_ENGINE: TESSERACT_ENV}

#: Engine name -> that engine's own host discovery. The probe DELEGATES rather
#: than reimplementing, because eligibility and the executable a backend actually
#: runs must never disagree. This matters concretely: tesseract's discovery
#: includes a Windows install-root scan, and its installer lets a user decline
#: adding itself to PATH — a probe that knew only PATH would report a working
#: install as absent and silently exclude the backend on exactly the hosts
#: ADR-0008 targets. Absent entry = the generic PATH rule in
#: :func:`_engine_available`.
ENGINE_DISCOVERY: dict[str, Callable[[], str | None]] = {TESSERACT_ENGINE: find_tesseract_cmd}

_OFFLINE_ENV = "PARSECRAFT_OFFLINE"
_OFFLINE_TRUE_VALUES = frozenset({"1", "true", "yes"})
_NVIDIA_SMI_QUERY = ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"]
_SMI_TIMEOUT_S = 10.0
_MIB_PER_GIB = 1024
_MIB_PATTERN = re.compile(r"(\d+)\s*MiB")
_TORCH_MODULE = "torch"
_TORCH_VERSION_FILE = "version.py"
#: ``cuda: Optional[str] = None`` (CPU wheel) or ``cuda = '12.6'`` (CUDA wheel).
_TORCH_CUDA_PATTERN = re.compile(r"^cuda(?:\s*:\s*[\w\[\]\. ]+)?\s*=\s*(.+)$", re.MULTILINE)


class _NoFetch:
    """The probe never downloads: a structural downloader that fails loudly."""

    @staticmethod
    def download(model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        raise AssertionError("the environment probe never downloads assets")


def probe_environment() -> EnvironmentInfo:
    """Detect this host's routing-relevant facts — offline, deterministic.

    Never imports torch/vLLM and never touches the network: the offline state
    is operator-declared via ``PARSECRAFT_OFFLINE``, not probed. The GPU facts
    are hardware *and* runtime: ``vram_budget_gb`` is nvidia-smi's total, and
    ``gpu_usable`` is true only when the installed torch build has CUDA support.
    """
    registry = BackendRegistry()  # fresh instance: the default registry stays untouched
    descriptors = registry.list_backends()
    backends = tuple(sorted(descriptor.name for descriptor in descriptors))
    groups = (descriptor.capabilities.optional_dependency_group for descriptor in descriptors)
    installed = frozenset(group for group in groups if group is not None and extra_present(group))
    vram_budget_gb = _detect_vram_gb()
    # The CUDA-runtime question is answered UNCONDITIONALLY - also on hosts
    # without nvidia-smi (macOS, GPU-less CI): it is part of the environment's
    # description, and short-circuiting it would make the probe result depend
    # on the host's GPU presence rather than the installed torch build.
    cuda_note = cuda_runtime_note()
    required_engines = {d.capabilities.required_engine for d in descriptors if d.capabilities.required_engine is not None}
    required_assets = [d.capabilities.model_asset for d in descriptors if d.capabilities.model_asset is not None]
    return EnvironmentInfo(
        backends=backends,
        installed_extras=installed,
        vram_budget_gb=vram_budget_gb,
        gpu_usable=vram_budget_gb > 0 and cuda_note is None,
        offline=_declared_offline(),
        engines=_detect_engines(required_engines),
        cached_assets=_detect_cached_assets(required_assets),
    )


def cuda_runtime_note() -> str | None:
    """``None`` when a CUDA-capable runtime is installed, else *why* it is not.

    Reads the installed torch build's own metadata file (``version.py``, which
    records ``cuda = None`` for a CPU-only wheel) — a millisecond-scale lookup
    that never imports torch, so probing stays cheap on hosts that will not use
    it. ``importlib.metadata.version('torch')`` cannot answer this: it drops the
    ``+cpu`` local tag. An unreadable or silent file is reported, not guessed.
    """
    spec = find_spec(_TORCH_MODULE)
    if spec is None:
        return f"{_TORCH_MODULE} is not installed (install a CUDA build to use GPU backends)"
    origin = spec.origin
    if origin is None:
        return f"cannot determine the installed {_TORCH_MODULE} build ({origin!r} origin)"
    version_file = Path(origin).parent / _TORCH_VERSION_FILE
    try:
        text = version_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"cannot read {version_file} to determine the CUDA runtime: {exc}"
    match = _TORCH_CUDA_PATTERN.search(text)
    if match is None:
        return f"{_TORCH_MODULE} does not declare a CUDA version in {version_file.name}"
    if match.group(1).strip() in _ABSENT_CUDA_VALUES:
        return f"the installed {_TORCH_MODULE} build has no CUDA support"
    return None


def extra_present(group: str) -> bool:
    """Detected, not declared: every import package of the extra resolves."""
    modules = EXTRA_IMPORTS.get(group)
    if modules is None:  # unknown group is not detectable — extend EXTRA_IMPORTS
        return False
    return all(find_spec(module) is not None for module in modules)


def _declared_offline() -> bool:
    """Operator-declared offline flag; connectivity is never probed."""
    return os.environ.get(_OFFLINE_ENV, "").strip().lower() in _OFFLINE_TRUE_VALUES


def _detect_engines(required: Collection[str]) -> frozenset[str]:
    """Which of the *required* engines this host can actually use.

    Only engines some registered backend declares are probed, so the cost is
    paid only for facts somebody asked for. Presence is the engine's own
    discovery — see :func:`_engine_available`.
    """
    return frozenset(name for name in required if _engine_available(name))


def _detect_cached_assets(assets: Collection[ModelAssetDescriptor]) -> frozenset[str]:
    """Which of the *declared* model assets are verified present in the cache (pc-m0k).

    Same economy as engines: only assets some registered backend declares are
    checked. Verification is the pc-u4q marker semantics via
    ``AssetManager.is_verified`` — a pure cache read with no download and no
    re-hash; an unverifiable asset (missing, foreign, or half-written marker,
    or no per-file manifest at all) counts as absent (fail-safe).
    """
    manager = AssetManager(offline=True, downloader=_NoFetch())  # the probe verifies, never fetches
    verified = set[str]()
    for asset in assets:
        if not asset.file_pins:
            continue  # no integrity manifest -> nothing to verify -> absent
        pin = AssetPin(
            descriptor=asset,
            filenames=[file_pin.path for file_pin in asset.file_pins],
            expected_sha256={file_pin.path: file_pin.sha256 for file_pin in asset.file_pins},
        )
        if manager.is_verified(pin):
            verified.add(f"{asset.model_id}@{asset.model_revision}")
    return frozenset(verified)


def _engine_available(name: str) -> bool:
    """Whether one required engine is usable on this host.

    Declaration first and never probed (ADR-0008 decision 2), then the engine's
    own discovery when it declares one, else a PATH lookup — a generic question
    that needs no per-engine knowledge.
    """
    if os.environ.get(ENGINE_DECLARATIONS.get(name, ""), "").strip():
        return True
    discovery = ENGINE_DISCOVERY.get(name)
    if discovery is not None:
        return discovery() is not None
    return shutil.which(name) is not None


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
