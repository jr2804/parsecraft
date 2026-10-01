"""Judge provider registry: resolve ``provider/model[:variant]`` strings.

Resolution is lazy and offline: a provider module
(``parsecraft.providers.<provider>``) is imported via ``import_module``
only when a judge is actually resolved — never at import time, never by an
inline ``import`` (pyreorder hoists those). Judge validity is enforced by
``plan_route``'s ``_validate_order``; this module only produces a
:class:`~parsecraft.routing.judge.RoutingJudge` instance.
"""

from __future__ import annotations

from importlib import import_module
from typing import cast

from pydantic import ValidationError

from parsecraft.routing.judge import DeterministicJudge, JudgeProviderLoader, JudgeSpec, MachineProfile, RoutingJudge
from parsecraft.routing.models import RoutingError, RoutingPreference

#: Lazy default module path for a provider's loader (export: ``load_judge``).
DEFAULT_PROVIDER_MODULE = "parsecraft.providers.{provider}"
_LOADER_EXPORT = "load_judge"

#: Runtime-registered providers; explicit registration beats the lazy path.
_PROVIDERS: dict[str, JudgeProviderLoader] = {}


class JudgeError(RoutingError):
    """Base class for judge resolution failures."""


class JudgeSpecError(JudgeError):
    """The judge spec string is malformed or unsupported."""


class JudgeProviderUnavailableError(JudgeError):
    """No loader is registered and the provider module cannot be loaded.

    ``module_name`` names the module that would resolve the provider, so a
    credential gap found while resolving it (a missing ``TYPESAFE_API_KEY``,
    say) reports that module too — resolution-time unavailability, not a
    missing import.
    """

    def __init__(self, provider: str, module_name: str, hint: str) -> None:
        self.provider = provider
        self.module_name = module_name
        self.hint = hint
        super().__init__(f"judge provider {provider!r} unavailable: {hint}")


class JudgeProviderLoadError(JudgeError):
    """The provider loader failed or returned something that is not a judge."""

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"judge provider {provider!r} failed: {detail}")


def register_judge_provider(name: str, loader: JudgeProviderLoader) -> None:
    """Register (or replace) the loader for a provider name.

    Last registration wins — re-registering enables overrides and test
    isolation. An explicit registration always beats the lazy module path.
    """
    if not name or "/" in name:
        raise JudgeSpecError(f"provider name must be non-empty and slash-free, got {name!r}")
    if not callable(loader):
        raise JudgeError(f"loader for provider {name!r} must be callable")
    _PROVIDERS[name] = loader


def resolve_judge(
    spec: str | RoutingJudge | None,
    *,
    machine: MachineProfile | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingJudge:
    """Turn a config/CLI judge spec into a judge instance.

    ``None`` → :class:`DeterministicJudge` (unchanged default), built with
    ``preference`` so the deterministic fallback honours the caller's axis too; a
    judge instance passes through; a string is parsed and dispatched to its
    provider (runtime registry first, then the lazy module path). ``machine``
    carries the host facts the caller already probed, so a machine-aware
    provider never has to probe again; ``None`` means the caller offered none.
    The result still only re-ranks — ``plan_route`` validates every order.
    """
    if spec is None:
        return DeterministicJudge(preference)
    if isinstance(spec, RoutingJudge):
        return spec
    if not isinstance(spec, str):
        raise JudgeSpecError(f"unsupported judge spec type: {type(spec).__name__}")
    parsed = parse_judge_spec(spec)
    loader = _PROVIDERS.get(parsed.provider)
    if loader is None:
        loader = _lazy_loader(parsed.provider)
    try:
        judge = loader(parsed, machine, preference)
    except JudgeError:
        raise
    except Exception as exc:
        raise JudgeProviderLoadError(parsed.provider, str(exc)) from exc
    if not isinstance(judge, RoutingJudge):
        raise JudgeProviderLoadError(parsed.provider, f"loader returned {type(judge).__name__}, not a RoutingJudge")
    return judge


def parse_judge_spec(spec: str) -> JudgeSpec:
    """Parse ``provider/model[:variant]`` into a typed :class:`JudgeSpec`."""
    text = spec.strip()
    provider, separator, rest = text.partition("/")
    if not separator:
        raise JudgeSpecError(f"judge spec {spec!r} must look like 'provider/model[:variant]'")
    model, colon, variant = rest.partition(":")
    try:
        return JudgeSpec(provider=provider, model=model, variant=variant if colon else None)
    except ValidationError as exc:
        raise JudgeSpecError(f"invalid judge spec {spec!r}: {exc}") from exc


def _lazy_loader(provider: str) -> JudgeProviderLoader:
    # Provider tokens may be hyphenated (``typesafe-ai``); module names may not.
    module_name = DEFAULT_PROVIDER_MODULE.format(provider=provider.replace("-", "_"))
    try:
        module = import_module(module_name)
    except ImportError as exc:
        hint = (
            f"no loader registered and {module_name!r} could not be imported — "
            f"install the extra that ships it (see: parsecraft judges) or call register_judge_provider({provider!r}, loader)"
        )
        raise JudgeProviderUnavailableError(provider, module_name, hint) from exc
    loader = getattr(module, _LOADER_EXPORT, None)
    if not callable(loader):
        raise JudgeProviderUnavailableError(provider, module_name, f"{module_name} does not export {_LOADER_EXPORT}(spec)")
    return cast(JudgeProviderLoader, loader)
