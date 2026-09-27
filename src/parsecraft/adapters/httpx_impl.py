"""HTTP fetching via httpx (``web`` extra) — the raw transport layer.

Loaded only through :func:`parsecraft.adapters.http`'s ``import_module``
seam; importing this module requires the ``web`` extra to be installed.
"""

from __future__ import annotations

import httpx

from parsecraft.adapters.errors import HttpFetchError


def request(url: str, timeout_s: float, headers: dict[str, str] | None) -> tuple[int, str | None, bytes]:
    """GET ``url``; return ``(status_code, content_type, body)``."""
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise HttpFetchError(url, str(exc)) from exc
    return response.status_code, response.headers.get("content-type"), response.content
