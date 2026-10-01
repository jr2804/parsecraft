"""Shared System One judge: one Choice question per page intent, distribution ranks.

Every System One endpoint parsecraft ships — the TypeSafe cloud, OpenCode Zen,
and a local Ollama daemon — is reached through the *same* vendored
``typesafe-sdk``, injected per endpoint with constructor arguments
(``base_url``, ``api_key``, ``model``). Verified in typesafe-sdk 0.7.2:

- ``TypeSafeClient(base_url=...)`` wins over ``TYPESAFE_BASE_URL`` and the SDK
  appends its own ``/v1/systemone`` path (``_core/constants.SYSTEM_ONE_PATH``);
- ``_core/transport.prepare`` sends ``Authorization: Bearer <api_key>`` — the
  header style OpenCode Zen documents, so a Zen key needs no custom transport;
- the documented bounds (10 s per operation, 3 attempts, 30 s retry budget) hold
  uniformly for the cloud endpoints because the transport is the same code; a
  *local* endpoint overrides only the per-operation timeout, because a cold
  model load is slower than any cloud round trip (measured: 12.4 s for
  ``nimble`` on this host).

Ranking: ONE Choice question per page intent over the *eligible* candidates.
The option set is the candidate names and the answer's probability distribution
IS the ranking — a candidate the answer omits scores ``0.0``, and the stable
sort keeps the planner's name order for ties, so identical inputs give
identical orders.

Contract: the judge only re-ranks candidates ``plan_route`` already deemed
eligible (ADR-0004 decisions 3-5) — ``_validate_order`` stays the sole
eligibility enforcement point. The SDK is imported at ``load_judge`` time only,
so importing this module needs no optional dependency and no network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Protocol, runtime_checkable

from parsecraft.backends.protocol import BackendDescriptor
from parsecraft.routing.judge import JudgeSpec, MachineProfile
from parsecraft.routing.judge_providers import JudgeProviderUnavailableError, JudgeSpecError
from parsecraft.routing.models import Intent, RoutingError
from parsecraft.routing.rules import is_ocr

#: Single choice question id — the judge only ever asks who should lead.
QUESTION_ID = "lead"
#: The one question every System One endpoint is asked.
INSTRUCTIONS = (
    "Which single eligible backend should lead pass 1 for a page whose intent is the one in `state`? "
    "Rank every candidate by how likely it is to convert the page correctly at the lowest cost."
)

_MODULE_NAME = "typesafe_sdk"
#: Extra that declares the SDK; all three endpoints share it (root AGENTS.md rule 10).
_EXTRA = "[project.optional-dependencies].systemone"
#: Credential sent when an endpoint needs none: the SDK rejects an empty key, and
#: a local daemon ignores the header entirely.
LOCAL_API_KEY = "local"


class JevJudgeError(RoutingError):
    """A System One endpoint was unreachable, answered off-schema, or failed."""


class _ChoiceAnswer(Protocol):
    """The ``ChoiceAnswer`` fields this judge reads."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float


class _Response(Protocol):
    """The ``SystemOneResponse`` surface this judge reads."""

    model: str
    choices: Mapping[str, _ChoiceAnswer]


class _Client(Protocol):
    """A ``TypeSafeClient``: a context manager with one ``system_one`` call."""

    def __enter__(self) -> _Client: ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...
    def system_one(self, state: Mapping[str, object], questions: Mapping[str, object]) -> _Response: ...


class _ChoiceFactory(Protocol):
    """Constructor call shape for ``typesafe_sdk.Choice``."""

    def __call__(self, *, instructions: str, criteria: Mapping[str, object]) -> object: ...


@runtime_checkable
class _SdkModule(Protocol):
    """The ``typesafe_sdk`` surface this provider family needs (verified against 0.7.2)."""

    def TypeSafeClient(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> _Client: ...

    def Choice(self, *, instructions: str, criteria: Mapping[str, object]) -> object: ...


class SdkEndpoint:
    """One System One endpoint, bound to the SDK call that reaches it.

    ``base_url`` is explicit for Zen and Ollama and ``None`` for the SDK's own
    default (the TypeSafe cloud, which also honours ``TYPESAFE_BASE_URL``).
    Constructing a client opens no connection: the SDK validates the key and
    builds its HTTP client, nothing more — so resolution stays network-free.
    """

    def __init__(
        self,
        *,
        module: _SdkModule,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._module = module
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout_s = timeout_s

    def client(self) -> _Client:
        """A fresh SDK client for one call (the memo caps how often that happens)."""
        if self._timeout_s is None:
            return self._module.TypeSafeClient(api_key=self._api_key, model=self._model, base_url=self._base_url)
        return self._module.TypeSafeClient(
            api_key=self._api_key,
            model=self._model,
            base_url=self._base_url,
            timeout=self._timeout_s,
        )

    def choice(self, *, instructions: str, criteria: Mapping[str, object]) -> object:
        """One ``Choice`` question object over ``criteria``."""
        return self._module.Choice(instructions=instructions, criteria=criteria)


class JevJudge:
    """Ranks eligible candidates by one System One Choice distribution.

    Shared by every System One endpoint; only the injected :class:`SdkEndpoint`
    differs.

    Memoized per judge instance, keyed ``(intent value, candidate names)``: a
    page shape that repeats within one plan reuses the verdict already paid
    for, so a four-shape document costs at most four calls instead of one per
    page. ``resolve_judge`` builds a fresh judge per ``convert`` invocation, so
    the memo lives for exactly one plan — never across documents, and never
    across a changed candidate set because the key carries the names. The
    trade-off is deliberate: memoized pages share one verdict. The model cannot
    tell them apart anyway — the request carries the intent, the candidates'
    declared capabilities, and (when known) the host budget, never page content.

    ``machine`` is the host profile the caller already probed; because it is
    constant for one judge instance, it stays out of the memo key.
    """

    def __init__(self, *, endpoint: SdkEndpoint, machine: MachineProfile | None = None) -> None:
        self._endpoint = endpoint
        self._machine = machine
        self._memo: dict[tuple[str, tuple[str, ...]], list[str]] = {}

    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        """Candidate names, best first, by the choice distribution (never widened)."""
        names = [descriptor.name for descriptor in candidates]
        if len(names) < 2:
            return names  # nothing to rank — one candidate is already the answer
        key = (intent.value, tuple(names))
        memoized = self._memo.get(key)
        if memoized is not None:
            return list(memoized)  # a copy: a caller must not mutate the memo
        try:
            with self._endpoint.client() as client:
                response = client.system_one(
                    request_state(intent, candidates, self._machine),
                    {QUESTION_ID: self._endpoint.choice(instructions=INSTRUCTIONS, criteria=choice_criteria(candidates))},
                )
        except Exception as exc:  # SDK boundary — typed, never a raw SDK/transport error
            msg = f"system one call failed: {type(exc).__name__}: {exc}"
            raise JevJudgeError(msg) from exc
        order = rank_from_response(response, candidates)
        self._memo[key] = order
        return list(order)


def load_endpoint_judge(
    *,
    provider: str,
    spec: JudgeSpec,
    api_key_env: str | None = None,
    machine: MachineProfile | None = None,
    base_url: str | None = None,
    timeout_s: float | None = None,
) -> JevJudge:
    """Shared provider body: validate the spec, resolve state, bind, build.

    The spec's model token IS the upstream model id (``jev-latest``,
    ``jev-1.13-free``, ``nimble``), so a ``:variant`` is rejected rather than
    silently appended. ``api_key_env`` names the required credential, or is
    ``None`` for a local endpoint that needs none (the SDK still rejects an empty
    key, so :data:`LOCAL_API_KEY` is sent and the daemon ignores it).
    ``timeout_s`` overrides the SDK's per-operation bound — only the local
    endpoint needs it (a cold model load is slower than any cloud round trip).

    Order is deliberate: **spec before environment**. A malformed spec is a
    caller bug (CLI exit 2) and must outrank environment state (a missing extra or
    credential, exit 1), so a bad request never hides behind an unconfigured
    host — the same reasoning that checks the source suffix before probing.
    """
    if spec.variant is not None:
        msg = f"{provider} takes the upstream model id as its model token — {spec.provider}/{spec.model} does not accept a variant"
        raise JudgeSpecError(msg)
    module = sdk_module(provider)
    api_key = LOCAL_API_KEY if api_key_env is None else _required_api_key(provider, api_key_env)
    endpoint = SdkEndpoint(module=module, api_key=api_key, model=spec.model, base_url=base_url, timeout_s=timeout_s)
    return JevJudge(endpoint=endpoint, machine=machine)


def sdk_module(provider: str) -> _SdkModule:
    """Import ``typesafe_sdk`` at load time, or fail with the extra to install."""
    try:
        module = import_module(_MODULE_NAME)
    except ImportError as exc:
        hint = f"{_MODULE_NAME!r} is not installed — install the {provider!r} extra ({_EXTRA})"
        raise JudgeProviderUnavailableError(provider, _MODULE_NAME, hint) from exc
    if not isinstance(module, _SdkModule):
        raise JudgeProviderUnavailableError(provider, _MODULE_NAME, f"{_MODULE_NAME} does not export TypeSafeClient/Choice")
    return module


def _required_api_key(provider: str, env_var: str) -> str:
    """Required credential from the environment; resolution fails rather than deferring."""
    key = os.environ.get(env_var, "").strip()
    if not key:
        hint = f"{env_var} is not set — export the credential before resolving a {provider} judge"
        raise JudgeProviderUnavailableError(provider, _MODULE_NAME, hint)
    return key


def request_state(
    intent: Intent,
    candidates: Sequence[BackendDescriptor],
    machine: MachineProfile | None,
) -> dict[str, object]:
    """Bounded request state: intent, declared capabilities, and the host budget.

    Never document content: the model sees which candidates exist, what they
    declare, and how much VRAM this machine has. An unknown host contributes no
    ``machine`` key at all rather than a misleading zero.
    """
    state: dict[str, object] = {
        "intent": intent.value,
        "candidates": {descriptor.name: describe(descriptor) for descriptor in candidates},
    }
    if machine is not None:
        state["machine"] = {"vram_budget_gb": machine.vram_budget_gb}
    return state


def describe(descriptor: BackendDescriptor) -> dict[str, object]:
    """Declared capability facts only — no document content, no prose."""
    capabilities = descriptor.capabilities
    return {
        "family": "ocr" if is_ocr(descriptor) else "native",
        "requires_gpu": capabilities.requires_gpu,
        "estimated_vram_gb": capabilities.estimated_vram_gb,
        "formats": list(capabilities.supported_formats),
    }


def choice_criteria(candidates: Sequence[BackendDescriptor]) -> dict[str, object]:
    """Option = candidate name; description = its declared capabilities in prose.

    Prose, not the structured facts ``request_state`` carries: the criteria map
    is the label description every endpoint accepts, and ``state`` already
    holds the machine-readable version.
    """
    return {descriptor.name: _sentence(descriptor) for descriptor in candidates}


def rank_from_response(response: _Response, candidates: Sequence[BackendDescriptor]) -> list[str]:
    """Calibrated ranking, best first; every candidate present, none invented."""
    answer = response.choices.get(QUESTION_ID)
    if answer is None:
        msg = f"system one endpoint answered no choice for question {QUESTION_ID!r}"
        raise JevJudgeError(msg)
    probabilities = answer.probabilities

    def _probability(descriptor: BackendDescriptor) -> float:
        raw = probabilities.get(descriptor.name)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return 0.0  # omitted or non-numeric candidate: last, ties keep the planner's name order

    # sorted() is stable → equal probabilities keep candidates' (name-sorted)
    # input order, so identical inputs give identical orders.
    return [descriptor.name for descriptor in sorted(candidates, key=lambda descriptor: -_probability(descriptor))]


def _sentence(descriptor: BackendDescriptor) -> str:
    """One capability sentence for a choice label."""
    capabilities = descriptor.capabilities
    family = "OCR" if is_ocr(descriptor) else "native"
    if capabilities.requires_gpu:
        vram = f"~{capabilities.estimated_vram_gb:g} GB VRAM" if capabilities.estimated_vram_gb is not None else "a GPU"
        gpu = f"requires {vram}"
    else:
        gpu = "runs on CPU"
    formats = ", ".join(capabilities.supported_formats) or "no declared formats"
    return f"{family} backend; {gpu}; handles {formats}"
