"""OpenCode Zen System One judge provider — spec ``zen/<model>``.

Zen serves the same System One wire at ``https://opencode.ai/zen/v1/systemone``
and authenticates with ``Authorization: Bearer $OPENCODE_API_KEY`` (verified in
Zen's own documentation), which is exactly the header and the appended path the
vendored SDK uses — so this endpoint is *injected* into the SDK rather than
hand-rolled: ``base_url`` is the Zen host and the SDK adds ``/v1/systemone``.

Models: ``jev-1.13`` and ``jev-1.13-free`` (the free one is the sensible choice
for tests and demos — no billing side effect). Needs the ``systemone`` extra and
``OPENCODE_API_KEY``, which is checked at resolution so the CLI reports
``judge unavailable: …`` before any backend runs. Shared mechanics:
:mod:`parsecraft.providers._jev`.
"""

from __future__ import annotations

from parsecraft.providers import JudgeProviderProfile
from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge
from parsecraft.routing.models import RoutingPreference

#: Declared provider facts — spec spelling, model tokens, credential, endpoint.
PROFILE = JudgeProviderProfile(
    name="zen",
    extra="systemone",
    models=("jev-1.13", "jev-1.13-free"),
    api_key_env="OPENCODE_API_KEY",
    base_url="https://opencode.ai/zen",
)
#: Spec provider token (module name is the token with hyphens as underscores).
PROVIDER_NAME = PROFILE.name


def load_judge(
    spec: JudgeSpec,
    machine: MachineProfile | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers``."""
    return load_endpoint_judge(
        provider=PROFILE.name,
        spec=spec,
        machine=machine,
        preference=preference,
        api_key_env=PROFILE.api_key_env,
        base_url=PROFILE.base_url,
    )
