"""``parsecraft judges`` — the built-in judge providers and how to spell their specs.

Description only: listing resolves nothing, reads no credential, imports no
extra's heavy module, probes no host and contacts no endpoint. Availability is
reported from ``find_spec`` (is the extra installed?) and ``os.environ`` (is the
credential exported?), so the command answers the same on every machine.

The facts come from each provider module's ``PROFILE`` — one declaration per
provider, shared with the loader and checked against the docs table by
``tests/test_cli_judges.py``.
"""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import cast

from parsecraft.environment import extra_present
from parsecraft.providers import BUILTIN_JUDGE_PROVIDERS, JudgeProviderProfile

#: What the CLI does when no spec is given (the first line of the human output).
DEFAULT_LINE = "no --judge: the deterministic rule table decides; no provider is resolved and no network is used"
_SDK_DEFAULT_ENDPOINT = "SDK default base URL"


class JudgeEntry(JudgeProviderProfile):
    """A provider's declared profile plus this host's availability for it."""

    credential_set: bool
    extra_installed: bool


def entries() -> list[JudgeEntry]:
    """One entry per built-in judge provider, in declared display order."""
    return [_entry(provider) for provider in BUILTIN_JUDGE_PROVIDERS]


def entries_payload(items: list[JudgeEntry]) -> list[dict[str, object]]:
    """JSON-ready payload: the profile, its availability, and the spec spellings."""
    return [{**item.model_dump(mode="json"), "specs": list(item.specs)} for item in items]


def render_entries(items: list[JudgeEntry]) -> list[str]:
    """Human-readable lines: one per spec, with availability and endpoint."""
    lines = [DEFAULT_LINE]
    for item in items:
        for spec in item.specs:
            credential = "no credential" if item.api_key_env is None else f"{item.api_key_env} ({'set' if item.credential_set else 'unset'})"
            extra = f"extra {item.extra} ({'installed' if item.extra_installed else 'missing'})"
            lines.append(f"{spec:24} {credential:32} {extra:28} {_endpoint(item)}")
        if item.example_models:
            lines.append(f"{'':24} verified local models: {', '.join(item.example_models)}")
    return lines


def _entry(provider: str) -> JudgeEntry:
    """Read a provider module's declared ``PROFILE`` and detect its availability."""
    module: ModuleType = import_module(f"parsecraft.providers.{provider.replace('-', '_')}")
    profile = cast(JudgeProviderProfile, getattr(module, "PROFILE", None))
    return JudgeEntry(
        **profile.model_dump(),
        credential_set=bool(os.environ.get(profile.api_key_env, "").strip()) if profile.api_key_env is not None else False,
        extra_installed=extra_present(profile.extra),
    )


def _endpoint(profile: JudgeProviderProfile) -> str:
    """Where the provider talks to, from its own declaration."""
    return profile.base_url if profile.base_url is not None else _SDK_DEFAULT_ENDPOINT
