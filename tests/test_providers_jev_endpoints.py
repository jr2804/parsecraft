"""Zen and Ollama System One judge providers: endpoint injection, keys, tiering.

Offline tests run against the stub SDK in ``sys.modules``. The semi-live tier
talks to a local Ollama daemon and is marked ``judge``; the live Zen tier is
marked ``judge`` and skips without ``OPENCODE_API_KEY``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from importlib.util import find_spec
from typing import Any, cast

import pytest

from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
from parsecraft.environment import probe_environment
from parsecraft.providers import _jev, ollama, zen
from parsecraft.routing import Intent
from parsecraft.routing.judge import JudgeSpec, MachineProfile
from parsecraft.routing.judge_providers import JudgeProviderUnavailableError, JudgeSpecError, resolve_judge
from tests.fixtures.jev_sdk import StubSdk

_ZEN_KEY = "zen-test-key"
_QUESTION_ID = "lead"
#: The CPU-only profile: the behavioral anchor's premise (pc-1's smoke host).
_CPU_ONLY = MachineProfile(vram_budget_gb=0.0)
#: A GPU host that can host the OCR candidate comfortably.
_GPU_HOST = MachineProfile(vram_budget_gb=32.0)
_NATIVE = BackendDescriptor(
    name="native-pdf",
    capabilities=BackendCapabilities(supported_formats=["application/pdf"], requires_gpu=False),
)
_OCR_GPU = BackendDescriptor(
    name="ocr-ovis",
    capabilities=BackendCapabilities(
        supported_formats=["application/pdf", "image/png"],
        requires_gpu=True,
        estimated_vram_gb=6.0,
        optional_dependency_group="ocr-ovis",
    ),
)
_OCR_CPU = BackendDescriptor(
    name="ocr-stub",
    capabilities=BackendCapabilities(supported_formats=["application/pdf"], requires_gpu=False),
)


# ── semi-live tier: a real local Ollama daemon, three machine profiles ───────

_OLLAMA_MODEL = "nimble"


# ── endpoint injection: one SDK, three endpoint bindings ─────────────────────


def test_zen_binds_base_url_key_and_model(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zen is the cloud wire with its own host and key — no custom transport."""
    monkeypatch.setenv(zen._API_KEY_ENV, _ZEN_KEY)
    judge = cast(_jev.JevJudge, zen.load_judge(JudgeSpec(provider="zen", model="jev-1.13-free")))
    judge.rank(Intent.NATIVE, [_OCR_GPU, _NATIVE])
    assert jev_sdk.client_kwargs == [{"api_key": _ZEN_KEY, "model": "jev-1.13-free", "base_url": "https://opencode.ai/zen"}]


def test_ollama_binds_the_local_host_and_no_credential(jev_sdk: StubSdk) -> None:
    """Ollama is local by definition: sentinel key, localhost root, nothing read.

    It is also the one endpoint with a raised per-operation bound: a cold local
    model load measured 12.4 s, past the SDK's 10 s cloud default.
    """
    judge = cast(_jev.JevJudge, ollama.load_judge(JudgeSpec(provider="ollama", model="nimble")))
    judge.rank(Intent.NATIVE, [_OCR_GPU, _NATIVE])
    assert jev_sdk.client_kwargs == [{"api_key": "local", "model": "nimble", "base_url": "http://localhost:11434", "timeout": 120.0}]


@pytest.mark.parametrize("provider_name", ["zen", "ollama"])
def test_resolve_judge_reaches_both_providers_lazily(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch, provider_name: str) -> None:
    monkeypatch.setenv(zen._API_KEY_ENV, _ZEN_KEY)
    assert isinstance(resolve_judge(f"{provider_name}/m"), _jev.JevJudge)


@pytest.mark.parametrize("module", [zen, ollama])
def test_variants_are_rejected_for_every_endpoint(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setenv(zen._API_KEY_ENV, _ZEN_KEY)
    with pytest.raises(JudgeSpecError, match="does not accept a variant"):
        module.load_judge(JudgeSpec(provider=module.PROVIDER_NAME, model="m", variant="latest"))
    assert jev_sdk.client_kwargs == []


# ── credentials ─────────────────────────────────────────────────────────────


def test_zen_requires_its_key_at_resolution(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(zen._API_KEY_ENV, raising=False)
    with pytest.raises(JudgeProviderUnavailableError, match="OPENCODE_API_KEY") as excinfo:
        resolve_judge("zen/jev-1.13-free")
    assert excinfo.value.provider == "zen"
    assert jev_sdk.client_kwargs == []


def test_ollama_never_reads_a_credential(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch) -> None:
    """The local endpoint resolves with no key set anywhere."""
    for env_var in ("OPENCODE_API_KEY", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    assert isinstance(ollama.load_judge(JudgeSpec(provider="ollama", model="nimble")), _jev.JevJudge)


# ── machine-aware state reaches the endpoint ─────────────────────────────────


def test_machine_profile_is_sent_to_the_endpoint(jev_sdk: StubSdk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(zen._API_KEY_ENV, _ZEN_KEY)
    judge = zen.load_judge(JudgeSpec(provider="zen", model="jev-1.13-free"), _GPU_HOST)
    judge.rank(Intent.OCR_GENERAL, [_OCR_GPU, _NATIVE])
    state, _ = jev_sdk.calls[0]
    assert cast("dict[str, object]", state["machine"]) == {"vram_budget_gb": 32.0}


def test_ollama_accepts_the_same_loader_contract(jev_sdk: StubSdk) -> None:
    assert isinstance(ollama.load_judge(JudgeSpec(provider="ollama", model="nimble"), _CPU_ONLY), _jev.JevJudge)


def _ollama_available() -> str | None:
    """``None`` when the daemon, the model, and the extra answer, else the skip reason."""
    if find_spec("typesafe_sdk") is None:
        return "the 'systemone' extra is not installed (run: uv run --isolated --extra systemone pytest -m judge --run-judge)"
    try:
        pass
    except ImportError as exc:  # pragma: no cover - stdlib always present
        return f"stdlib unavailable: {exc}"
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as response:  # noqa: S310
            tags = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return f"no Ollama daemon at http://localhost:11434 ({exc})"
    names = {str(entry.get("name", "")) for entry in tags.get("models", []) if isinstance(entry, dict)}
    if not any(name == _OLLAMA_MODEL or name.startswith(f"{_OLLAMA_MODEL}:") for name in names):
        return f"model {_OLLAMA_MODEL!r} is not pulled (have: {sorted(names)})"
    return None


_skip_reason = _ollama_available()


@pytest.mark.judge
@pytest.mark.skipif(_skip_reason is not None, reason=f"semi-live Ollama tier needs a local daemon: {_skip_reason}")
def test_semilive_ollama_ranks_under_three_machine_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    """Contract-valid, deterministic orderings under real / GPU / CPU-only hosts.

    One behavioral anchor from the pc-1 smoke: on the CPU-only profile a
    GPU-only OCR candidate must not rank first. The tier is opt-in, so a model
    update that flips the anchor fails loudly instead of silently.
    """
    candidates = [_OCR_GPU, _OCR_CPU, _NATIVE]
    real = MachineProfile(vram_budget_gb=probe_environment().vram_budget_gb)  # this host, as the planner sees it
    profiles = {"real": real, "better (32.0)": _GPU_HOST, "worse (0.0)": _CPU_ONLY}
    names = [descriptor.name for descriptor in candidates]
    orders: dict[str, list[str]] = {}
    for label, profile in profiles.items():
        judge = ollama.load_judge(JudgeSpec(provider="ollama", model=_OLLAMA_MODEL), profile)
        order = list(judge.rank(Intent.OCR_GENERAL, candidates))
        assert sorted(order) == sorted(names)  # a permutation: nothing invented, nothing dropped
        assert len(order) == len(set(order))
        orders[label] = order
        # One verdict per shape: the memo means a second identical call costs nothing.
        assert list(judge.rank(Intent.OCR_GENERAL, candidates)) == order
    with capsys.disabled():  # the three orderings are the deliverable of this tier
        for label, order in orders.items():
            print(f"  [machine {label}] -> {order}")
    assert orders["worse (0.0)"][0] != _OCR_GPU.name, f"CPU-only host ranked a GPU-only candidate first: {orders['worse (0.0)']}"


@pytest.mark.judge
@pytest.mark.skipif(_skip_reason is not None, reason=f"semi-live Ollama tier needs a local daemon: {_skip_reason}")
def test_semilive_ollama_never_ranks_outside_the_eligible_set() -> None:
    """The judge re-ranks what it is handed — ghosts are dropped, not ranked."""
    judge = ollama.load_judge(JudgeSpec(provider="ollama", model=_OLLAMA_MODEL), _CPU_ONLY)
    order = list(judge.rank(Intent.OCR_GENERAL, [_OCR_CPU, _NATIVE]))
    assert sorted(order) == sorted([_OCR_CPU.name, _NATIVE.name])


# ── live tier: OpenCode Zen (opt-in, needs the extra AND OPENCODE_API_KEY) ───


@pytest.mark.judge
def test_live_zen_ranks_real_candidates() -> None:
    """Ground truth for the Zen endpoint, against the live API (free model)."""
    pytest.importorskip("typesafe_sdk")
    if not os.environ.get(zen._API_KEY_ENV):
        pytest.skip(f"{zen._API_KEY_ENV} is not set")
    judge = resolve_judge("zen/jev-1.13-free", machine=_CPU_ONLY)
    candidates = [_OCR_GPU, _OCR_CPU, _NATIVE]
    order = list(judge.rank(Intent.OCR_GENERAL, candidates))
    assert sorted(order) == sorted(descriptor.name for descriptor in candidates)
    assert len(order) == len(set(order))
