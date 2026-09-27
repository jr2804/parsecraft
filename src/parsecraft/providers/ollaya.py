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


def load_judge(spec: JudgeSpec) -> RoutingJudge:
    """Provider entry point required by ``routing.judge_providers`` (pc-2's contract)."""
    model = spec.model if spec.variant is None else f"{spec.model}:{spec.variant}"
    base_url = os.environ.get(_BASE_URL_ENV) or DEFAULT_BASE_URL
    return OllayaJudge(model=model, base_url=base_url)


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
    if not isinstance(response, dict):
        msg = f"ollaya returned {type(response).__name__}, expected an object"
        raise OllayaJudgeError(msg)
    answers = response.get("answers")
    if not isinstance(answers, dict):
        msg = "ollaya response carries no 'answers' object"
        raise OllayaJudgeError(msg)
    answer = answers.get(_QUESTION_ID)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        msg = f"ollaya answered no choice for question {_QUESTION_ID!r}"
        raise OllayaJudgeError(msg)
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
