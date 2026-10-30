"""System One decision model for text-kind classification.

One Choice question per FILE over three labels; the winning label is the raw
verdict the front module validates into :class:`TextKind`. The SDK is imported
at call time only (via :func:`importlib.import_module`), so importing this
module needs no optional dependency and no network — the offline gate holds.

Unlike the judge seam (which never sends document content), this seam sends
the blob BY DESIGN: there is nothing else to classify. The full text goes
untruncated — truncation would bias the verdict toward the file head.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Protocol, cast

from parsecraft.adapters.classification import (
    TextClassificationError,
    TextClassifierSpec,
)

#: Single choice question id — one question per file, one label back.
QUESTION_ID = "text-kind"

#: The SDK module every System One endpoint is reached through.
_MODULE_NAME = "typesafe_sdk"

#: Option labels. Order carries no meaning: a shared maximum is a tie the
#: model could not decide, and ties raise rather than picking by position.
_OPTIONS: tuple[str, str, str] = ("markdown-like", "data-like", "plain")

_CRITERIA: dict[str, str] = {
    "markdown-like": "headings, lists, tables, code fences, or link/image markup",
    "data-like": "delimiter-separated rows with consistent columns, a table dump",
    "plain": "prose with neither markup structure nor tabular rows",
}

_INSTRUCTIONS = (
    "Classify the document text into exactly one of markdown-like, data-like, "
    "or plain. Markdown-like has headings, lists, tables, code fences, or "
    "link markup. Data-like has delimiter-separated rows with consistent "
    "columns. Plain is prose with neither. Answer with the single best label. "
    "Be deterministic: identical text gets the identical label."
)


class _ChoiceAnswer(Protocol):
    """The ``ChoiceAnswer`` fields this model reads."""

    choice: str
    probabilities: Mapping[str, float]
    confidence: float


class _Response(Protocol):
    """The ``SystemOneResponse`` surface this model reads."""

    model: str
    choices: Mapping[str, _ChoiceAnswer]


class _Client(Protocol):
    """A ``TypeSafeClient``: a context manager with one ``system_one`` call."""

    def __enter__(self) -> _Client: ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...
    def system_one(self, state: Mapping[str, object], questions: Mapping[str, object]) -> _Response: ...


class _SdkModule(Protocol):
    """The ``typesafe_sdk`` surface this model needs (mirrors the judge seam)."""

    def TypeSafeClient(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> _Client: ...

    def Choice(self, *, instructions: str, criteria: Mapping[str, object]) -> object: ...


class SystemOneDecisionModel:
    """One System One Choice call per file, returning the winning label."""

    def __init__(self, spec: TextClassifierSpec, *, api_key: str, base_url: str | None = None) -> None:
        self._spec = spec
        self._api_key = api_key
        self._base_url = base_url

    def decide(self, text: str) -> str:
        """Ask once; the unique maximum wins, a shared maximum raises."""
        try:
            raw = import_module(_MODULE_NAME)
        except ImportError as exc:
            raise TextClassificationError(f"{_MODULE_NAME} is not installed: {exc}") from exc
        if not hasattr(raw, "TypeSafeClient") or not hasattr(raw, "Choice"):
            raise TextClassificationError(f"{_MODULE_NAME} does not export TypeSafeClient/Choice")
        sdk = cast(_SdkModule, raw)
        try:
            client = sdk.TypeSafeClient(api_key=self._api_key, model=self._spec.model, base_url=self._base_url)
            with client as active:
                response = active.system_one(
                    {"task": "classify-text-kind", "text": text},
                    {QUESTION_ID: sdk.Choice(instructions=_INSTRUCTIONS, criteria=_CRITERIA)},
                )
        except TextClassificationError:
            raise
        except Exception as exc:  # SDK boundary — typed, never a raw SDK/transport error
            raise TextClassificationError(f"system one call failed: {type(exc).__name__}: {exc}") from exc
        return _winning_label(response)


def _winning_label(response: _Response) -> str:
    """The unique maximum label; a shared maximum raises (undecided)."""
    answer = response.choices.get(QUESTION_ID)
    if answer is None:
        raise TextClassificationError(f"system one endpoint answered no choice for question {QUESTION_ID!r}")

    def _probability(label: str) -> float:
        raw = answer.probabilities.get(label)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return 0.0

    scored = [(label, _probability(label)) for label in _OPTIONS]
    top = max(score for _, score in scored)
    winners = [label for label, score in scored if score == top]
    if len(winners) == 1:
        return winners[0]
    raise TextClassificationError(f"system one endpoint tied between {', '.join(winners)}; no single verdict")


__all__ = ["SystemOneDecisionModel"]
