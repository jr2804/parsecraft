"""Opt-in caption attribution over an existing :class:`DocumentResult`.

Two public functions, deliberately separate:

- :func:`attribute_captions` — one batched System One request per document
  that labels ambiguous TABLE/FIGURE paragraphs as captions. It appends
  ``caption_of`` relations only: no chunk is created, deleted, reclassified,
  or rewritten, and no text is copied anywhere. Off means never calling it.
- :func:`link_same_span_captions` — deterministic structural linking for
  already-explicit caption pairs (a CAPTION chunk sharing its exact
  ``source_span`` with an adjacent FIGURE/TABLE). No model, no network.

Only Jev-serving providers are supported (``typesafe-ai``, ``zen``). The
local endpoint (``ollama``/Nimble/Tev) is a different model family with
cold-start semantics this batch path does not cover, so requesting it fails
instead of degrading silently. Model tokens stay verbatim per provider
doctrine; the base URL comes only from the standard chain; the SDK default
timeout applies (cloud endpoints only).

Privacy: :func:`attribute_captions` sends bounded text snippets (the target
table/figure text and the candidate paragraph, each truncated to a documented
cap, with truncation flagged in the request) to the configured Jev endpoint.
Cloud endpoints traverse the network. The request carries no credentials
beyond the endpoint's own authentication, and this module never logs or
prints secrets.

Uncalibrated by design: answer probabilities are a selection signal only —
this module applies no numeric floor and reports nothing as validated or
calibrated. A pair links only when its answer is strictly well-formed: exact
option keys, finite in-range scores summing to approximately unity, a unique
maximum, and a ``choice`` naming that maximum. Anything else abstains; a
unusable response envelope fails the run. Malformed answers never produce links.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from math import isfinite
from typing import Protocol, cast

from parsecraft.adapters.errors import AdapterError
from parsecraft.ir.models import ChunkKind, ChunkRelation, DocumentResult, RelationKind, StructuredChunk
from parsecraft.routing.judge_providers import JudgeSpecError

#: The only providers this feature resolves — Jev-serving endpoints. A named
#: provider outside this set fails instead of substituting, and the local
#: endpoint stays out: different model family, cold-start semantics.
CAPTION_PROVIDERS: tuple[str, str] = ("typesafe-ai", "zen")

_ENDPOINTS_MODULE = "parsecraft.providers._endpoints"
_JUDGE_PROVIDERS_MODULE = "parsecraft.routing.judge_providers"
_SDK_MODULE_NAME = "typesafe_sdk"

#: Question labels — the only answers that can produce a link.
_LABEL_CAPTION = "caption"
_LABEL_NONE = "none"
_OPTION_LABELS: tuple[str, str] = (_LABEL_CAPTION, _LABEL_NONE)

#: Scores must sum to unity within one percentage point — the API promises
#: only "approximately 1", so exact equality would abstain valid answers.
_UNIT_SUM_TOLERANCE = 0.01

#: Request bounds: snippets are truncated to these caps and flagged.
_MAX_TARGET_CHARS = 2000
_MAX_CANDIDATE_CHARS = 1000


class CaptionFailure(StrEnum):
    """Why :func:`attribute_captions` produced no attributions from the endpoint."""

    UNCONFIGURED = "unconfigured"
    INVALID_SPEC = "invalid_spec"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    UNAVAILABLE = "unavailable"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True, slots=True)
class CaptionReport:
    """Outcome of :func:`attribute_captions` — counts, never document content."""

    attributed: int
    abstained: int
    failure: CaptionFailure | None = None


@dataclass(frozen=True, slots=True)
class _Pair:
    """One undecided caption candidacy: paragraph chunk before a target chunk."""

    page_index: int
    paragraph_index: int
    paragraph_id: str
    paragraph_text: str
    target_id: str
    target_kind: str
    target_text: str


class CaptionError(AdapterError):
    """Internal caption-attribution error (surfaced only for programming bugs)."""


class ChoiceAnswer(Protocol):
    """Public seam: the SDK's typed choice answer (``.choices`` values)."""

    @property
    def choice(self) -> str: ...
    @property
    def probabilities(self) -> Mapping[str, float]: ...


class SystemOneResponse(Protocol):
    """Public seam: the SDK's typed System One response."""

    @property
    def choices(self) -> Mapping[str, ChoiceAnswer]: ...


class SystemOneClient(Protocol):
    """Public seam: the SDK client surface this module drives."""

    def __enter__(self) -> SystemOneClient: ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...
    def system_one(self, state: object, questions: object) -> SystemOneResponse: ...


class SystemOneSdk(Protocol):
    """Public seam: the ``typesafe_sdk`` module surface, imported at call time."""

    def TypeSafeClient(self, **kwargs: object) -> SystemOneClient: ...
    def Choice(self, *, instructions: str, criteria: object) -> object: ...


def attribute_captions(document: DocumentResult, *, model: str | None = None) -> CaptionReport:
    """Attribute existing caption paragraphs to their tables/figures via Jev.

    One batched System One request per document over every undecided pair;
    answers are validated strictly (see :func:`_strict_winner`) and applied
    only afterwards, so an unusable response envelope changes nothing. Each linked pair
    appends one ``caption_of`` relation on the paragraph chunk — chunks keep
    their kinds, text, and metadata untouched.

    Returns counts plus a :class:`CaptionFailure` when the endpoint could not
    be used; on failure the document remains unchanged. Runs after
    ``execute``/cache at the caller's discretion — the caller owns the caching
    model (attribute-then-cache for stability); no cached reproducibility is
    claimed.
    """
    pairs = _candidate_pairs(document)
    if not pairs:
        return CaptionReport(attributed=0, abstained=0)
    resolved = _resolve_endpoint(model)
    if isinstance(resolved, CaptionFailure):
        return CaptionReport(attributed=0, abstained=0, failure=resolved)
    api_key, model_token, base_url = resolved
    try:
        sdk = cast(SystemOneSdk, import_module(_SDK_MODULE_NAME))
        state_pairs: list[dict[str, object]] = []
        questions: dict[str, object] = {}
        for index, pair in enumerate(pairs):
            target_text, target_cut = _truncate(pair.target_text, _MAX_TARGET_CHARS)
            candidate_text, candidate_cut = _truncate(pair.paragraph_text, _MAX_CANDIDATE_CHARS)
            state_pairs.append(
                {
                    "target_kind": pair.target_kind,
                    "target_text": target_text,
                    "target_truncated": target_cut,
                    "candidate_text": candidate_text,
                    "candidate_truncated": candidate_cut,
                }
            )
            questions[f"pair_{index}"] = sdk.Choice(
                instructions=_question_instructions(index, pair, target_cut, candidate_cut),
                criteria=_choice_criteria(),
            )
    except Exception:
        return CaptionReport(attributed=0, abstained=0, failure=CaptionFailure.UNAVAILABLE)
    try:
        client_factory = _open_client(api_key=api_key, model=model_token, base_url=base_url)
    except Exception:
        return CaptionReport(attributed=0, abstained=0, failure=CaptionFailure.UNAVAILABLE)
    try:
        with client_factory as client:
            response = client.system_one({"pairs": state_pairs}, questions)
        choices = response.choices
    except Exception:
        return CaptionReport(attributed=0, abstained=0, failure=CaptionFailure.TRANSPORT_ERROR)
    attributed_pairs, abstained = _valid_attributions(pairs, choices)
    for pair in attributed_pairs:
        paragraph = document.pages[pair.page_index].blocks[pair.paragraph_index]
        paragraph.relations.append(ChunkRelation(kind=RelationKind.CAPTION_OF, target_id=pair.target_id))
    return CaptionReport(attributed=len(attributed_pairs), abstained=abstained)


def _candidate_pairs(document: DocumentResult) -> list[_Pair]:
    """Collect undecided caption candidacies in document order.

    A candidacy is a caption-role chunk immediately preceding a target-role
    sibling on the same page, excluding pairs already linked (repeat calls ask
    fewer questions and never duplicate relations).
    """
    pairs: list[_Pair] = []
    for page_index, page in enumerate(document.pages):
        for position in range(1, len(page.blocks)):
            paragraph = page.blocks[position - 1]
            target = page.blocks[position]
            if not _caption_role(paragraph.kind) or not _target_role(target.kind):
                continue
            if _already_linked(paragraph, target.id):
                continue
            pairs.append(
                _Pair(
                    page_index=page_index,
                    paragraph_index=position - 1,
                    paragraph_id=paragraph.id,
                    paragraph_text=paragraph.content,
                    target_id=target.id,
                    target_kind=target.kind.value,
                    target_text=target.content,
                )
            )
    return pairs


def _caption_role(kind: ChunkKind) -> bool:
    """Chunks that may play the caption role: visible prose, never reclassified."""
    return kind in (ChunkKind.PARAGRAPH, ChunkKind.CAPTION)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cap a snippet, reporting whether anything was cut."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _resolve_endpoint(model: str | None) -> tuple[str, str, str | None] | CaptionFailure:
    """Resolve ``(api_key, model, base_url)`` or a failure class — never throws.

    Spec order mirrors the judge chain (explicit argument, generic env, file);
    the provider must be Jev-serving, otherwise the failure names it instead
    of silently substituting another. Any resolution failure — malformed spec
    excepted — maps to :attr:`CaptionFailure.UNAVAILABLE` without leaking
    detail. Nothing here touches the network: client construction only
    validates config and builds an HTTP client.
    """
    try:
        endpoints = import_module(_ENDPOINTS_MODULE)
        spec_text = model.strip() if model is not None and model.strip() else endpoints.select_judge_spec(None)
        if not spec_text:
            return CaptionFailure.UNCONFIGURED
        judge_providers = import_module(_JUDGE_PROVIDERS_MODULE)
        try:
            parsed = judge_providers.parse_judge_spec(spec_text)
        except JudgeSpecError:
            return CaptionFailure.INVALID_SPEC
        if parsed.provider not in CAPTION_PROVIDERS:
            return CaptionFailure.UNSUPPORTED_PROVIDER
        provider_module = import_module(f"parsecraft.providers.{parsed.provider.replace('-', '_')}")
        profile = provider_module.PROFILE
        base_url, api_key = endpoints.resolve_endpoint(
            parsed.provider,
            default_base_url=profile.base_url,
            legacy_api_key_env=profile.api_key_env,
        )
        if api_key is None or not api_key.strip():
            return CaptionFailure.UNAVAILABLE
        return api_key, parsed.model, base_url
    except Exception:
        return CaptionFailure.UNAVAILABLE


def _open_client(*, api_key: str, model: str, base_url: str | None) -> SystemOneClient:
    """Build the real SDK client — the one seam tests patch for transport."""
    sdk = cast(SystemOneSdk, import_module(_SDK_MODULE_NAME))
    return sdk.TypeSafeClient(api_key=api_key, model=model, base_url=base_url)


def _question_instructions(index: int, pair: _Pair, target_truncated: bool, candidate_truncated: bool) -> str:
    """Per-pair question text naming the pair it judges."""
    notes: list[str] = []
    if target_truncated:
        notes.append("the target text is truncated to its first characters")
    if candidate_truncated:
        notes.append("the candidate paragraph is truncated to its first characters")
    scope = f" ({'; '.join(notes)})" if notes else ""
    return (
        f"Pair {index}: does the candidate paragraph caption the {pair.target_kind} as a unit"
        f" — labelling it (for example a 'Table N' or 'Figure N' reference) or describing its"
        f" contents{scope}? Answer with the single best label; identical text gets an identical"
        f" label. 'caption' only for a genuine caption, 'none' for unrelated, incidental, or"
        f" off-target text."
    )


def _choice_criteria() -> dict[str, str]:
    """The closed option set — the only labels that can produce a link."""
    return {
        _LABEL_CAPTION: "the paragraph captions the table or figure as a unit",
        _LABEL_NONE: "the paragraph is unrelated, incidental, or describes something else",
    }


def _valid_attributions(pairs: list[_Pair], choices: Mapping[str, ChoiceAnswer]) -> tuple[list[_Pair], int]:
    """Validate every answer before any write; unknown keys are ignored."""
    attributed: list[_Pair] = []
    abstained = 0
    for index, pair in enumerate(pairs):
        try:
            answer = choices.get(f"pair_{index}")
            winner: str | None = None
            if answer is not None:
                winner = _strict_winner(answer.probabilities)
                if winner is not None and answer.choice != winner:
                    winner = None
        except Exception:
            winner = None
        if winner == _LABEL_CAPTION:
            attributed.append(pair)
        else:
            abstained += 1
    return attributed, abstained


def _strict_winner(probabilities: Mapping[str, float]) -> str | None:
    """The winning label, or ``None`` when the distribution is not well-formed.

    The SDK's strict parsing already guarantees the mapping shape and float
    values, so this checks the remaining well-formedness: exactly the two
    option keys, finite in-range scores summing to approximately unity, and a
    unique maximum (ties included as abstentions).
    """
    if set(probabilities.keys()) != set(_OPTION_LABELS):
        return None
    caption_score = probabilities[_LABEL_CAPTION]
    none_score = probabilities[_LABEL_NONE]
    for score in (caption_score, none_score):
        if not isfinite(score) or score < 0.0 or score > 1.0:
            return None
    if abs(caption_score + none_score - 1.0) > _UNIT_SUM_TOLERANCE:
        return None
    if caption_score == none_score:
        return None
    return _LABEL_CAPTION if caption_score > none_score else _LABEL_NONE


def link_same_span_captions(document: DocumentResult) -> int:
    """Link already-explicit caption pairs sharing one exact source span.

    A CAPTION chunk with a non-null ``source_span`` equal to an adjacent
    FIGURE/TABLE chunk's span gets that relation — separately from (and never
    confused with) model-selected paragraphs. Deterministic, no endpoint.
    Returns the number of relations appended.
    """
    linked = 0
    for page in document.pages:
        for position, chunk in enumerate(page.blocks):
            if chunk.kind is not ChunkKind.CAPTION or chunk.source_span is None:
                continue
            neighbours: list[StructuredChunk] = []
            if position > 0:
                neighbours.append(page.blocks[position - 1])
            if position + 1 < len(page.blocks):
                neighbours.append(page.blocks[position + 1])
            for neighbour in neighbours:
                if not _target_role(neighbour.kind) or neighbour.source_span != chunk.source_span:
                    continue
                if _already_linked(chunk, neighbour.id):
                    continue
                chunk.relations.append(ChunkRelation(kind=RelationKind.CAPTION_OF, target_id=neighbour.id))
                linked += 1
    return linked


def _target_role(kind: ChunkKind) -> bool:
    """Chunks that may play the caption target role."""
    return kind in (ChunkKind.TABLE, ChunkKind.FIGURE)


def _already_linked(paragraph: StructuredChunk, target_id: str) -> bool:
    """Whether the paragraph already carries this exact caption relation."""
    return any(relation.kind is RelationKind.CAPTION_OF and relation.target_id == target_id for relation in paragraph.relations)
