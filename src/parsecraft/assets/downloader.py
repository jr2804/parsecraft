"""Downloader protocol and the sanctioned lazy-load boundary.

The heavy optional dependency (``huggingface_hub``) is referenced ONLY by
:mod:`parsecraft.assets.huggingface`, which is loaded on demand via
:func:`default_downloader` — never at package import time. The GitHub adapter
has no optional dependency, so selecting it costs nothing extra.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol

from parsecraft.assets.errors import DownloaderUnavailableError
from parsecraft.assets.github import GitHubDownloader
from parsecraft.backends.protocol import ModelSource

_HUGGINGFACE_MODULE = "parsecraft.assets.huggingface"


class Downloader(Protocol):
    """Minimal typed download seam; tests inject a fake implementation."""

    def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        """Fetch one file of a pinned revision into ``dest_dir`` and return its local path."""
        ...


def downloader_for(source: ModelSource) -> Downloader:
    """The downloader an asset's declared source needs (ADR-0008 decision 11).

    One seam, two adapters: the choice is the descriptor's ``model_source``,
    never the backend's name. A GitHub-sourced asset resolves here without the
    ``download`` extra, because only the Hub adapter has an optional dependency.
    """
    if source is ModelSource.GITHUB:
        return GitHubDownloader()
    return default_downloader()


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
