"""Asset manager: pinned downloads, cache inspection/cleanup, license records.

Never fetches at import time; all networking happens inside ``AssetManager.ensure``
through the injected :class:`~parsecraft.assets.downloader.Downloader`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from importlib.metadata import version as _dist_version
from pathlib import Path

import pydantic
from platformdirs import user_cache_path

from parsecraft.assets.downloader import Downloader, default_downloader
from parsecraft.assets.errors import (
    AssetError,
    ChecksumMismatchError,
    InsufficientDiskSpaceError,
    LicenseNotAcceptedError,
    OfflineModeError,
)
from parsecraft.assets.models import (
    AssetPin,
    CachedAsset,
    CachedAssetFile,
    CacheReport,
    LicenseAcceptance,
    human_bytes,
)
from parsecraft.backends.protocol import ModelAssetDescriptor

_LICENSE_STORE_FILENAME = "license-acceptances.json"
_CHUNK_SIZE = 1 << 20

#: The first-use download notice goes through this logger (INFO); the CLI
#: routes the channel to stderr (``cli.output.ensure_asset_info_logging``),
#: library embedders configure it like any other logger. Never in
#: ``cli.verbosity.QUIET_LOGGERS`` — this is our own message, not chatter.
logger = logging.getLogger(__name__)


class AssetManager:
    """Downloads, validates, and inspects pinned model assets.

    Parameters are explicit constructor arguments; nothing is read from the
    environment at import time and no network call happens outside
    :meth:`ensure`.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        downloader: Downloader | None = None,
        offline: bool = False,
        min_free_bytes: int = 0,
    ) -> None:
        self.cache_dir = cache_dir if cache_dir is not None else user_cache_path("parsecraft") / "models"
        self.downloader: Downloader = downloader if downloader is not None else default_downloader()
        self.offline = offline
        self.min_free_bytes = min_free_bytes
        self._license_store_path = self.cache_dir / _LICENSE_STORE_FILENAME

    # ── cache ──────────────────────────────────────────────────────────────

    def cache_location(self) -> str:
        """Absolute cache location as a string."""
        return str(self.cache_dir)

    def available_disk_bytes(self) -> int:
        """Free bytes on the volume hosting the cache."""
        return shutil.disk_usage(self.cache_dir).free

    def inspect_cache(self) -> CacheReport:
        """Snapshot every pinned revision currently present in the cache."""
        assets: list[CachedAsset] = []
        if self.cache_dir.is_dir():
            for model_dir in sorted(path for path in self.cache_dir.iterdir() if path.is_dir()):
                for revision_dir in sorted(path for path in model_dir.iterdir() if path.is_dir()):
                    files = [
                        CachedAssetFile(
                            filename=file.name,
                            path=str(file),
                            size_bytes=file.stat().st_size,
                        )
                        for file in sorted(revision_dir.iterdir())
                        if file.is_file()
                    ]
                    assets.append(
                        CachedAsset(
                            model_id=model_dir.name.replace("--", "/"),
                            revision=revision_dir.name,
                            files=files,
                            total_bytes=sum(file.size_bytes for file in files),
                        )
                    )
        return CacheReport(
            location=str(self.cache_dir),
            assets=assets,
            total_bytes=sum(asset.total_bytes for asset in assets),
        )

    def revision_dir(self, model_id: str, revision: str) -> Path:
        """Local directory holding one pinned revision."""
        return self.cache_dir / slug(model_id) / revision

    def remove(self, model_id: str, revision: str) -> bool:
        """Delete one cached revision; return whether anything was removed."""
        target = self.revision_dir(model_id, revision)
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        model_dir = target.parent
        if model_dir.is_dir() and not any(model_dir.iterdir()):
            model_dir.rmdir()
        return True

    def clear_cache(self) -> int:
        """Delete every cached revision; return the number of revisions removed."""
        removed = 0
        if self.cache_dir.is_dir():
            for path in sorted(self.cache_dir.iterdir()):
                if path.is_dir():
                    shutil.rmtree(path)
                    removed += 1
        return removed

    # ── license acceptance ─────────────────────────────────────────────────

    def license_acceptance(self, model_id: str, revision: str) -> LicenseAcceptance | None:
        """The recorded acceptance for one revision, if any."""
        store = self._load_license_store()
        raw = store.get(f"{model_id}@{revision}")
        if raw is None:
            return None
        return pydantic.TypeAdapter(LicenseAcceptance).validate_python(raw)

    def accept_license(self, descriptor: ModelAssetDescriptor) -> LicenseAcceptance:
        """Record explicit acceptance of the descriptor's license terms."""
        acceptance = LicenseAcceptance(
            model_id=descriptor.model_id,
            model_revision=descriptor.model_revision,
            model_license=descriptor.model_license,
            accepted_at=datetime.now(UTC),
        )
        store = self._load_license_store()
        store[f"{descriptor.model_id}@{descriptor.model_revision}"] = acceptance.model_dump(mode="json")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._license_store_path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return acceptance

    def has_all_files(self, pin: AssetPin) -> bool:
        """Whether every pinned file already exists in the cache."""
        target = self.revision_dir(pin.descriptor.model_id, pin.descriptor.model_revision)
        return all((target / filename).is_file() for filename in pin.filenames)

    def ensure(self, pin: AssetPin) -> list[str]:
        """Guarantee the pinned files exist locally; return their local paths.

        Order of operations: offline check → license gate → disk-space check →
        resumable download → checksum validation.
        """
        descriptor = pin.descriptor
        model_id = descriptor.model_id
        revision = descriptor.model_revision
        target = self.revision_dir(model_id, revision)

        if not self.has_all_files(pin):
            if self.offline:
                raise OfflineModeError(model_id, revision, "offline mode and files not cached")
            if descriptor.requires_user_acceptance and self.license_acceptance(model_id, revision) is None:
                raise LicenseNotAcceptedError(
                    model_id,
                    revision,
                    f"license {descriptor.model_license!r} requires explicit acceptance",
                )
            self._check_disk_space(pin)
            target.mkdir(parents=True, exist_ok=True)
            logger.info(
                "downloading %s (%s) into %s",
                model_id,
                human_bytes(descriptor.size_bytes),
                target,
            )
            for filename in pin.filenames:
                self.downloader.download(model_id, revision, filename, str(target))

        self._verify_checksums(pin, target)
        return [str(target / filename) for filename in pin.filenames]

    @staticmethod
    def package_version() -> str:
        """Installed package version recorded alongside license acceptances."""
        return _dist_version_or("0.0.0")

    # ── download / ensure ──────────────────────────────────────────────────

    @staticmethod
    def local_model_dir(descriptor: ModelAssetDescriptor, path: Path) -> Path:
        """Validate a user-supplied local model directory (no download)."""
        if not path.is_dir():
            raise AssetError(descriptor.model_id, descriptor.model_revision, f"local model dir not found: {path}")
        return path

    def _load_license_store(self) -> dict[str, dict[str, str | int]]:
        if not self._license_store_path.is_file():
            return {}
        raw = json.loads(self._license_store_path.read_text(encoding="utf-8"))
        return dict(raw.items())

    def _check_disk_space(self, pin: AssetPin) -> None:
        required = pin.descriptor.size_bytes
        if required is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        available = self.available_disk_bytes()
        if available - self.min_free_bytes < required:
            raise InsufficientDiskSpaceError(pin.descriptor.model_id, pin.descriptor.model_revision, required, available)

    @staticmethod
    def _verify_checksums(pin: AssetPin, target: Path) -> None:
        model_id = pin.descriptor.model_id
        revision = pin.descriptor.model_revision
        for filename in pin.filenames:
            expected = pin.expected_sha256.get(filename)
            if expected is None:
                continue
            actual = sha256_of(target / filename)
            if actual != expected:
                raise ChecksumMismatchError(model_id, revision, filename, expected, actual)


def _dist_version_or(default: str) -> str:
    try:
        return _dist_version("parsecraft")
    except Exception:  # noqa: BLE001 - any metadata failure falls back
        return default


def slug(model_id: str) -> str:
    """Filesystem-safe directory name for a model id."""
    return model_id.replace("/", "--")


def sha256_of(path: Path) -> str:
    """Streaming SHA-256 of a file (hex digest)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
