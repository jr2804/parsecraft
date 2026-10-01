"""TypeSafe System One / Jev judge provider: spec contract, ranking, typed failures.

Fully offline: the ``typesafe_sdk`` package is a stub in ``sys.modules`` (the
provider imports it through ``import_module``), and the credential comes from a
patched environment. The live Jev tier is marked ``judge`` and additionally
skips without ``TYPESAFE_API_KEY`` — no key exists in CI, which is expected.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
from parsecraft.environment.probe import EXTRA_IMPORTS
from parsecraft.providers import systemone as provider
from parsecraft.routing import Intent
from parsecraft.routing.judge import JudgeSpec, RoutingJudge
from parsecraft.routing.judge_providers import (
    JudgeProviderLoadError,
    JudgeProviderUnavailableError,
    JudgeSpecError,
    resolve_judge,
)
from parsecraft.routing.models import RoutingError

_API_KEY = "ts-test-key"
_MODEL = "jev"
_QUESTION_ID = "lead"
_NATIVE = BackendDescriptor(
    name="native-pdf",
    capabilities=BackendCapabilities(supported_formats=["application/pdf"], requires_gpu=False),
)
_OCR = BackendDescriptor(
    name="ocr-ovis",
    capabilities=BackendCapabilities(
        supported_formats=["application/pdf", "image/png"],
        requires_gpu=True,
        estimated_vram_gb=6.0,
        optional_dependency_group="ocr-ovis",
    ),
)


# ── Stub: the `typesafe_sdk` package, faked through sys.modules ──────────────────


class _Answer:
    """Stand-in for the SDK's `ChoiceAnswer`."""

    def __init__(self, *, choice: str, probabilities: dict[str, object], confidence: float = 1.0) -> None:
        self.choice = choice
        self.probabilities = probabilities
        self.confidence = confidence


class _Response:
    """Stand-in for the SDK's `SystemOneResponse` (`.choices` keyed by question id)."""

    def __init__(self, *, choices: dict[str, _Answer] | None = None, model: str = "jev-1.13.0") -> None:
        self.model = model
        self.choices = choices or {}


class _Client:
    """Stand-in for `TypeSafeClient`: a context manager with one `system_one` call."""

    def __init__(self, stub: _SdkStub, *, api_key: str, model: str) -> None:
        self._stub = stub
        self.api_key = api_key
        self.model = model

    def __enter__(self) -> _Client:
        self._stub.entered += 1
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._stub.exited += 1

    def system_one(self, state: dict[str, object], questions: dict[str, object]) -> _Response:
        self._stub.calls.append((state, questions))
        if self._stub.error is not None:
            raise self._stub.error
        return self._stub.response


class _SdkStub:
    """Fake ``typesafe_sdk`` module: scripted answer, recorded calls."""

    def __init__(self) -> None:
        self.response = _Response(choices={_QUESTION_ID: _Answer(choice="ocr-ovis", probabilities={"ocr-ovis": 1.0})})
        self.error: Exception | None = None
        self.calls: list[tuple[dict[str, object], dict[str, object]]] = []
        self.clients: list[_Client] = []
        self.choices: list[tuple[str, dict[str, object]]] = []
        self.entered = 0
        self.exited = 0

    def TypeSafeClient(self, *, api_key: str, model: str) -> _Client:
        client = _Client(self, api_key=api_key, model=model)
        self.clients.append(client)
        return client

    def Choice(self, *, instructions: str, criteria: dict[str, object]) -> dict[str, object]:
        self.choices.append((instructions, criteria))
        return {"type": "choice", "instructions": instructions, "criteria": criteria}


# ── load_judge: spec and credential contract ─────────────────────────────────────


def test_load_judge_returns_a_routing_judge(stub: _SdkStub) -> None:
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    assert isinstance(judge, RoutingJudge)
    assert isinstance(judge, provider.JevJudge)


def test_resolve_judge_loads_the_provider_lazily(stub: _SdkStub) -> None:
    """The spec string path resolves through `parsecraft.providers.systemone`."""
    assert isinstance(resolve_judge("systemone/jev"), provider.JevJudge)


def test_variant_is_rejected_not_silently_ignored(stub: _SdkStub) -> None:
    with pytest.raises(JudgeSpecError, match="does not accept a variant"):
        provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL, variant="fast"))
    assert stub.clients == []


def test_missing_extra_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("typesafe_sdk")

    monkeypatch.setattr(provider, "import_module", _raise)
    with pytest.raises(JudgeProviderUnavailableError, match="'systemone' extra") as excinfo:
        provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    assert (excinfo.value.provider, excinfo.value.module_name) == ("systemone", "typesafe_sdk")


def test_missing_api_key_fails_at_resolution(monkeypatch: pytest.MonkeyPatch, stub: _SdkStub) -> None:
    """No key: a typed resolution failure, before any client is built (CLI exit 1)."""
    monkeypatch.delenv(provider._API_KEY_ENV, raising=False)
    with pytest.raises(JudgeProviderUnavailableError, match="TYPESAFE_API_KEY") as excinfo:
        resolve_judge("systemone/jev")
    assert excinfo.value.provider == "systemone"
    assert stub.clients == []  # never reached the SDK


def test_module_without_the_sdk_surface_is_a_load_error(monkeypatch: pytest.MonkeyPatch, stub: _SdkStub) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", importlib)
    with pytest.raises(JudgeProviderLoadError, match="does not export TypeSafeClient/Choice"):
        provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))


def test_extra_imports_map_declares_the_systemone_group() -> None:
    """The environment probe detects the extra through the SDK import (environment/AGENTS.md)."""
    assert EXTRA_IMPORTS["systemone"] == ("typesafe_sdk",)


# ── rank: the choice distribution is the ranking ─────────────────────────────────


def test_rank_orders_candidates_by_the_choice_distribution(stub: _SdkStub) -> None:
    stub.response = _Response(
        choices={
            _QUESTION_ID: _Answer(
                choice="native-pdf",
                probabilities={"ocr-ovis": 0.2, "native-pdf": 0.7, "native-text": 0.1},
            )
        }
    )
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    candidates = [_OCR, _NATIVE]
    assert list(judge.rank(Intent.NATIVE, candidates)) == ["native-pdf", "ocr-ovis"]
    assert stub.clients[0].api_key == _API_KEY
    assert stub.clients[0].model == _MODEL


def test_rank_keeps_planner_order_for_ties_and_non_numeric_probabilities(stub: _SdkStub) -> None:
    """Equal, boolean, string, and omitted probabilities all score 0.0 — never a guess."""
    stub.response = _Response(choices={_QUESTION_ID: _Answer(choice="alpha", probabilities={"alpha": 0.5, "beta": True, "gamma": "0.9"})})
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    candidates = [_descriptor("alpha"), _descriptor("beta"), _descriptor("gamma"), _descriptor("delta")]
    assert list(judge.rank(Intent.OCR_GENERAL, candidates)) == ["alpha", "beta", "gamma", "delta"]


def test_rank_memoizes_identical_page_shapes(stub: _SdkStub) -> None:
    """One verdict per (intent, candidate set) shape — not one request per page."""
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    first = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    second = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    assert first == second
    assert first is not second  # callers get a copy, never the memo itself
    assert len(stub.calls) == 1
    assert len(stub.clients) == 1


def test_rank_does_not_share_a_verdict_across_shapes(stub: _SdkStub) -> None:
    """The memo key carries both the intent and the candidate names."""
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])  # same candidates, other intent
    judge.rank(Intent.NATIVE, [_OCR, _descriptor("native-text", ocr=False)])  # other candidate set
    judge.rank(Intent.NATIVE, [_OCR])  # one candidate: short-circuits, no request
    assert len(stub.calls) == 3


def test_rank_sends_one_bounded_choice_question(stub: _SdkStub) -> None:
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    judge.rank(Intent.OCR_VISION, [_OCR, _NATIVE])
    assert len(stub.calls) == 1
    assert len(stub.clients) == 1
    assert len(stub.choices) == 1
    state, questions = stub.calls[0]
    assert state == {"intent": "ocr-vision", "candidates": ["ocr-ovis", "native-pdf"]}
    assert set(questions) == {_QUESTION_ID}
    instructions, criteria = stub.choices[0]
    assert "`intent`" in instructions
    assert set(criteria) == {"ocr-ovis", "native-pdf"}
    assert criteria["ocr-ovis"] == {
        "family": "ocr",
        "requires_gpu": True,
        "estimated_vram_gb": 6.0,
        "formats": ["application/pdf", "image/png"],
    }
    assert criteria["native-pdf"] == {
        "family": "native",
        "requires_gpu": False,
        "estimated_vram_gb": None,
        "formats": ["application/pdf"],
    }


def test_rank_short_circuits_a_single_candidate(stub: _SdkStub) -> None:
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    assert list(judge.rank(Intent.NATIVE, [_NATIVE])) == ["native-pdf"]
    assert stub.calls == []  # nothing to rank, no request spent
    assert stub.clients == []


def test_rank_wraps_sdk_failures_as_a_typed_error(stub: _SdkStub) -> None:
    stub.error = RuntimeError("api key rejected upstream")
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    with pytest.raises(provider.JevJudgeError, match="RuntimeError: api key rejected upstream") as excinfo:
        judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    assert isinstance(excinfo.value, RoutingError)
    assert stub.exited == 1  # the client was closed even on failure


def test_rank_without_the_choice_answer_is_a_typed_error(stub: _SdkStub) -> None:
    stub.response = _Response(choices={})
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    with pytest.raises(provider.JevJudgeError, match="no choice for question 'lead'"):
        judge.rank(Intent.NATIVE, [_OCR, _NATIVE])


def test_rank_is_deterministic_and_never_invents_a_candidate(stub: _SdkStub) -> None:
    stub.response = _Response(choices={_QUESTION_ID: _Answer(choice="ghost", probabilities={"ghost": 1.0, "native-pdf": 0.4, "ocr-ovis": 0.6})})
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    first = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    second = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    assert first == second == ["ocr-ovis", "native-pdf"]
    assert "ghost" not in first  # an option the planner never offered is dropped


def test_client_is_closed_after_each_request(stub: _SdkStub) -> None:
    """Every request opens and closes its own client (a memoized shape opens none)."""
    judge = provider.load_judge(JudgeSpec(provider="systemone", model=_MODEL))
    judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])  # a distinct shape: a second request
    assert (stub.entered, stub.exited) == (2, 2)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _SdkStub:
    """Install the fake SDK module and a credential in the environment."""
    instance = _SdkStub()
    monkeypatch.setitem(sys.modules, "typesafe_sdk", instance)
    monkeypatch.setenv(provider._API_KEY_ENV, _API_KEY)
    return instance


# ── Live: the real System One endpoint (opt-in, needs the extra AND a key) ───────


@pytest.mark.judge
def test_live_jev_ranks_real_candidates() -> None:
    """Ground truth for the Choice-as-ranking contract, against the live API.

    Skips without the `systemone` extra or `TYPESAFE_API_KEY`; run with
    `mise run test-judge`.
    """
    pytest.importorskip("typesafe_sdk")
    if not os.environ.get(provider._API_KEY_ENV):
        pytest.skip(f"{provider._API_KEY_ENV} is not set")
    judge = resolve_judge("systemone/jev")
    candidates = [_descriptor("ocr-ovis"), _descriptor("ocr-tele"), _descriptor("native-pdf", ocr=False)]
    order = list(judge.rank(Intent.OCR_GENERAL, candidates))
    assert sorted(order) == sorted(descriptor.name for descriptor in candidates)
    assert len(order) == len(set(order))  # best-first, each candidate exactly once


def _descriptor(name: str, *, ocr: bool = True) -> BackendDescriptor:
    group = "ocr-ovis" if ocr else None
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(
            supported_formats=["application/pdf"],
            requires_gpu=ocr,
            estimated_vram_gb=6.0 if ocr else None,
            optional_dependency_group=group,
        ),
    )
