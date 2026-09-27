"""Downloader protocol and the sanctioned lazy-load boundary.

The heavy optional dependency (``huggingface_hub``) is referenced ONLY by
:mod:`parsecraft.assets.huggingface`, which is loaded on demand via
:func:`default_downloader` — never at package import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol

from parsecraft.assets.errors import DownloaderUnavailableError

_HUGGINGFACE_MODULE = "parsecraft.assets.huggingface"


class Downloader(Protocol):
    """Minimal typed download seam; tests inject a fake implementation."""

    def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        """Fetch one file of a pinned revision into ``dest_dir`` and return its local path."""
        ...


def default_downloader() -> Downloader:
    """Lazily load the Hugging Face adapter (requires the ``download`` extra)."""
    try:
        module = import_module(_HUGGINGFACE_MODULE)
    except ImportError as exc:
        raise DownloaderUnavailableError(
            detail="the 'download' extra is required (pip install parsecraft[download])",
        ) from exc
    downloader: Downloader = module.HuggingFaceDownloader()
    return downloader
