"""Local Ollama System One judge provider — spec ``ollama/<model>``.

Ollama 0.35+ serves the same System One wire locally at
``http://localhost:11434/v1/systemone`` (the SDK appends that path) and needs no
credential — the SDK rejects an empty key, so the local endpoint gets a sentinel
it ignores. **Local by definition**: resolution reads no key and opens no
connection (the SDK only validates its config and builds an HTTP client); the
daemon is contacted on the first ``rank``.

The System One model is ``nimble`` (``laya-gguf`` is not scoring-capable: it
answers ``400: use a local Nimble or Tev GGUF model``). Any local Nimble/Tev
GGUF works. Needs the ``systemone`` extra. Shared mechanics:
:mod:`parsecraft.providers._jev`.
"""

from __future__ import annotations

from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge
from parsecraft.routing.models import RoutingPreference

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "ollama"
#: The daemon's documented root; the SDK appends its ``/v1/systemone`` path.
BASE_URL = "http://localhost:11434"
#: Per-operation bound: a cold local model load is slower than the SDK's 10 s
#: cloud default (measured: 12.4 s for ``nimble`` on this host, and a larger
#: model on CPU takes longer). Only this endpoint overrides it.
_TIMEOUT_S = 120.0


def load_judge(
    spec: JudgeSpec,
    machine: MachineProfile | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract).

    No ``api_key_env``: this endpoint needs no credential, and the shared loader
    sends the SDK's local sentinel (the daemon ignores the header).
    """
    return load_endpoint_judge(
        provider=PROVIDER_NAME,
        spec=spec,
        machine=machine,
        preference=preference,
        base_url=BASE_URL,
        timeout_s=_TIMEOUT_S,
    )
