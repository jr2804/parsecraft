"""GitHub adapter — stdlib only, so a GitHub-sourced asset needs no extra.

This module is deliberately NOT imported by ``parsecraft.assets``; it is
selected by :func:`parsecraft.assets.downloader.downloader_for` when a
descriptor declares ``model_source="github"`` (ADR-0008 decision 11). Unlike
:mod:`parsecraft.assets.huggingface` it has no optional dependency, so the
import is eager rather than lazy — which is what lets a GitHub-sourced asset be
installed without the ``download`` extra.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from urllib.parse import quote

from parsecraft.assets.errors import AssetError

#: Raw-file endpoint. The scheme is fixed here, so a descriptor can never steer
#: the fetch to another protocol. Each path segment is percent-encoded
#: separately, which keeps a repo id's ``/`` separators intact.
_RAW_URL = "https://raw.githubusercontent.com/{model_id}/{revision}/{filename}"

#: Socket timeout in seconds. This is a per-operation timeout, not a budget for
#: the whole transfer: a slow but progressing download is never cut off, while a
#: host that accepts and then stalls fails instead of hanging the conversion.
_SOCKET_TIMEOUT_S = 30.0


class GitHubDownloader:
    """Fetches pinned files from a git host over the stdlib.

    Writes through a temporary file and renames it into place, so an
    interrupted transfer can never leave a truncated file that a later
    verification would hash and record.
    """

    @staticmethod
    def download(model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        """Fetch one file of a pinned revision into ``dest_dir``; return its path."""
        url = _RAW_URL.format(
            model_id="/".join(quote(part, safe="") for part in model_id.split("/")),
            revision=quote(revision, safe=""),
            filename=quote(filename, safe=""),
        )
        # Containment is judged on resolved paths, but the file is written and
        # reported through the caller's own string: ``resolve()`` normalizes
        # case on Windows, so resolving the result would hand back a different
        # spelling than the caller passed in.
        base = Path(dest_dir)
        root = base.resolve()
        target = base / filename
        if not target.resolve().is_relative_to(root):
            raise AssetError(model_id, revision, f"pinned path escapes the cache directory: {filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.part")
        try:
            with urllib.request.urlopen(url, timeout=_SOCKET_TIMEOUT_S) as response:  # noqa: S310 - https fixed above
                temporary.write_bytes(response.read())
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise AssetError(model_id, revision, f"could not download {filename!r} from {url}: {exc}") from exc
        return str(target)
