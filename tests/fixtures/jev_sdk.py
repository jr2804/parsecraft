"""Fake ``typesafe_sdk`` for offline System One provider tests.

The providers import their SDK through ``importlib`` at ``load_judge`` time, so
installing this stub in ``sys.modules`` exercises the real provider code with no
network and no optional dependency. It mirrors the surface the providers use:
``TypeSafeClient`` (keyword-only ctor, a context manager with one
``system_one`` call), ``Choice``, and the response shape
(``.choices[<question>]`` with ``choice``/``probabilities``/``confidence``).

``StubSdk`` records every constructor kwargs dict, so tests can assert the
per-endpoint injection (``base_url``, ``api_key``, ``model``) that distinguishes
the cloud, Zen, and Ollama endpoints.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest


class StubAnswer:
    """Stand-in for the SDK's ``ChoiceAnswer``."""

    def __init__(self, *, choice: str, probabilities: dict[str, object], confidence: float = 1.0) -> None:
        self.choice = choice
        self.probabilities = probabilities
        self.confidence = confidence


class StubResponse:
    """Stand-in for the SDK's ``SystemOneResponse`` (``.choices`` keyed by question id)."""

    def __init__(self, *, choices: dict[str, StubAnswer] | None = None, model: str = "jev-1.13") -> None:
        self.model = model
        self.choices = choices or {}


class StubClient:
    """Stand-in for ``TypeSafeClient``: a context manager with one ``system_one`` call."""

    def __init__(self, stub: StubSdk, kwargs: dict[str, Any]) -> None:
        self._stub = stub
        self._kwargs = kwargs

    def __enter__(self) -> StubClient:
        self._stub.entered += 1
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._stub.exited += 1

    def system_one(self, state: dict[str, object], questions: dict[str, object]) -> StubResponse:
        self._stub.calls.append((state, questions))
        if self._stub.error is not None:
            raise self._stub.error
        return self._stub.response


class StubSdk:
    """The fake module object: scripted answer, recorded calls and constructor kwargs."""

    def __init__(self) -> None:
        self.response = StubResponse(
            choices={"lead": StubAnswer(choice="ocr-ovis", probabilities={"ocr-ovis": 1.0})},
        )
        self.error: Exception | None = None
        #: ``(state, questions)`` per ``system_one`` call.
        self.calls: list[tuple[dict[str, object], dict[str, object]]] = []
        #: Constructor kwargs per ``TypeSafeClient`` call — the endpoint binding.
        self.client_kwargs: list[dict[str, Any]] = []
        #: ``(instructions, criteria)`` per ``Choice`` call.
        self.choices: list[tuple[str, dict[str, object]]] = []
        self.entered = 0
        self.exited = 0

    def TypeSafeClient(self, **kwargs: Any) -> StubClient:
        self.client_kwargs.append(kwargs)
        return StubClient(self, kwargs)

    def Choice(self, *, instructions: str, criteria: dict[str, object]) -> dict[str, object]:
        self.choices.append((instructions, criteria))
        return {"type": "choice", "instructions": instructions, "criteria": criteria}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> StubSdk:
        """Install as ``typesafe_sdk`` for the duration of a test."""
        monkeypatch.setitem(sys.modules, "typesafe_sdk", self)
        return self
