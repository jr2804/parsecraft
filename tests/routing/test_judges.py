"""Offline tests for judge spec parsing, the provider registry, resolution."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, cast

import pytest

from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
from parsecraft.routing import DeterministicJudge, Intent, RoutingJudge, RoutingPreference
from parsecraft.routing.judge import JudgeSpec, MachineProfile
from parsecraft.routing.judge_providers import (
    DEFAULT_PROVIDER_MODULE,
    JudgeError,
    JudgeProviderLoadError,
    JudgeProviderUnavailableError,
    JudgeSpecError,
    parse_judge_spec,
    register_judge_provider,
    resolve_judge,
)

PROVIDERS_MODULE = "parsecraft.routing.judge_providers"


def make_descriptor(name: str, vram: float | None = None) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(supported_formats=["text/plain"], estimated_vram_gb=vram),
    )


CANDIDATES = [make_descriptor("native-b", 2.0), make_descriptor("native-a", 1.0)]


# ── resolve_judge: None / instance / bad type ──────────────────────────────


def test_resolve_none_returns_deterministic_default() -> None:
    judge = resolve_judge(None)
    assert isinstance(judge, DeterministicJudge)
    first = list(judge.rank(Intent.NATIVE, CANDIDATES))
    second = list(resolve_judge(None).rank(Intent.NATIVE, CANDIDATES))
    assert first == second == ["native-a", "native-b"]


def test_resolve_judge_instance_passes_through() -> None:
    class Custom:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return [descriptor.name for descriptor in candidates]

    custom = Custom()
    assert resolve_judge(cast(RoutingJudge, custom)) is custom


def test_resolve_judge_rejects_unsupported_type() -> None:
    with pytest.raises(JudgeSpecError, match="unsupported judge spec type"):
        resolve_judge(cast(Any, object()))


# ── parse_judge_spec ───────────────────────────────────────────────────────


def test_parse_full_spec_with_variant() -> None:
    spec = parse_judge_spec("cli-fake/laya:typed-decisions")
    assert spec == JudgeSpec(provider="cli-fake", model="laya", variant="typed-decisions")


def test_parse_spec_without_variant_and_with_whitespace() -> None:
    assert parse_judge_spec("typesafeai/jev").variant is None
    assert parse_judge_spec("  openrouter/jev \n").provider == "openrouter"


@pytest.mark.parametrize(
    "bad_spec",
    [
        "nodash",
        "",
        "a/",
        "/b",
        "a/b:",
        "a.b/c",
        "A/B",
    ],
)
def test_parse_rejects_malformed_specs(bad_spec: str) -> None:
    with pytest.raises(JudgeSpecError):
        parse_judge_spec(bad_spec)


def test_default_provider_module_path_convention() -> None:
    assert DEFAULT_PROVIDER_MODULE.format(provider="zen") == "parsecraft.providers.zen"


# ── registry ───────────────────────────────────────────────────────────────


def test_registered_loader_receives_typed_spec_and_host_facts() -> None:
    captured: list[tuple[JudgeSpec, MachineProfile | None]] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        captured.append((spec, machine))
        return DeterministicJudge()

    register_judge_provider("fake", loader)
    judge = resolve_judge("fake/model:v1", machine=MachineProfile(vram_budget_gb=12.5))
    assert isinstance(judge, DeterministicJudge)
    assert captured == [(JudgeSpec(provider="fake", model="model", variant="v1"), MachineProfile(vram_budget_gb=12.5))]


def test_resolve_judge_without_host_facts_passes_none() -> None:
    """An unprobed caller offers no machine facts — not a zero-budget host."""
    captured: list[MachineProfile | None] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        captured.append(machine)
        return DeterministicJudge()

    register_judge_provider("unprobed", loader)
    resolve_judge("unprobed/m")
    assert captured == [None]


def test_resolve_judge_passes_the_preference_to_the_loader() -> None:
    captured: list[RoutingPreference] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        captured.append(preference)
        return DeterministicJudge()

    register_judge_provider("pref", loader)
    resolve_judge("pref/m", preference=RoutingPreference.QUALITY)
    assert captured == [RoutingPreference.QUALITY]


def test_resolve_judge_defaults_the_preference_to_balanced() -> None:
    captured: list[RoutingPreference] = []

    def loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        captured.append(preference)
        return DeterministicJudge()

    register_judge_provider("prefdefault", loader)
    resolve_judge("prefdefault/m")
    assert captured == [RoutingPreference.BALANCED]


def test_resolve_judge_none_honours_the_preference() -> None:
    """The deterministic fallback is preference-aware too, so one flag drives both paths."""
    heavy = make_descriptor("ocr-ovis", 6.0)
    light = make_descriptor("ocr-unlimited", 4.0)
    judge = resolve_judge(None, preference=RoutingPreference.QUALITY)
    assert list(judge.rank(Intent.OCR_GENERAL, [light, heavy])) == ["ocr-ovis", "ocr-unlimited"]
    default = resolve_judge(None)
    assert list(default.rank(Intent.OCR_GENERAL, [light, heavy])) == ["ocr-unlimited", "ocr-ovis"]


def test_last_registration_wins() -> None:
    register_judge_provider("dup", lambda spec, machine=None, preference=None: DeterministicJudge())

    class Marker:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return ["native-a"]

    register_judge_provider("dup", lambda spec, machine=None, preference=None: cast(RoutingJudge, Marker()))
    assert isinstance(resolve_judge("dup/m"), Marker)


def test_explicit_registration_beats_lazy_module_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.preferred")

    def module_loader(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        raise AssertionError("lazy module must not load when a loader is registered")

    module.load_judge = module_loader  # ty: ignore[unresolved-attribute] — ModuleType is dynamically extended
    monkeypatch.setitem(sys.modules, "parsecraft.providers.preferred", module)
    register_judge_provider("preferred", lambda spec, machine=None, preference=None: DeterministicJudge())
    assert isinstance(resolve_judge("preferred/m"), DeterministicJudge)


def test_register_rejects_bad_name_and_loader() -> None:
    with pytest.raises(JudgeSpecError, match="slash-free"):
        register_judge_provider("bad/name", lambda spec, machine=None, preference=None: DeterministicJudge())
    with pytest.raises(JudgeSpecError, match="slash-free"):
        register_judge_provider("", lambda spec, machine=None, preference=None: DeterministicJudge())
    with pytest.raises(JudgeError, match="must be callable"):
        register_judge_provider("noload", cast(Any, "not-callable"))


# ── lazy module path ───────────────────────────────────────────────────────


def test_lazy_module_loads_on_first_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.lazyp")

    def load_judge(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        assert spec.model == "m"
        assert machine is None
        return DeterministicJudge()

    module.load_judge = load_judge  # ty: ignore[unresolved-attribute] — ModuleType is dynamically extended
    monkeypatch.setitem(sys.modules, "parsecraft.providers.lazyp", module)
    assert isinstance(resolve_judge("lazyp/m"), DeterministicJudge)


def test_lazy_module_missing_names_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(f"{PROVIDERS_MODULE}.import_module", raiser)
    with pytest.raises(JudgeProviderUnavailableError) as exc_info:
        resolve_judge("ghost/m")
    assert exc_info.value.provider == "ghost"
    assert "parsecraft.providers.ghost" in exc_info.value.module_name
    assert "install the extra that ships it (see: parsecraft judges)" in exc_info.value.hint
    assert "register_judge_provider('ghost', loader)" in exc_info.value.hint


def test_lazy_module_without_loader_export(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("parsecraft.providers.bare")
    monkeypatch.setitem(sys.modules, "parsecraft.providers.bare", module)
    with pytest.raises(JudgeProviderUnavailableError, match="does not export load_judge"):
        resolve_judge("bare/m")


# ── loader outcomes ────────────────────────────────────────────────────────


def test_loader_exception_is_wrapped() -> None:
    def broken(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        raise ValueError("upstream exploded")

    register_judge_provider("broken", broken)
    with pytest.raises(JudgeProviderLoadError, match="upstream exploded") as exc_info:
        resolve_judge("broken/m")
    assert exc_info.value.provider == "broken"


def test_loader_judge_error_passes_through() -> None:
    def raising(spec: JudgeSpec, machine: MachineProfile | None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge:
        raise JudgeSpecError("provider-side spec problem")

    register_judge_provider("raiser", raising)
    with pytest.raises(JudgeSpecError, match="provider-side spec problem"):
        resolve_judge("raiser/m")


def test_loader_returning_non_judge_is_rejected() -> None:
    register_judge_provider("junk", lambda spec, machine=None, preference=None: "not a judge")  # ty: ignore[invalid-argument-type]
    with pytest.raises(JudgeProviderLoadError, match="not a RoutingJudge"):
        resolve_judge("junk/m")
