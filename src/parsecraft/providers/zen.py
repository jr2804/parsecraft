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

from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "zen"
#: Zen's API root; the SDK appends its ``/v1/systemone`` path.
BASE_URL = "https://opencode.ai/zen"
#: Required credential; resolution fails without it (call-time env is not read).
_API_KEY_ENV = "OPENCODE_API_KEY"


def load_judge(spec: JudgeSpec, machine: MachineProfile | None = None) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract)."""
    return load_endpoint_judge(
        provider=PROVIDER_NAME,
        spec=spec,
        machine=machine,
        api_key_env=_API_KEY_ENV,
        base_url=BASE_URL,
    )
