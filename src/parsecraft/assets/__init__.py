"""Model-asset manager: pinned downloads, cache, license records.

Optional ``download`` extra (``huggingface-hub``) provides the real
downloader; the manager itself is dependency-light and import-clean offline.
"""

from __future__ import annotations

from parsecraft.assets.downloader import Downloader, default_downloader, downloader_for
from parsecraft.assets.errors import (
    AssetError,
    ChecksumMismatchError,
    DownloaderUnavailableError,
    InsufficientDiskSpaceError,
    LicenseNotAcceptedError,
    OfflineModeError,
)
from parsecraft.assets.github import GitHubDownloader
from parsecraft.assets.manager import (
    AssetManager,
    default_cache_dir,
    model_revision_dir,
    sha256_of,
    slug,
)
from parsecraft.assets.models import (
    AssetPin,
    CachedAsset,
    CachedAssetFile,
    CacheReport,
    LicenseAcceptance,
    human_bytes,
)
from parsecraft.backends.protocol import ModelSource

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
    "GitHubDownloader",
    "InsufficientDiskSpaceError",
    "LicenseAcceptance",
    "LicenseNotAcceptedError",
    "ModelSource",
    "OfflineModeError",
    "default_cache_dir",
    "default_downloader",
    "downloader_for",
    "human_bytes",
    "model_revision_dir",
    "sha256_of",
    "slug",
]
