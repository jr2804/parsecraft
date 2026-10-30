"""TypeSafe cloud System One judge provider — spec ``typesafe-ai/<model>``.

The vendored ``typesafe-sdk`` against the TypeSafe cloud (the SDK's own default
base URL; ``TYPESAFE_BASE_URL`` can point it elsewhere). The SDK is a **core
dependency** (MIT, pure Python; surface verified against 0.7.2), so no extra is
involved; ``TYPESAFE_API_KEY`` is checked at resolution so the CLI reports
``judge unavailable: …`` before any backend runs.

The request shape, the SDK's timing/retry bounds, the per-shape memo, and the
ranking all live in :mod:`parsecraft.providers._jev` — this module only names
the endpoint and its credential. ``typesafe-ai`` is the provider; **System One**
is the model type it serves. Spec: ``typesafe-ai/jev-latest`` — the cloud serves
``jev-latest`` and ``jev-preview`` (verified against the live ``/v1/models``; a
bare ``jev`` answers ``400 Unknown model``).
"""

from __future__ import annotations

from parsecraft.providers import JudgeProviderProfile
from parsecraft.providers._endpoints import resolve_endpoint
from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge
from parsecraft.routing.models import RoutingPreference

#: Declared provider facts — spec spelling, model tokens, credential.
PROFILE = JudgeProviderProfile(
    name="typesafe-ai",
    models=("jev-latest", "jev-preview"),
    api_key_env="TYPESAFE_API_KEY",
)
#: Spec provider token (module name is the token with hyphens as underscores).
PROVIDER_NAME = PROFILE.name


def load_judge(
    spec: JudgeSpec,
    machine: MachineProfile | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers``."""
    base_url, api_key = resolve_endpoint(
        PROFILE.name,
        default_base_url=PROFILE.base_url,
        legacy_api_key_env=PROFILE.api_key_env,
    )
    return load_endpoint_judge(
        provider=PROFILE.name,
        spec=spec,
        machine=machine,
        preference=preference,
        api_key_env=PROFILE.api_key_env,
        api_key=api_key,
        base_url=base_url,
    )
