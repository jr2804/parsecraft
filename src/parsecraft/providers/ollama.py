"""Local Ollama System One judge provider — spec ``ollama/<model>``.

Ollama 0.35+ serves the same System One wire locally at
``http://localhost:11434/v1/systemone`` (the SDK appends that path) and needs no
credential — the SDK rejects an empty key, so the local endpoint gets a sentinel
it ignores. **Local by definition**: resolution reads no key and opens no
connection (the SDK only validates its config and builds an HTTP client); the
daemon is contacted on the first ``rank``.

The System One model is ``nimble`` (``laya-gguf`` is not scoring-capable: it
answers ``400: use a local Nimble or Tev GGUF model``). This is not the ollama
*chat* daemon the ``ollaya`` provider talks to — that is a different seam with a
different wire. Needs the ``systemone`` extra. Shared mechanics:
:mod:`parsecraft.providers._jev`.
"""

from __future__ import annotations

from parsecraft.providers._jev import load_endpoint_judge
from parsecraft.routing.judge import JudgeSpec, MachineProfile, RoutingJudge

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "ollama"
#: The daemon's documented root; the SDK appends its ``/v1/systemone`` path.
BASE_URL = "http://localhost:11434"
#: Sentinel for a local endpoint: the SDK requires a non-empty key, Ollama ignores the header.
_LOCAL_API_KEY = "local"
#: Per-operation bound: a cold local model load is slower than the SDK's 10 s
#: cloud default (measured: 12.4 s for ``nimble`` on this host, and a larger
#: model on CPU takes longer). Only this endpoint overrides it.
_TIMEOUT_S = 120.0


def load_judge(spec: JudgeSpec, machine: MachineProfile | None = None) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract)."""
    return load_endpoint_judge(
        provider=PROVIDER_NAME,
        spec=spec,
        machine=machine,
        api_key=_LOCAL_API_KEY,
        base_url=BASE_URL,
        timeout_s=_TIMEOUT_S,
    )
