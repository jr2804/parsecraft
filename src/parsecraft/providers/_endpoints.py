"""Judge endpoint resolution: config file, generic env, and CLI overrides (pc-66v).

One decision-model endpoint has two knobs — ``base_url`` and ``api_key`` —
resolved per knob in the same order everywhere:

1. explicit CLI flags (``--judge-base-url`` / ``--judge-api-key``), carried in
   an ambient :class:`EndpointOverride` that ``cli.convert`` sets around
   resolution;
2. generic env vars (``PARSECRAFT_JUDGE_BASE_URL`` /
   ``PARSECRAFT_JUDGE_API_KEY``) — the only judge env vars this package
   defines;
3. the ``[providers.<section>]`` file value (``${VAR}`` already expanded by
   the config engine);
4. the built-in default: the provider's ``PROFILE`` base URL, or the legacy
   credential env var (``TYPESAFE_API_KEY`` / ``OPENCODE_API_KEY``), or no
   credential for the local daemon.

The default decision model is selected the same way by :func:`select_judge_spec`:
explicit ``--judge`` beats ``PARSECRAFT_JUDGE_MODEL`` beats ``[judge] model``.

Config files are read lazily (inside the resolve functions, never at import)
so unresolved runs — including every run without a judge — never touch the
filesystem beyond what they already did. A malformed config file or an
unexpandable ``${VAR}`` surfaces as :class:`JudgeProviderUnavailableError`
naming the provider, never as a raw config error.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from parsecraft.cli.config import JudgeSettings, ProviderEndpoint, build_engine
from parsecraft.config import ConfigError
from parsecraft.routing.judge_providers import JudgeProviderUnavailableError

#: Section-name aliases: the file spells kompend's provider names, the token
#: is this package's provider name. An exact provider token always works too.
SECTION_ALIASES: dict[str, str] = {"typesafe": "typesafe-ai", "ollama-cloud": "ollama"}

#: Generic env vars — the only judge-specific env vars this package defines.
JUDGE_MODEL_ENV = "PARSECRAFT_JUDGE_MODEL"
JUDGE_BASE_URL_ENV = "PARSECRAFT_JUDGE_BASE_URL"
JUDGE_API_KEY_ENV = "PARSECRAFT_JUDGE_API_KEY"


_current_override: ContextVar[EndpointOverride | None] = ContextVar("judge_endpoint_override", default=None)


@dataclass(frozen=True, slots=True)
class EndpointOverride:
    """Explicit per-run endpoint knobs from the CLI flags (unset means absent)."""

    base_url: str | None = None
    api_key: str | None = None


@contextmanager
def override_endpoint(base_url: str | None, api_key: str | None) -> Generator[None]:
    """Carry explicit CLI endpoint knobs through resolution, then drop them."""
    token = _current_override.set(EndpointOverride(base_url=base_url, api_key=api_key))
    try:
        yield
    finally:
        _current_override.reset(token)


def select_judge_spec(cli_spec: str | None) -> str | None:
    """Pick the decision-model spec: CLI flag, generic env, or ``[judge] model``."""
    if cli_spec:
        return cli_spec
    from_env = os.environ.get(JUDGE_MODEL_ENV, "").strip()
    if from_env:
        return from_env
    from_file = (_judge_section().model or "").strip()
    return from_file or None


def resolve_endpoint(
    provider: str,
    *,
    default_base_url: str | None,
    legacy_api_key_env: str | None,
) -> tuple[str | None, str | None]:
    """Resolve ``(base_url, api_key)`` for one judge provider.

    ``api_key`` is ``None`` when no source names a credential — the caller
    keeps its legacy lookup (env var or local sentinel) in that case, so
    unconfigured runs behave exactly as before.
    """
    override = _current_override.get()
    section = _provider_section(provider)
    base_url = _first_non_empty(
        override.base_url if override is not None else None,
        os.environ.get(JUDGE_BASE_URL_ENV),
        section.base_url,
        default_base_url,
    )
    api_key = _first_non_empty(
        override.api_key if override is not None else None,
        os.environ.get(JUDGE_API_KEY_ENV),
        section.api_key,
    )
    if api_key is None and legacy_api_key_env is not None:
        api_key = os.environ.get(legacy_api_key_env, "").strip() or None
    return base_url, api_key


def _judge_section() -> JudgeSettings:
    """The file's ``[judge]`` section (defaults when no file configures one)."""
    try:
        return build_engine(None).load().judge
    except ConfigError as exc:
        raise JudgeProviderUnavailableError("judge", "config", f"cannot read judge configuration: {exc}") from exc


def _provider_section(provider: str) -> ProviderEndpoint:
    """The file's ``[providers.<section>]`` for one provider (defaults absent)."""
    try:
        providers = build_engine(None).load().providers
    except ConfigError as exc:
        raise JudgeProviderUnavailableError(provider, provider, f"cannot read provider configuration: {exc}") from exc
    if provider in providers:
        return providers[provider]
    for section, token in SECTION_ALIASES.items():
        if token == provider and section in providers:
            return providers[section]
    return ProviderEndpoint()


def _first_non_empty(*values: str | None) -> str | None:
    """The first value that is set and not blank (whitespace never configures)."""
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None
