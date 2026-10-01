"""TypeSafe cloud System One judge provider — spec ``systemone/<model>``.

The vendored ``typesafe-sdk`` against the TypeSafe cloud (the SDK's own default
base URL; ``TYPESAFE_BASE_URL`` can point it elsewhere). Needs the
``systemone`` extra (``typesafe-sdk``, MIT, pure Python; surface verified
against 0.7.2) and ``TYPESAFE_API_KEY``, which is checked at resolution so the
CLI reports ``judge unavailable: …`` before any backend runs.

The request shape, the SDK's timing/retry bounds, the per-shape memo, and the
ranking all live in :mod:`parsecraft.providers._jev` — this module only names
the endpoint and its credential. Spec: ``systemone/jev-latest`` — the TypeSafe
cloud is served by ``jev-latest`` and ``jev-preview`` (verified against the live
``/v1/models``; a bare ``jev`` answers ``400 Unknown model``).
"""

from __future__ import annotations

from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "systemone"
#: Required credential; resolution fails without it (call-time env is not read).
_API_KEY_ENV = "TYPESAFE_API_KEY"


def load_judge(spec: JudgeSpec, machine: MachineProfile | None = None) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract)."""
    return load_endpoint_judge(
        provider=PROVIDER_NAME,
        spec=spec,
        machine=machine,
        api_key_env=_API_KEY_ENV,
    )
