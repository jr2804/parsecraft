"""Asset manager: pinned downloads, cache inspection/cleanup, license records.

Never fetches at import time; all networking happens inside ``AssetManager.ensure``
through the injected :class:`~parsecraft.assets.downloader.Downloader`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
    VerificationMarker,
    VerifiedFile,
    human_bytes,
)
from parsecraft.backends.protocol import ModelAssetDescriptor

_LICENSE_STORE_FILENAME = "license-acceptances.json"
#: Dot-prefixed so it cannot collide with a file a model repository ships.
_VERIFICATION_MARKER_FILENAME = ".parsecraft-verification.json"
_MARKER_RECORD_VERSION = 1
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
        self.cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
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
                        if file.is_file() and not file.name.startswith(_VERIFICATION_MARKER_FILENAME)
                    ]
                    assets.append(
                        CachedAsset(
                            model_id=model_dir.name.replace("--", "/"),
                            revision=revision_dir.name,
                            files=files,
                            total_bytes=_revision_bytes(revision_dir),
                        )
                    )
        return CacheReport(
            location=str(self.cache_dir),
            assets=assets,
            total_bytes=sum(asset.total_bytes for asset in assets),
        )

    def revision_dir(self, model_id: str, revision: str) -> Path:
        """Local directory holding one pinned revision."""
        return model_revision_dir(self.cache_dir, model_id, revision)

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

        Order of operations: marker fast path → offline check → license gate →
        disk-space check → resumable download → checksum validation.

        The fast path is what keeps a warm cache cheap: a pinned file whose size
        and mtime still match what the verification marker recorded, and whose
        recorded digest still equals the pin's, is taken as verified without
        reading it again (re-hashing 9.5 GB measured ~29 s per backend
        instantiation, and the executor builds one per page group). Any
        deviation — content changed, resized, retouched, missing, or a marker
        from another revision or another pin — falls through to the full path,
        which re-hashes everything and rewrites the marker. The license and
        disk-space gates are untouched: they still run exactly when a download is
        required.
        """
        descriptor = pin.descriptor
        model_id = descriptor.model_id
        revision = descriptor.model_revision
        target = self.revision_dir(model_id, revision)

        marker = self._read_marker(model_id, revision)
        if marker is not None and self._marker_verifies(pin, marker, target):
            return [str(target / filename) for filename in pin.filenames]

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

        verified = self._verify_checksums(pin, target)
        self._write_marker(model_id, revision, verified)
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

    # ── verification marker ────────────────────────────────────────────────

    def _marker_path(self, model_id: str, revision: str) -> Path:
        return self.revision_dir(model_id, revision) / _VERIFICATION_MARKER_FILENAME

    def _read_marker(self, model_id: str, revision: str) -> VerificationMarker | None:
        """The recorded verification for this revision, or ``None`` when unusable.

        A missing, damaged, foreign, or older-version marker is never trusted:
        the caller then verifies from scratch, which is exactly the old cost.
        """
        path = self._marker_path(model_id, revision)
        if not path.is_file():
            return None
        try:
            marker = VerificationMarker.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable or damaged JSON/payload
            return None
        if marker.record_version != _MARKER_RECORD_VERSION:
            return None
        if (marker.model_id, marker.model_revision) != (model_id, revision):
            return None
        return marker

    def _write_marker(self, model_id: str, revision: str, verified: dict[str, VerifiedFile]) -> None:
        """Record what was just verified; written atomically so a torn marker is ignored."""
        path = self._marker_path(model_id, revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = VerificationMarker(
            record_version=_MARKER_RECORD_VERSION,
            model_id=model_id,
            model_revision=revision,
            files=verified,
        )
        temporary = path.with_name(f"{path.name}.tmp")
        payload = json.dumps(marker.model_dump(mode="json"), indent=2, sort_keys=True)
        temporary.write_text(f"{payload}\n", encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _verify_checksums(pin: AssetPin, target: Path) -> dict[str, VerifiedFile]:
        """Hash every pinned file, raising on the first mismatch.

        Returns the verified stamps so the caller can record them in the marker
        without reading the files a second time.
        """
        model_id = pin.descriptor.model_id
        revision = pin.descriptor.model_revision
        verified: dict[str, VerifiedFile] = {}
        for filename in pin.filenames:
            expected = pin.expected_sha256.get(filename)
            if expected is None:
                continue
            path = target / filename
            actual = sha256_of(path)
            if actual != expected:
                raise ChecksumMismatchError(model_id, revision, filename, expected, actual)
            stat = path.stat()
            verified[filename] = VerifiedFile(sha256=actual, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        return verified

    @staticmethod
    def _marker_verifies(pin: AssetPin, marker: VerificationMarker, target: Path) -> bool:
        """Whether every pinned file is unchanged since the marker recorded it."""
        for filename in pin.filenames:
            expected = pin.expected_sha256.get(filename)
            if expected is None:
                continue  # an unpinned file carries no integrity contract to re-check
            recorded = marker.files.get(filename)
            if recorded is None or recorded.sha256 != expected:
                return False  # the marker claims something else about this pin's file
            try:
                stat = (target / filename).stat()
            except OSError:
                return False  # gone since it was verified
            if recorded.size != stat.st_size or recorded.mtime_ns != stat.st_mtime_ns:
                return False
        return True


def _dist_version_or(default: str) -> str:
    try:
        return _dist_version("parsecraft")
    except Exception:  # noqa: BLE001 - any metadata failure falls back
        return default


def default_cache_dir() -> Path:
    """The managed model cache root (``<user cache>/parsecraft/models``)."""
    return user_cache_path("parsecraft") / "models"


def model_revision_dir(cache_dir: Path, model_id: str, revision: str) -> Path:
    """Local directory holding one pinned revision under ``cache_dir``."""
    return cache_dir / slug(model_id) / revision


def slug(model_id: str) -> str:
    """Filesystem-safe directory name for a model id."""
    return model_id.replace("/", "--")


def _revision_bytes(revision_dir: Path) -> int:
    """Bytes physically stored under a revision dir, aliases excluded.

    ``CachedAssetFile`` lists only direct children (its contract), but a backend
    may nest its weights deeper — MinerU stores them under
    ``$MINERU_HOME/models/`` — so the size is aggregated over the whole tree.

    Symlinks are neither counted nor followed: ``rglob`` does not descend a
    directory symlink (a link to a huge tree cannot explode the walk) and the
    filter below drops file symlinks (a link into another revision cannot make
    that target's bytes count twice). The number is what this revision occupies,
    not what it can reach.
    """
    return sum(
        path.stat().st_size
        for path in revision_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and not path.name.startswith(_VERIFICATION_MARKER_FILENAME)
    )


def sha256_of(path: Path) -> str:
    """Streaming SHA-256 of a file (hex digest)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
