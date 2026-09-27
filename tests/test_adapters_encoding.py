"""Offline tests for content-type/encoding detection (charset-normalizer)."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from parsecraft.adapters import decode, parse_content_type
from parsecraft.adapters.charset_impl import detect_charset
from parsecraft.adapters.errors import AdapterError, MissingDependencyError

_LONG_LATIN1 = ("Grüße, schöne Welt! " * 8).encode("latin-1")
_LONG_TEXT = "Grüße, schöne Welt! " * 8


def test_adapter_error_is_typed_base() -> None:
    error = AdapterError("boom")
    assert isinstance(error, Exception)
    assert str(error) == "boom"


def test_parse_content_type_plain_and_params() -> None:
    assert parse_content_type("text/plain") == ("text/plain", None)
    assert parse_content_type("text/HTML; charset=UTF-8") == ("text/html", "utf-8")
    assert parse_content_type('application/pdf; charset="ISO-8859-1"') == ("application/pdf", "iso-8859-1")
    assert parse_content_type("text/plain; boundary=x; charset=UTF-8") == ("text/plain", "utf-8")


def test_parse_content_type_edge_cases() -> None:
    assert parse_content_type("") == (None, None)
    assert parse_content_type("   ") == (None, None)
    assert parse_content_type("text/plain; charset=") == ("text/plain", None)
    assert parse_content_type("text/plain; malformed") == ("text/plain", None)


def test_decode_honors_valid_declared_charset() -> None:
    detection = decode(b"hello", declared_charset="utf-8")
    assert detection.charset == "utf-8"
    assert detection.text == "hello"


def test_decode_falls_back_when_declared_charset_unknown() -> None:
    detection = decode(b"plain", declared_charset="no-such-charset")
    assert detection.text == "plain"


def test_decode_falls_back_when_declared_charset_cannot_decode() -> None:
    detection = decode("Grüße".encode("latin-1"), declared_charset="utf-8")
    assert detection.text  # best-effort decode, never raises


def test_decode_detects_utf8_input() -> None:
    detection = decode(_LONG_TEXT.encode("utf-8"))
    assert detection.text == _LONG_TEXT
    assert detection.charset


def test_decode_detects_non_utf8_input() -> None:
    detection = decode(_LONG_LATIN1)
    assert detection.text == _LONG_TEXT
    assert detection.charset


def test_decode_empty_input_uses_lib_default() -> None:
    detection = decode(b"")
    assert detection.charset
    assert detection.text == ""


def test_decode_falls_back_to_utf8_when_detection_is_undecided() -> None:
    # charset-normalizer finds no candidate for this input → utf-8 fallback path.
    detection = decode(bytes(range(256)))
    assert detection.charset == "utf-8"
    assert detection.text


def test_detect_charset_real_library_paths() -> None:
    assert detect_charset(_LONG_TEXT.encode("utf-8")) is not None
    assert detect_charset(bytes(range(256))) is None


def test_decode_missing_dependency_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr("parsecraft.adapters.encoding.import_module", raiser)
    with pytest.raises(MissingDependencyError, match="charset-normalizer is a core dependency"):
        decode(_LONG_TEXT.encode())


def test_charset_impl_module_imports_cleanly() -> None:
    # The module import itself is the contract: charset-normalizer is core.
    module = importlib.import_module("parsecraft.adapters.charset_impl")
    assert module.detect_charset(b"plain ascii text") is not None
