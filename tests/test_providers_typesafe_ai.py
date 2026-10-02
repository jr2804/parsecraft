"""Offline tests for the TypeSafe cloud System One provider (``typesafe-ai/<model>``).

The live tier uses ``jev-latest``, the id the cloud actually serves (a bare
``jev`` answers ``400 Unknown model``); the offline tests use a stub model token
because nothing reaches the cloud.

The SDK is a stub in ``sys.modules`` (``tests/fixtures/jev_sdk.py``), so the real
provider code runs with no network and no optional dependency. The live tier is
marked ``judge`` and skips without ``TYPESAFE_API_KEY`` — no key exists in CI.
"""

from __future__ import annotations

import os
import sys
from typing import cast

import pytest

from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
from parsecraft.providers import _jev
from parsecraft.providers import typesafe_ai as provider
from parsecraft.routing import Intent, RoutingPreference
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge
from parsecraft.routing.judge_providers import (
    JudgeProviderUnavailableError,
    JudgeSpecError,
    resolve_judge,
)
from parsecraft.routing.models import RoutingError
from tests.fixtures.jev_sdk import StubAnswer, StubResponse, StubSdk

_API_KEY = "ts-test-key"
#: The credential env var the endpoint declares — the tests never hardcode it twice.
assert provider.PROFILE.api_key_env is not None  # a None here means the declared contract changed
_TYPESAFE_KEY_ENV: str = provider.PROFILE.api_key_env
_MODEL = "jev"
_QUESTION_ID = "lead"
_NATIVE = BackendDescriptor(
    name="native-pdf",
    capabilities=BackendCapabilities(supported_formats=["application/pdf"], gpu_requirement=0.0),
)
_OCR = BackendDescriptor(
    name="ocr-ovis",
    capabilities=BackendCapabilities(
        supported_formats=["application/pdf", "image/png"],
        gpu_requirement=1.0,
        estimated_vram_gb=6.0,
        optional_dependency_group="ocr-ovis",
    ),
)


# ── load_judge: spec, credential, extra ──────────────────────────────────────


def test_load_judge_returns_the_shared_jev_judge(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    assert isinstance(judge, RoutingJudge)
    assert isinstance(judge, _jev.JevJudge)


def test_resolve_judge_loads_the_provider_lazily(jev_sdk: StubSdk, ts_key: str) -> None:
    assert isinstance(resolve_judge("typesafe-ai/jev"), _jev.JevJudge)


def test_endpoint_binding_uses_the_sdk_default_base_url(jev_sdk: StubSdk, ts_key: str) -> None:
    """The cloud endpoint passes no base_url of its own, so the SDK default applies."""
    judge = cast(_jev.JevJudge, provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL)))
    judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    assert jev_sdk.client_kwargs == [{"api_key": _API_KEY, "model": _MODEL, "base_url": None}]


def test_variant_is_rejected_not_silently_ignored(jev_sdk: StubSdk) -> None:
    with pytest.raises(JudgeSpecError, match="does not accept a variant"):
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL, variant="fast"))
    assert jev_sdk.client_kwargs == []


def test_spec_error_outranks_a_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller bug must not hide behind environment state (CLI exit 2 before exit 1).

    No key is planted and no SDK stub is installed: the spec check runs first, so
    the unsupported variant is what the caller hears about.
    """
    monkeypatch.delenv(_TYPESAFE_KEY_ENV, raising=False)
    with pytest.raises(JudgeSpecError, match="does not accept a variant"):
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL, variant="fast"))


def test_good_spec_without_a_credential_is_unavailable(monkeypatch: pytest.MonkeyPatch, jev_sdk: StubSdk) -> None:
    """The mirror case: a valid spec plus no key is environment state (exit 1)."""
    monkeypatch.delenv(_TYPESAFE_KEY_ENV, raising=False)
    with pytest.raises(JudgeProviderUnavailableError, match="TYPESAFE_API_KEY") as excinfo:
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    assert excinfo.value.provider == "typesafe-ai"
    assert jev_sdk.client_kwargs == []  # never reached the SDK


def test_missing_extra_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("typesafe_sdk")

    monkeypatch.setattr(_jev, "import_module", _raise)
    with pytest.raises(JudgeProviderUnavailableError, match="'systemone' extra") as excinfo:
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    assert (excinfo.value.provider, excinfo.value.module_name) == ("typesafe-ai", "typesafe_sdk")


def test_module_without_the_sdk_surface_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", object())
    with pytest.raises(JudgeProviderUnavailableError, match="does not export TypeSafeClient/Choice"):
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))


def test_missing_api_key_fails_at_resolution(monkeypatch: pytest.MonkeyPatch, jev_sdk: StubSdk) -> None:
    """No key: a typed resolution failure, before any client is built (CLI exit 1)."""
    monkeypatch.delenv(_TYPESAFE_KEY_ENV, raising=False)
    with pytest.raises(JudgeProviderUnavailableError, match="TYPESAFE_API_KEY") as excinfo:
        resolve_judge("typesafe-ai/jev")
    assert excinfo.value.provider == "typesafe-ai"
    assert jev_sdk.client_kwargs == []  # never reached the SDK


def test_blank_api_key_is_treated_as_missing(monkeypatch: pytest.MonkeyPatch, jev_sdk: StubSdk) -> None:
    monkeypatch.setenv(_TYPESAFE_KEY_ENV, "   ")
    with pytest.raises(JudgeProviderUnavailableError, match="TYPESAFE_API_KEY"):
        provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))


# ── rank: the choice distribution is the ranking ─────────────────────────────


def test_rank_orders_candidates_by_the_choice_distribution(jev_sdk: StubSdk, ts_key: str) -> None:
    jev_sdk.response = StubResponse(
        choices={_QUESTION_ID: StubAnswer(choice="native-pdf", probabilities={"ocr-ovis": 0.2, "native-pdf": 0.7})},
    )
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    assert list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE])) == ["native-pdf", "ocr-ovis"]


def test_rank_keeps_planner_order_for_ties_and_non_numeric_probabilities(jev_sdk: StubSdk, ts_key: str) -> None:
    """Equal, boolean, string, and omitted probabilities all score 0.0 — never a guess."""
    jev_sdk.response = StubResponse(
        choices={_QUESTION_ID: StubAnswer(choice="alpha", probabilities={"alpha": 0.5, "beta": True, "gamma": "0.9"})},
    )
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    candidates = [_descriptor("alpha"), _descriptor("beta"), _descriptor("gamma"), _descriptor("delta")]
    assert list(judge.rank(Intent.OCR_GENERAL, candidates)) == ["alpha", "beta", "gamma", "delta"]


def test_rank_sends_one_bounded_choice_question(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    judge.rank(Intent.OCR_VISION, [_OCR, _NATIVE])
    assert (len(jev_sdk.calls), len(jev_sdk.client_kwargs), len(jev_sdk.choices)) == (1, 1, 1)
    state, questions = jev_sdk.calls[0]
    assert state == {
        "intent": "ocr-vision",
        "preference": "balanced",
        "candidates": {
            "ocr-ovis": {"family": "ocr", "gpu_requirement": 1.0, "estimated_vram_gb": 6.0, "formats": ["application/pdf", "image/png"]},
            "native-pdf": {"family": "native", "gpu_requirement": 0.0, "estimated_vram_gb": None, "formats": ["application/pdf"]},
        },
    }
    assert set(questions) == {_QUESTION_ID}
    instructions, criteria = jev_sdk.choices[0]
    assert "`state`" in instructions
    assert criteria == {
        "ocr-ovis": "OCR backend; requires ~6 GB VRAM; handles application/pdf, image/png",
        "native-pdf": "native backend; runs on CPU; handles application/pdf",
    }


def test_rank_carries_the_preference_verbatim_in_state_and_instructions(jev_sdk: StubSdk, ts_key: str) -> None:
    """The preference steers the question AND is inspectable in the payload."""
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL), None, RoutingPreference.QUALITY)
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])
    state, _questions = jev_sdk.calls[0]
    assert state["preference"] == "quality"  # verbatim, not a repr or an ordinal
    instructions, _criteria = jev_sdk.choices[0]
    assert "`quality`" in instructions
    assert "most capable" in instructions


def test_rank_defaults_the_preference_to_balanced(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])
    state, _questions = jev_sdk.calls[0]
    assert state["preference"] == RoutingPreference.BALANCED.value == "balanced"
    instructions, _criteria = jev_sdk.choices[0]
    assert "`balanced`" in instructions


def test_rank_reports_the_host_budget_when_known(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL), MachineProfile(vram_budget_gb=32.0, gpu_usable=True))
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])
    state, _ = jev_sdk.calls[0]
    # The runtime fact travels with the budget: 32 GB nobody can use is not a host
    # a GPU-only candidate should lead on.
    assert cast("dict[str, object]", state["machine"]) == {"vram_budget_gb": 32.0, "gpu_usable": True}


def test_rank_reports_an_unusable_gpu_as_such(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL), MachineProfile(vram_budget_gb=8.0))
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])
    state, _ = jev_sdk.calls[0]
    assert cast("dict[str, object]", state["machine"]) == {"vram_budget_gb": 8.0, "gpu_usable": False}


@pytest.mark.parametrize(
    ("requirement", "vram", "expected"),
    [
        (1.0, 4.5, "requires ~4.5 GB VRAM"),
        (0.5, 2.0, "runs on CPU or GPU"),
        (0.0, None, "runs on CPU"),
    ],
)
def test_choice_labels_describe_the_gpu_scale(requirement: float, vram: float | None, expected: str) -> None:
    """The labels the endpoint sees explain the three scale anchors."""
    descriptor = BackendDescriptor(
        name="candidate",
        capabilities=BackendCapabilities(
            supported_formats=["application/pdf"],
            gpu_requirement=requirement,
            estimated_vram_gb=vram,
        ),
    )
    assert expected in _jev._sentence(descriptor)


def test_rank_omits_the_machine_key_when_the_host_is_unknown(jev_sdk: StubSdk, ts_key: str) -> None:
    """An unprobed host contributes no machine key — never a misleading zero."""
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])
    state, _ = jev_sdk.calls[0]
    assert "machine" not in state


def test_rank_short_circuits_a_single_candidate(jev_sdk: StubSdk, ts_key: str) -> None:
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    assert list(judge.rank(Intent.NATIVE, [_NATIVE])) == ["native-pdf"]
    assert jev_sdk.calls == []
    assert jev_sdk.client_kwargs == []


def test_rank_wraps_sdk_failures_as_a_typed_error(jev_sdk: StubSdk, ts_key: str) -> None:
    jev_sdk.error = RuntimeError("api key rejected upstream")
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    with pytest.raises(_jev.JevJudgeError, match="RuntimeError: api key rejected upstream") as excinfo:
        judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    assert isinstance(excinfo.value, RoutingError)
    assert jev_sdk.exited == 1  # the client was closed even on failure


def test_rank_without_the_choice_answer_is_a_typed_error(jev_sdk: StubSdk, ts_key: str) -> None:
    jev_sdk.response = StubResponse(choices={})
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    with pytest.raises(_jev.JevJudgeError, match="no choice for question 'lead'"):
        judge.rank(Intent.NATIVE, [_OCR, _NATIVE])


def test_rank_is_deterministic_and_never_invents_a_candidate(jev_sdk: StubSdk, ts_key: str) -> None:
    jev_sdk.response = StubResponse(
        choices={_QUESTION_ID: StubAnswer(choice="ghost", probabilities={"ghost": 1.0, "native-pdf": 0.4, "ocr-ovis": 0.6})},
    )
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    first = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    second = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    assert first == second == ["ocr-ovis", "native-pdf"]
    assert "ghost" not in first  # an option the planner never offered is dropped


# ── memo ────────────────────────────────────────────────────────────────────


def test_rank_memoizes_identical_page_shapes(jev_sdk: StubSdk, ts_key: str) -> None:
    """One verdict per (intent, candidate set) shape — not one request per page."""
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    first = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    second = list(judge.rank(Intent.NATIVE, [_OCR, _NATIVE]))
    assert first == second
    assert first is not second  # callers get a copy, never the memo itself
    assert len(jev_sdk.calls) == 1
    assert len(jev_sdk.client_kwargs) == 1


def test_rank_does_not_share_a_verdict_across_shapes(jev_sdk: StubSdk, ts_key: str) -> None:
    """The memo key carries both the intent and the candidate names."""
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])  # same candidates, other intent
    judge.rank(Intent.NATIVE, [_OCR, _descriptor("native-text", ocr=False)])  # other candidate set
    judge.rank(Intent.NATIVE, [_OCR])  # one candidate: short-circuits, no request
    assert len(jev_sdk.calls) == 3


def test_client_is_closed_after_each_request(jev_sdk: StubSdk, ts_key: str) -> None:
    """Every request opens and closes its own client (a memoized shape opens none)."""
    judge = provider.load_judge(JudgeSpec(provider="typesafe-ai", model=_MODEL))
    judge.rank(Intent.NATIVE, [_OCR, _NATIVE])
    judge.rank(Intent.OCR_GENERAL, [_OCR, _NATIVE])  # a distinct shape: a second request
    assert (jev_sdk.entered, jev_sdk.exited) == (2, 2)


@pytest.fixture
def ts_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Plant the cloud credential for the offline tests that need one.

    Deliberately *not* autouse: the live tier must read the real environment, and
    a planted key would both make its skip unreachable (the endpoint answers 401
    without one) and mask a real key when the operator set one.
    """
    monkeypatch.setenv(_TYPESAFE_KEY_ENV, _API_KEY)
    return _API_KEY


# ── Live: the real TypeSafe cloud (opt-in, needs the extra AND a key) ─────────


@pytest.mark.judge
def test_live_typesafe_ranks_real_candidates() -> None:
    """Ground truth for the Choice-as-ranking contract, against the live API.

    Skips without the `systemone` extra or `TYPESAFE_API_KEY`; run with
    `mise run test-judge`.
    """
    pytest.importorskip("typesafe_sdk")
    if not os.environ.get(_TYPESAFE_KEY_ENV):  # the REAL environment: no fixture plants a key here
        pytest.skip(f"{_TYPESAFE_KEY_ENV} is not set")
    judge = resolve_judge("typesafe-ai/jev-latest", machine=MachineProfile(vram_budget_gb=0.0))
    candidates = [_descriptor("ocr-ovis"), _descriptor("ocr-tele", ocr=False), _descriptor("native-pdf", ocr=False)]
    order = list(judge.rank(Intent.OCR_GENERAL, candidates))
    assert sorted(order) == sorted(descriptor.name for descriptor in candidates)
    assert len(order) == len(set(order))  # best-first, each candidate exactly once


def _descriptor(name: str, *, ocr: bool = True) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(
            supported_formats=["application/pdf"],
            gpu_requirement=1.0 if ocr else 0.0,
            estimated_vram_gb=6.0 if ocr else None,
            optional_dependency_group="ocr-ovis" if ocr else None,
        ),
    )
