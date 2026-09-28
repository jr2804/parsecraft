r"""Shared ``file://`` URI resolution for source documents.

One helper, five call sites (ocr, native, liteparse, pandoc, docling) —
Windows-aware: ``file:///C:/x`` must become ``C:/x``, never ``\C:\x``
(Errno 22), and UNC form ``file://server/share`` stays a UNC path. pc-4u7.36.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

__all__ = ["path_from_file_uri"]


def path_from_file_uri(uri: str) -> Path:
    """Local path for a ``file://`` URI: percent-decoded, drive-aware, UNC-safe."""
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    netloc = parsed.netloc
    if len(netloc) >= 2 and netloc[1] == ":" and netloc[0].isalpha():
        # file://C:/x and the drive-letter spelling where the whole local path
        # lands in netloc (backslashes are not URL separators): a drive, not a host.
        path = netloc + path
    elif netloc and netloc.lower() != "localhost":
        path = f"//{netloc}{path}"  # UNC: file://server/share -> //server/share
    if len(path) >= 3 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
        path = path[1:]  # /C:/x -> C:/x (Windows drive form)
    return Path(path)
