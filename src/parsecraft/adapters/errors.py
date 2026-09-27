"""Typed errors for input adapters."""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for all adapter failures."""


class MissingDependencyError(AdapterError):
    """An adapter's implementation dependency is not installed."""

    def __init__(self, module: str, hint: str) -> None:
        self.module = module
        self.hint = hint
        super().__init__(f"adapter dependency {module!r} is not available — {hint}")


class HttpFetchError(AdapterError):
    """An HTTP request failed at the transport level."""

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        self.detail = detail
        super().__init__(f"HTTP fetch failed for {url!r}: {detail}")
