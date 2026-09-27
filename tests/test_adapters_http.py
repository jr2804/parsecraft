"""Offline tests for the HTTP fetching adapter (web extra)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from parsecraft.adapters.errors import HttpFetchError, MissingDependencyError
from parsecraft.adapters.http import HttpResource, fetch


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "text/plain; charset=utf-8"}
    content = b"payload"


class _FakeClient:
    captured: dict[str, Any] = {}

    def __init__(self, timeout: float | None = None, **kwargs: Any) -> None:
        _FakeClient.captured = {"timeout": timeout, "kwargs": kwargs}

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    @staticmethod
    def get(url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        _FakeClient.captured["url"] = url
        _FakeClient.captured["headers"] = headers
        return _FakeResponse()


def test_http_resource_model_bounds() -> None:
    resource = HttpResource(url="http://x", status_code=204, content_type=None, body=b"")
    assert resource.status_code == 204
    with pytest.raises(ValidationError):
        HttpResource(url="", status_code=200, content_type=None, body=b"")


def test_fetch_success_via_injected_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.adapters.httpx_impl.httpx.Client", _FakeClient)
    _FakeClient.captured = {}
    resource = fetch("https://example.test/doc.txt", timeout_s=5.0, headers={"Accept": "text/plain"})
    assert resource.url == "https://example.test/doc.txt"
    assert resource.status_code == 200
    assert resource.content_type == "text/plain; charset=utf-8"
    assert resource.body == b"payload"
    assert _FakeClient.captured["timeout"] == 5.0
    assert _FakeClient.captured["headers"] == {"Accept": "text/plain"}


def test_fetch_uses_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.adapters.httpx_impl.httpx.Client", _FakeClient)
    _FakeClient.captured = {}
    fetch("https://example.test/x")
    assert _FakeClient.captured["timeout"] == 30.0


def test_fetch_transport_error_becomes_typed_failure() -> None:
    # Loopback with nothing listening: real httpx transport, no network.
    with pytest.raises(HttpFetchError, match="HTTP fetch failed"):
        fetch("http://127.0.0.1:1/never", timeout_s=2.0)


def test_fetch_missing_web_extra_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr("parsecraft.adapters.http.import_module", raiser)
    with pytest.raises(MissingDependencyError, match="web"):
        fetch("https://example.test/x")
