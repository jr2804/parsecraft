"""URL fetching adapter (``web`` extra) — content-type aware input source.

The httpx implementation module is loaded via ``import_module`` only when
:func:`fetch` is called; importing this module never requires ``httpx``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol, cast

from pydantic import BaseModel, Field

from parsecraft.adapters.errors import MissingDependencyError

_HTTPX_IMPL_MODULE = "parsecraft.adapters.httpx_impl"

_HTTP_HINT = "install the 'web' extra (pip install parsecraft[web])"

DEFAULT_TIMEOUT_S = 30.0


class HttpResource(BaseModel):
    """One fetched HTTP resource: metadata plus raw bytes."""

    url: str = Field(min_length=1)
    status_code: int = Field(ge=0)
    content_type: str | None = None
    body: bytes


class _HttpImpl(Protocol):
    """What the HTTP adapter needs from the httpx module."""

    def request(self, url: str, timeout_s: float, headers: dict[str, str] | None) -> tuple[int, str | None, bytes]: ...


def fetch(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S, headers: dict[str, str] | None = None) -> HttpResource:
    """GET ``url`` and return a typed :class:`HttpResource` (no decoding)."""
    try:
        impl = cast(_HttpImpl, import_module(_HTTPX_IMPL_MODULE))
    except ImportError as exc:
        raise MissingDependencyError(_HTTPX_IMPL_MODULE, _HTTP_HINT) from exc
    status_code, content_type, body = impl.request(url, timeout_s, headers)
    return HttpResource(url=url, status_code=status_code, content_type=content_type, body=body)
