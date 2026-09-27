"""``parsecraft models`` — inspect and manage the pinned model-asset cache.

The descriptor catalogue comes from the backend registry
(``capabilities.model_asset``); local state comes from
:class:`parsecraft.assets.AssetManager`. Installing a model needs an
:class:`parsecraft.assets.AssetPin` (files + sha256); the shipped
:data:`PinProvider` default refuses until the assets pin catalogue lands
(bead pc-4u7.22) rather than fabricating checksums.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from parsecraft.assets import (
    AssetError,
    AssetManager,
    AssetPin,
    CacheReport,
    DownloaderUnavailableError,
    OfflineModeError,
    default_downloader,
)
from parsecraft.backends import default_registry
from parsecraft.backends.protocol import BackendDescriptor, ModelAssetDescriptor
from parsecraft.cli.errors import CliError
from parsecraft.environment import probe_environment

_BYTE_UNITS: tuple[str, ...] = ("B", "KiB", "MiB", "GiB", "TiB")

#: Builds the integrity pin for a model asset. The shipped default is a refusal;
#: the assets pin catalogue (bead pc-4u7.22) plugs in here.
PinProvider = Callable[[ModelAssetDescriptor], AssetPin]


class ModelEntry(BaseModel):
    """One model asset: its descriptor joined with local cache state."""

    model_config = ConfigDict(frozen=True)

    name: str
    model_id: str
    revision: str
    size_bytes: int | None
    model_license: str
    quantization: str | None
    estimated_vram_gb: float | None
    cached: bool


class _UnavailableDownloader:
    """Stand-in downloader for read-only commands when the ``download`` extra is absent."""

    @staticmethod
    def download(model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        raise DownloaderUnavailableError(model_id, revision, "the 'download' extra is required (pip install parsecraft[download])")


def available_descriptors() -> list[BackendDescriptor]:
    """Descriptors from the process registry (the model-asset catalogue source)."""
    return default_registry.list_backends()


def manager() -> AssetManager:
    """Asset manager for the local cache, respecting the detected offline flag."""
    try:
        downloader = default_downloader()
    except DownloaderUnavailableError:
        downloader = _UnavailableDownloader()
    return AssetManager(offline=probe_environment().offline, downloader=downloader)


def entries(descriptors: Sequence[BackendDescriptor], report: CacheReport) -> list[ModelEntry]:
    """Join the descriptor catalogue with the cache snapshot, sorted by name."""
    cached = {(asset.model_id, asset.revision) for asset in report.assets}
    return sorted(
        (
            ModelEntry(
                name=name,
                model_id=asset.model_id,
                revision=asset.model_revision,
                size_bytes=asset.size_bytes,
                model_license=asset.model_license,
                quantization=asset.quantization,
                estimated_vram_gb=asset.estimated_vram_gb,
                cached=(asset.model_id, asset.model_revision) in cached,
            )
            for name, asset in catalog(descriptors)
        ),
        key=lambda entry: entry.name,
    )


def resolve_asset(name: str, descriptors: Sequence[BackendDescriptor]) -> tuple[str, ModelAssetDescriptor]:
    """Resolve a backend name or model id to its asset descriptor."""
    for backend_name, asset in catalog(descriptors):
        if name in (backend_name, asset.model_id):
            return backend_name, asset
    raise CliError(f"unknown model {name!r}; run `parsecraft models list`", exit_code=2)


def catalog(descriptors: Sequence[BackendDescriptor]) -> list[tuple[str, ModelAssetDescriptor]]:
    """``(backend name, asset)`` for every backend that declares a model asset."""
    return [(descriptor.name, descriptor.capabilities.model_asset) for descriptor in descriptors if descriptor.capabilities.model_asset is not None]


def install(
    descriptor: ModelAssetDescriptor,
    asset_manager: AssetManager,
    *,
    confirmed: bool,
    accept_license: bool,
    provider: PinProvider | None = None,
) -> list[str]:
    """Install one pinned asset; never downloads without ``--yes``."""
    if not confirmed:
        raise CliError(f"refusing to install {descriptor.model_id!r} without --yes (this downloads from the network)", exit_code=2)
    if asset_manager.offline:
        raise CliError(f"offline mode: cannot install {descriptor.model_id!r}; unset PARSECRAFT_OFFLINE or pre-cache the asset")
    if descriptor.requires_user_acceptance and asset_manager.license_acceptance(descriptor.model_id, descriptor.model_revision) is None:
        if not accept_license:
            raise CliError(f"license {descriptor.model_license!r} requires acceptance — re-run with --accept-license", exit_code=2)
        asset_manager.accept_license(descriptor)
    pin = (_missing_pin if provider is None else provider)(descriptor)
    try:
        return asset_manager.ensure(pin)
    except DownloaderUnavailableError as exc:
        raise CliError(f"{exc} — install the download extra: pip install 'parsecraft[download]'") from exc
    except OfflineModeError as exc:
        raise CliError(f"{exc} — unset PARSECRAFT_OFFLINE or pre-cache the asset") from exc
    except AssetError as exc:
        raise CliError(str(exc)) from exc


def _missing_pin(descriptor: ModelAssetDescriptor) -> AssetPin:
    """Refuse to invent a manifest; the pin catalogue is bead pc-4u7.22."""
    raise CliError(f"no pinned manifest for {descriptor.model_id!r} yet — the asset pin catalogue is not published (bead pc-4u7.22)")


def remove(descriptor: ModelAssetDescriptor, asset_manager: AssetManager) -> list[str]:
    """Remove every cached revision of one model; return the revisions removed."""
    revisions = sorted(asset.revision for asset in asset_manager.inspect_cache().assets if asset.model_id == descriptor.model_id)
    return [revision for revision in revisions if asset_manager.remove(descriptor.model_id, revision)]


def clean(asset_manager: AssetManager) -> int:
    """Delete every cached revision; return how many were removed."""
    return asset_manager.clear_cache()


def entries_payload(items: Sequence[ModelEntry]) -> list[dict[str, object]]:
    """JSON-ready rows for ``models list --json``."""
    return [entry.model_dump(mode="json") for entry in items]


def render_entries(items: Sequence[ModelEntry]) -> list[str]:
    """Text table for ``models list``."""
    if not items:
        return ["No model assets registered."]
    lines = [f"{'NAME':14} {'MODEL_ID':26} {'REVISION':12} {'SIZE':9} {'LICENSE':12} {'QUANT':8} {'VRAM':6} CACHED"]
    lines.extend(
        f"{entry.name:14} {entry.model_id:26} {entry.revision:12} {human_bytes(entry.size_bytes):9} "
        f"{entry.model_license:12} {entry.quantization or '-':8} "
        f"{f'{entry.estimated_vram_gb:g}G' if entry.estimated_vram_gb is not None else '-':6} "
        f"{'yes' if entry.cached else 'no'}"
        for entry in items
    )
    return lines


def human_bytes(size: int | None) -> str:
    """Human-readable byte size; ``-`` when unknown."""
    if size is None:
        return "-"
    value = float(size)
    index = 0
    while value >= 1024 and index < len(_BYTE_UNITS) - 1:
        value /= 1024
        index += 1
    unit = _BYTE_UNITS[index]
    return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
