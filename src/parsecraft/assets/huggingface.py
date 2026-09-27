"""Hugging Face Hub adapter — imported only when the ``download`` extra is used.

This module is deliberately NOT imported by ``parsecraft.assets``; it is
loaded lazily via :func:`parsecraft.assets.downloader.default_downloader`.
"""

from __future__ import annotations

from huggingface_hub import hf_hub_download


class HuggingFaceDownloader:
    """Downloads files from the Hugging Face Hub via the optional extra."""

    @staticmethod
    def download(model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        path = hf_hub_download(
            repo_id=model_id,
            filename=filename,
            revision=revision,
            local_dir=dest_dir,
        )
        return str(path)
