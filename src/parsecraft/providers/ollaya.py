"""Ollaya-backed :class:`RoutingJudge` — calibrated probabilities, never text.

Wire format verified live against ollaya 0.7.3 (2026-09-27, default daemon
``http://localhost:11435``, override with ``OLLAYA_BASE_URL``):

``POST {base}/api/decide``
    request  ``{"model", "state": {..}, "questions": {"lead": {"type":
             "choice", "criteria": {<candidate>: <description>, ..} ≥2}}}``
    response ``{"answers": {"lead": {"type": "choice", "choice", "confidence",
             "probabilities": {<option>: p, ..}}}, "routing": {..}, timings..}``

``criteria`` is the candidate→description map (a dict, minimum 2 entries);
``probabilities`` is the calibrated ranking basis. The ``routing`` block shows
the daemon's script/language routing (``laya`` fans out to ``laya:en`` /
``laya:multilingual``). The judge only re-ranks — ``plan_route`` still
validates every order against eligibility.

The daemon is reached with stdlib ``urllib`` at call time only: importing
this module touches no network and pulls no third-party dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Sequence

from parsecraft.backends.protocol import BackendDescriptor
from parsecraft.routing.judge import JudgeSpec, RoutingJudge
from parsecraft.routing.models import Intent, RoutingError
from parsecraft.routing.rules import is_ocr

#: Reachable without OLLAYA_BASE_URL (pc-1's documented default port).
DEFAULT_BASE_URL = "http://localhost:11435"

#: Single choice question id — the judge only ever asks who should lead.
_QUESTION_ID = "lead"
#: One POST covers the daemon's CPU cold-load too (measured: 21 s to load
#: laya:en after the scheduler unloaded it; warm answers are sub-second).
_TIMEOUT_S = 60.0
_BASE_URL_ENV = "OLLAYA_BASE_URL"

#: BCP-47 candidates offered to the model (the server needs >=2 criteria).
DEFAULT_LANGUAGE_CANDIDATES: tuple[str, ...] = (
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "it",
    "ja",
    "ko",
    "nl",
    "pl",
    "pt",
    "ru",
    "tr",
    "vi",
    "zh",
)
#: Question id for the detector's typed choice question.
_LANGUAGE_QUESTION = "language"
#: Text forwarded as state — enough for script/language routing, bounded for latency.
_SAMPLE_LIMIT = 4000


class OllayaJudgeError(RoutingError):
    """The ollaya daemon was unreachable or spoke an unknown dialect."""


class OllayaJudge:
    """Ranks eligible candidates by calibrated choice probabilities."""

    def __init__(self, *, model: str, base_url: str) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")

    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        if len(candidates) < 2:
            # ollaya's choice questions need ≥2 criteria entries; one candidate
            # has nothing to rank.
            return [descriptor.name for descriptor in candidates]
        payload: dict[str, object] = {
            "model": self._model,
            "state": {
                "intent": intent.value,
                "candidates": ",".join(descriptor.name for descriptor in candidates),
            },
            "questions": {
                _QUESTION_ID: {
                    "type": "choice",
                    "criteria": {descriptor.name: _describe(descriptor, intent) for descriptor in candidates},
                }
            },
        }
        try:
            response = _post_json(f"{self._base_url}/api/decide", payload, timeout_s=_TIMEOUT_S)
        except (OSError, ValueError) as exc:
            msg = f"ollaya daemon unreachable at {self._base_url}: {exc}"
            raise OllayaJudgeError(msg) from exc
        return _rank_from_response(response, candidates)


class OllayaLanguageDetector:
    """laya-backed detector: a calibrated choice question over BCP-47 candidates.

    Implements ``routing.language.LanguageDetector`` — detection stays
    opt-in and outside the deterministic core (the core only reads
    ``RoutingConstraints.language``).
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        min_confidence: float = 0.5,
        candidates: Sequence[str] = DEFAULT_LANGUAGE_CANDIDATES,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._min_confidence = min_confidence
        self._candidates = tuple(candidates)

    def detect_language(self, text: str) -> str | None:
        """BCP-47 tag for ``text``; ``None`` when calibrated confidence is low.

        Raises :class:`OllayaJudgeError` when the daemon is unreachable or the
        answer violates the wire shape — never a silent fallback guess.
        """
        if len(self._candidates) < 2:
            msg = "language detection needs at least two candidate tags"
            raise OllayaJudgeError(msg)
        payload: dict[str, object] = {
            "model": self._model,
            "state": {"sample": text[:_SAMPLE_LIMIT]},
            "questions": {
                _LANGUAGE_QUESTION: {
                    "type": "choice",
                    "criteria": {tag: f"the document text is written in {tag}" for tag in self._candidates},
                }
            },
        }
        try:
            response = _post_json(f"{self._base_url}/api/decide", payload, timeout_s=_TIMEOUT_S)
        except (OSError, ValueError) as exc:
            msg = f"ollaya daemon unreachable at {self._base_url}: {exc}"
            raise OllayaJudgeError(msg) from exc
        answer = _choice_answer(response, _LANGUAGE_QUESTION)
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        if not isinstance(choice, str) or choice not in self._candidates:
            msg = f"ollaya chose a language outside the candidate set: {choice!r}"
            raise OllayaJudgeError(msg)
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and float(confidence) >= self._min_confidence:
            return choice
        return None  # calibrated uncertainty means 'not identified' — never a guess


def load_judge(spec: JudgeSpec) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract)."""
    model = spec.model if spec.variant is None else f"{spec.model}:{spec.variant}"
    return OllayaJudge(model=model, base_url=_configured_base_url())


def _describe(descriptor: BackendDescriptor, intent: Intent) -> str:
    """Candidate facts for the criteria map — declared capabilities only."""
    capabilities = descriptor.capabilities
    family = "OCR" if is_ocr(descriptor) else "native"
    if capabilities.requires_gpu:
        vram = f"~{capabilities.estimated_vram_gb:g} GB VRAM" if capabilities.estimated_vram_gb is not None else "GPU"
        gpu = f"requires a {vram}"
    else:
        gpu = "runs on CPU"
    formats = ", ".join(capabilities.supported_formats) or "no declared formats"
    return f"{family} backend; {gpu}; handles {formats}; page intent {intent.value}"


def _post_json(url: str, payload: dict[str, object], *, timeout_s: float) -> object:
    """POST one JSON body and decode the JSON answer (call-time boundary)."""
    request = urllib.request.Request(  # noqa: S310 — fixed http(s) URL from configuration
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _rank_from_response(response: object, candidates: Sequence[BackendDescriptor]) -> list[str]:
    """Calibrated ranking, best first; every candidate present, none invented.

    Structural checks are deliberate: wire answers are JSON-shaped dicts, not
    typed models — anything unexpected raises instead of guessing.
    """
    answer = _choice_answer(response, _QUESTION_ID)
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        msg = "ollaya choice answer carries no probabilities"
        raise OllayaJudgeError(msg)

    def _probability(descriptor: BackendDescriptor) -> float:
        raw = probabilities.get(descriptor.name)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return 0.0  # absent candidate: last, ties keep the planner's name order

    # sorted() is stable → equal probabilities keep candidates' (name-sorted)
    # input order, so identical inputs give identical orders.
    return [descriptor.name for descriptor in sorted(candidates, key=lambda d: -_probability(d))]


def _choice_answer(response: object, question: str) -> dict[str, object]:
    """Extract one typed choice answer; wire-shape violations raise, never guess."""
    if not isinstance(response, dict):
        msg = f"ollaya returned {type(response).__name__}, expected an object"
        raise OllayaJudgeError(msg)
    answers = response.get("answers")
    if not isinstance(answers, dict):
        msg = "ollaya response carries no 'answers' object"
        raise OllayaJudgeError(msg)
    answer = answers.get(question)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        msg = f"ollaya answered no choice for question {question!r}"
        raise OllayaJudgeError(msg)
    return answer


def load_language_detector(*, model: str = "laya", min_confidence: float = 0.5) -> OllayaLanguageDetector:
    """Build the detector against the configured daemon (opt-in, like ``load_judge``)."""
    return OllayaLanguageDetector(
        model=model,
        base_url=_configured_base_url(),
        min_confidence=min_confidence,
    )


def _configured_base_url() -> str:
    """Daemon base URL from ``OLLAYA_BASE_URL``, else the documented default."""
    return os.environ.get(_BASE_URL_ENV) or DEFAULT_BASE_URL
