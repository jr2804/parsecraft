"""TypeSafe System One / Jev-backed ``RoutingJudge`` — one Choice, probabilities rank.

Provider entry point for :mod:`parsecraft.routing.judge_providers`:
``load_judge(spec)`` returns a :class:`~parsecraft.routing.judge.RoutingJudge`
backed by TypeSafe's System One API through the optional ``systemone`` extra
(``typesafe-sdk``, MIT, pure Python).

Verified against typesafe-sdk 0.7.2 (2026-10-01) and the live docs
(``primitives/choice``, ``api``, ``sdk/python``):

- ``TypeSafeClient(*, api_key, model)`` reads ``TYPESAFE_API_KEY`` /
  ``TYPESAFE_BASE_URL``; a missing or malformed key raises at construction.
- ``client.system_one(state, questions)`` → ``SystemOneResponse``, whose
  ``.choices`` maps the question id to the answer's ``.choice``,
  ``.probabilities`` (a distribution summing to 1) and ``.confidence``.
- A Choice question is ``Choice(instructions=..., criteria={option: description})``.

Ranking: ONE Choice question per page intent over the *eligible* candidates.
The option set is the candidate names and the distribution IS the ranking — a
candidate the answer omits scores ``0.0``, and the stable sort keeps the
planner's name order for ties, so identical inputs give identical orders.

Bounding defaults are the SDK's, not ours (verified on typesafe-sdk 0.7.2 and
deliberately not overridden): 10 s per HTTP operation, and a retry policy of 3
attempts (``max_retries=2``) with exponential backoff 0.5 s → 5 s, 0.25 jitter,
and a 30 s total retry budget per call (tenacity ``stop_after_attempt |
stop_before_delay``). A future SDK default change would shift that bound; no
constructor argument exists to tune it until a real need appears.

Contract: the judge only re-ranks candidates ``plan_route`` already deemed
eligible (ADR-0004 decisions 3-5) — ``_validate_order`` stays the sole
eligibility enforcement point. ``typesafe_sdk`` is imported through
``import_module`` inside :func:`load_judge` only: importing this module touches
no network and no third-party dependency, and a missing ``TYPESAFE_API_KEY``
fails at resolution with a typed error, never mid-conversion.

Spec: ``systemone/<model>`` (e.g. ``systemone/jev``, ``systemone/jev-latest``).
The model token is the upstream model id, so a ``:variant`` is rejected rather
than silently ignored.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Protocol, runtime_checkable

from parsecraft.backends.protocol import BackendDescriptor
from parsecraft.routing.judge import JudgeSpec, RoutingJudge
from parsecraft.routing.judge_providers import (
    JudgeProviderLoadError,
    JudgeProviderUnavailableError,
    JudgeSpecError,
)
from parsecraft.routing.models import Intent, RoutingError
from parsecraft.routing.rules import is_ocr

#: Spec provider token (also the module name under ``parsecraft.providers``).
PROVIDER_NAME = "systemone"
#: Upstream distribution name — the extra names ``typesafe-sdk``, the import ``typesafe_sdk``.
DISTRIBUTION_NAME = "typesafe-sdk"
_MODULE_NAME = "typesafe_sdk"
_EXTRA = "[project.optional-dependencies].systemone"
#: Required credential; resolution fails without it (call-time env is not read).
_API_KEY_ENV = "TYPESAFE_API_KEY"

#: Single choice question id — the judge only ever asks who should lead.
_QUESTION_ID = "lead"
_INSTRUCTIONS = (
    "Which single eligible backend should lead pass 1 for a page whose intent is `intent`? "
    "Rank every candidate by how likely it is to convert the page correctly at the lowest cost."
)


class JevJudgeError(RoutingError):
    """The System One call failed or answered outside the choice contract."""


class _ChoiceAnswer(Protocol):
    """The ``ChoiceAnswer`` fields this provider reads."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float


class _Response(Protocol):
    """The ``SystemOneResponse`` surface this provider reads."""

    model: str
    choices: Mapping[str, _ChoiceAnswer]


class _Client(Protocol):
    """The ``TypeSafeClient`` surface used: a context manager with one call."""

    def __enter__(self) -> _Client: ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...
    def system_one(self, state: Mapping[str, object], questions: Mapping[str, object]) -> _Response: ...


class _ClientFactory(Protocol):
    """Constructor call shape for ``typesafe_sdk.TypeSafeClient``."""

    def __call__(self, *, api_key: str, model: str) -> _Client: ...


class _ChoiceFactory(Protocol):
    """Constructor call shape for ``typesafe_sdk.Choice``."""

    def __call__(self, *, instructions: str, criteria: Mapping[str, object]) -> object: ...


@runtime_checkable
class _SdkModule(Protocol):
    """The ``typesafe_sdk`` surface this provider needs (verified against 0.7.2)."""

    def TypeSafeClient(self, *, api_key: str, model: str) -> _Client: ...
    def Choice(self, *, instructions: str, criteria: Mapping[str, object]) -> object: ...


class JevJudge:
    """Ranks eligible candidates by one System One Choice distribution.

    Memoized per judge instance, keyed ``(intent value, candidate names)``: a
    page shape that repeats within one plan reuses the verdict already paid
    for, so a four-shape document costs at most four calls instead of one per
    page. ``resolve_judge`` builds a fresh judge per ``convert`` invocation, so
    the memo lives for exactly one plan — never across documents, and never
    across a changed candidate set because the key carries the names. The
    trade-off is deliberate: memoized pages share one verdict. The model cannot
    tell them apart anyway — the request carries the intent and the candidates'
    declared capabilities, never page content.
    """

    def __init__(self, *, client_factory: _ClientFactory, choice_factory: _ChoiceFactory, api_key: str, model: str) -> None:
        self._client_factory = client_factory
        self._choice_factory = choice_factory
        self._api_key = api_key
        self._model = model
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
            with self._client_factory(api_key=self._api_key, model=self._model) as client:
                response = client.system_one(
                    _state(intent, names),
                    {_QUESTION_ID: self._choice_factory(instructions=_INSTRUCTIONS, criteria=_criteria(candidates))},
                )
        except Exception as exc:  # SDK boundary — typed, never raw
            msg = f"system one call failed: {type(exc).__name__}: {exc}"
            raise JevJudgeError(msg) from exc
        order = _rank_from_response(response, candidates)
        self._memo[key] = order
        return list(order)


def load_judge(spec: JudgeSpec) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-1's contract)."""
    if spec.variant is not None:
        msg = f"systemone takes the upstream model id as its model token — {spec.provider}/{spec.model} does not accept a variant"
        raise JudgeSpecError(msg)
    module = _sdk_module()
    api_key = _api_key()
    return JevJudge(client_factory=module.TypeSafeClient, choice_factory=module.Choice, api_key=api_key, model=spec.model)


def _sdk_module() -> _SdkModule:
    """Import ``typesafe_sdk`` lazily, or fail with the extra to install."""
    try:
        module = import_module(_MODULE_NAME)
    except ImportError as exc:
        hint = f"{DISTRIBUTION_NAME!r} is not installed — install the {PROVIDER_NAME!r} extra ({_EXTRA})"
        raise JudgeProviderUnavailableError(PROVIDER_NAME, _MODULE_NAME, hint) from exc
    if not isinstance(module, _SdkModule):
        raise JudgeProviderLoadError(PROVIDER_NAME, f"{_MODULE_NAME} does not export TypeSafeClient/Choice")
    return module


def _api_key() -> str:
    """Required credential from the environment; resolution fails rather than deferring."""
    key = os.environ.get(_API_KEY_ENV, "").strip()
    if not key:
        hint = f"{_API_KEY_ENV} is not set — export the credential before resolving a System One judge"
        raise JudgeProviderUnavailableError(PROVIDER_NAME, _MODULE_NAME, hint)
    return key


def _state(intent: Intent, names: Sequence[str]) -> dict[str, object]:
    """Bounded, typed page state: the intent plus the candidate names."""
    return {"intent": intent.value, "candidates": list(names)}


def _criteria(candidates: Sequence[BackendDescriptor]) -> dict[str, object]:
    """Option = candidate name; description = its declared capabilities."""
    return {descriptor.name: _capabilities(descriptor) for descriptor in candidates}


def _capabilities(descriptor: BackendDescriptor) -> dict[str, object]:
    """Declared capability facts only — no document content, no prose."""
    capabilities = descriptor.capabilities
    return {
        "family": "ocr" if is_ocr(descriptor) else "native",
        "requires_gpu": capabilities.requires_gpu,
        "estimated_vram_gb": capabilities.estimated_vram_gb,
        "formats": list(capabilities.supported_formats),
    }


def _rank_from_response(response: _Response, candidates: Sequence[BackendDescriptor]) -> list[str]:
    """Calibrated ranking, best first; every candidate present, none invented."""
    answer = response.choices.get(_QUESTION_ID)
    if answer is None:
        msg = f"typesafe answered no choice for question {_QUESTION_ID!r}"
        raise JevJudgeError(msg)
    probabilities = answer.probabilities

    def _probability(descriptor: BackendDescriptor) -> float:
        raw = probabilities.get(descriptor.name)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return 0.0  # omitted candidate: last, ties keep the planner's name order

    # sorted() is stable → equal probabilities keep candidates' (name-sorted)
    # input order, so identical inputs give identical orders.
    return [descriptor.name for descriptor in sorted(candidates, key=lambda descriptor: -_probability(descriptor))]
