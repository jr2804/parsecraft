"""Model-asset manager: pinned downloads, cache, license records.

Optional ``download`` extra (``huggingface-hub``) provides the real
downloader; the manager itself is dependency-light and import-clean offline.
"""

from __future__ import annotations

from parsecraft.assets.downloader import Downloader, default_downloader
from parsecraft.assets.errors import (
    AssetError,
    ChecksumMismatchError,
    DownloaderUnavailableError,
    InsufficientDiskSpaceError,
    LicenseNotAcceptedError,
    OfflineModeError,
)
from parsecraft.assets.manager import AssetManager, sha256_of, slug
from parsecraft.assets.models import (
    AssetPin,
    CachedAsset,
    CachedAssetFile,
    CacheReport,
    LicenseAcceptance,
)

__all__ = [
    "AssetError",
    "AssetManager",
    "AssetPin",
    "CacheReport",
    "CachedAsset",
    "CachedAssetFile",
    "ChecksumMismatchError",
    "Downloader",
    "DownloaderUnavailableError",
    "InsufficientDiskSpaceError",
    "LicenseAcceptance",
    "LicenseNotAcceptedError",
    "OfflineModeError",
    "default_downloader",
    "sha256_of",
    "slug",
]
