"""Ollama judge provider: verified wire format offline; live daemon opt-in."""

from __future__ import annotations

import json
import os
import time
import urllib.request

import pytest

from parsecraft.backends.ocr.ovis import DESCRIPTOR as OVIS_DESCRIPTOR
from parsecraft.backends.ocr.tele import DESCRIPTOR as TELE_DESCRIPTOR
from parsecraft.backends.protocol import AnalysisResult, BackendCapabilities, BackendDescriptor, PageSignal
from parsecraft.providers import ollaya as ollaya_module
from parsecraft.providers.ollaya import (
    DEFAULT_BASE_URL,
    OllayaJudge,
    OllayaJudgeError,
    load_judge,
)
from parsecraft.routing.judge import JudgeSpec, RoutingJudge
from parsecraft.routing.judge_providers import resolve_judge
from parsecraft.routing.models import Intent, RoutingConstraints, RoutingError
from parsecraft.routing.planner import plan_route

_PostSpy = list[tuple[str, dict[str, object]]]


# ── Ranking semantics ───────────────────────────────────────────────────────────


# ── The HTTP seam itself (offline: urlopen faked, real _post_json exercised) ────


class _CannedResponse:
    """Minimal context-manager response for the urlopen seam."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _CannedResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


# ── load_judge / spec mapping ───────────────────────────────────────────────────


def test_load_judge_maps_spec_model_variant_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAYA_BASE_URL", raising=False)
    judge = load_judge(JudgeSpec(provider="ollaya", model="laya", variant="typed-decisions"))
    captured = _post_that(monkeypatch, _response({"a": 0.6, "b": 0.4}))
    judge.rank(Intent.OCR_GENERAL, [_descriptor("a"), _descriptor("b")])
    url, payload = captured[0]
    assert url == f"{DEFAULT_BASE_URL}/api/decide"
    assert payload["model"] == "laya:typed-decisions"


def test_base_url_env_override_is_honored_and_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAYA_BASE_URL", "http://127.0.0.1:9999/")
    judge = load_judge(JudgeSpec(provider="ollaya", model="laya", variant=None))
    captured = _post_that(monkeypatch, _response({"a": 1.0, "b": 0.0}))
    judge.rank(Intent.NATIVE, [_descriptor("a", vram=None, group=None), _descriptor("b", vram=None, group=None)])
    url, payload = captured[0]
    assert url == "http://127.0.0.1:9999/api/decide"
    assert payload["model"] == "laya"


def test_resolve_judge_uses_the_lazy_module_path() -> None:
    judge = resolve_judge("ollaya/laya")
    assert isinstance(judge, OllayaJudge)
    assert isinstance(judge, RoutingJudge)


# ── Wire payload (verified shape) ───────────────────────────────────────────────


def test_wire_payload_matches_the_verified_decide_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    candidates = [_descriptor("ocr-a", vram=1.0), _descriptor("ocr-b", vram=4.5)]
    captured = _post_that(monkeypatch, _response({"ocr-a": 0.7, "ocr-b": 0.3}))
    judge.rank(Intent.OCR_VISION, candidates)
    _url, payload = captured[0]
    assert set(payload) == {"model", "state", "questions"}
    state = payload["state"]
    assert isinstance(state, dict)
    assert state["intent"] == Intent.OCR_VISION.value
    assert state["candidates"] == "ocr-a,ocr-b"
    questions = payload["questions"]
    assert isinstance(questions, dict)
    lead = questions["lead"]
    assert isinstance(lead, dict)
    assert lead["type"] == "choice"
    assert "options" not in lead  # criteria (candidate → description) carries the choice set
    criteria = lead["criteria"]
    assert isinstance(criteria, dict)
    assert set(criteria) == {"ocr-a", "ocr-b"}
    description = criteria["ocr-a"]
    assert isinstance(description, str)
    assert "OCR backend" in description
    assert "1 GB VRAM" in description
    assert Intent.OCR_VISION.value in description


def test_descriptions_declare_cpu_and_formats_for_native_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    native = BackendDescriptor(
        name="native-x",
        capabilities=BackendCapabilities(
            supported_formats=["text/plain"],
            requires_gpu=False,
            optional_dependency_group=None,
        ),
    )
    captured = _post_that(monkeypatch, _response({"native-x": 0.9, "ocr-y": 0.1}))
    judge.rank(Intent.NATIVE, [native, _descriptor("ocr-y")])
    _url, payload = captured[0]
    questions = payload["questions"]
    assert isinstance(questions, dict)
    lead = questions["lead"]
    assert isinstance(lead, dict)
    criteria = lead["criteria"]
    assert isinstance(criteria, dict)
    native_description = criteria["native-x"]
    assert isinstance(native_description, str)
    assert "native backend" in native_description
    assert "runs on CPU" in native_description
    assert "text/plain" in native_description


def test_post_json_sends_the_verified_http_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: float = 0) -> _CannedResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _CannedResponse(json.dumps({"done_reason": "decide"}).encode("utf-8"))

    monkeypatch.setattr(ollaya_module.urllib.request, "urlopen", _fake_urlopen)  # noqa: S310 — seam is faked, no real request happens
    result = ollaya_module._post_json("http://127.0.0.1:9/api/decide", {"model": "laya"}, timeout_s=7.0)
    assert result == {"done_reason": "decide"}
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)  # noqa: S310 — type check only, no request
    assert request.full_url == "http://127.0.0.1:9/api/decide"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    body = request.data
    assert isinstance(body, bytes)
    assert json.loads(body.decode("utf-8")) == {"model": "laya"}
    assert captured["timeout"] == 7.0


def test_rank_orders_by_calibrated_probabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    candidates = [_descriptor("first"), _descriptor("second"), _descriptor("third")]
    _post_that(monkeypatch, _response({"first": 0.1, "second": 0.7, "third": 0.2}))
    order = list(judge.rank(Intent.OCR_GENERAL, candidates))
    assert order == ["second", "third", "first"]
    assert set(order) == {descriptor.name for descriptor in candidates}
    assert len(order) == len(set(order))


def test_rank_keeps_planner_order_for_ties_and_non_numeric_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    candidates = [_descriptor("alpha"), _descriptor("beta"), _descriptor("gamma")]
    # equal / missing / non-numeric probabilities degrade to the input order:
    _post_that(monkeypatch, _response({"alpha": 0.5, "beta": 0.5, "gamma": True}))
    order = list(judge.rank(Intent.OCR_GENERAL, candidates))
    assert order == ["alpha", "beta", "gamma"]


def test_single_candidate_short_circuits_without_a_daemon_call(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)

    def _boom(url: str, payload: dict[str, object], *, timeout_s: float) -> object:
        raise AssertionError("one candidate must not reach the daemon")

    monkeypatch.setattr(ollaya_module, "_post_json", _boom)
    only = [_descriptor("solo")]
    assert list(judge.rank(Intent.OCR_GENERAL, only)) == ["solo"]


# ── Failure paths (typed, never silent) ─────────────────────────────────────────


def test_unreachable_daemon_raises_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = OllayaJudge(model="laya", base_url="http://127.0.0.1:9")
    _post_that(monkeypatch, OSError("connection refused"))
    with pytest.raises(OllayaJudgeError, match="http://127.0.0.1:9") as excinfo:
        judge.rank(Intent.OCR_GENERAL, [_descriptor("a"), _descriptor("b")])
    assert isinstance(excinfo.value, RoutingError)


@pytest.mark.parametrize(
    "response",
    [
        "not-a-dict",
        {"no": "answers"},
        {"answers": {}},
        {"answers": {"lead": {"type": "score", "score": 1.0}}},
        {"answers": {"lead": {"type": "choice"}}},
    ],
)
def test_malformed_responses_raise_a_typed_error(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    _post_that(monkeypatch, response)
    with pytest.raises(OllayaJudgeError):
        judge.rank(Intent.OCR_GENERAL, [_descriptor("a"), _descriptor("b")])


def _descriptor(name: str, *, vram: float | None = 1.0, group: str | None = "ocr-fake") -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(
            supported_formats=["application/pdf"],
            requires_gpu=vram is not None,
            estimated_vram_gb=vram,
            optional_dependency_group=group,
        ),
    )


# ── End-to-end through the planner (the actual contract) ─────────────────────────


def test_plan_route_accepts_the_judge_for_a_scanned_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scanned-PDF source (application/pdf) must be able to route to OCR.

    This is the format-vocabulary bug's regression: OCR descriptors declare
    MIME, constraints carry the source media type.
    """
    analysis = AnalysisResult(
        source_hash="a" * 64,
        page_count=1,
        signals=[
            PageSignal(page_number=1, has_native_text=False, text_chars=0, image_count=6, blank=False),
        ],
    )
    constraints = RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"ocr-ovis", "ocr-tele"},
        vram_budget_gb=8.0,
        allow_ocr=True,
        offline=False,
        max_passes=2,
    )
    _post_that(monkeypatch, _response({"ocr-ovis": 0.75, "ocr-tele": 0.25}))
    judge = OllayaJudge(model="laya", base_url=DEFAULT_BASE_URL)
    plan = plan_route(analysis, [TELE_DESCRIPTOR, OVIS_DESCRIPTOR], constraints, judge=judge)
    page = plan.pages[0]
    assert page.intent is Intent.OCR_VISION
    assert page.candidates == ["ocr-ovis", "ocr-tele"]
    assert page.chosen == "ocr-ovis"
    assert plan.primary == "ocr-ovis"


def _response(probabilities: dict[str, object]) -> dict[str, object]:
    """A verified /api/decide answer shape (2026-09-27, ollaya 0.7.3)."""
    first = next(iter(probabilities))
    return {
        "model": "laya:en",
        "answers": {
            "lead": {
                "type": "choice",
                "choice": first,
                "confidence": 0.9,
                "probabilities": probabilities,
            }
        },
        "routing": {"router": "laya:latest", "model": "laya:en", "route": "english"},
        "done_reason": "decide",
    }


def _post_that(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> _PostSpy:
    """Install a fake wire; returns the spy capturing (url, payload) calls."""
    captured: _PostSpy = []

    def _fake_post(url: str, payload: dict[str, object], *, timeout_s: float) -> object:
        assert timeout_s > 0
        captured.append((url, payload))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ollaya_module, "_post_json", _fake_post)
    return captured


@pytest.mark.judge
def test_live_daemon_ranks_real_candidates(live_daemon: str) -> None:
    judge = load_judge(JudgeSpec(provider="ollaya", model="laya", variant=None))
    candidates = [OVIS_DESCRIPTOR, TELE_DESCRIPTOR]
    # First call warms the model (a cold CPU load takes tens of seconds); the
    # timed call must answer from the warm daemon.
    warm_up = list(judge.rank(Intent.OCR_VISION, candidates))
    assert sorted(warm_up) == sorted(descriptor.name for descriptor in candidates)
    started = time.monotonic()
    order = list(judge.rank(Intent.OCR_VISION, candidates))
    elapsed = time.monotonic() - started
    assert sorted(order) == sorted(descriptor.name for descriptor in candidates)
    assert elapsed < 30.0, f"warm laya should answer in well under 30s, took {elapsed:.1f}s"


@pytest.mark.judge
def test_live_daemon_is_discoverable_via_models_endpoint(live_daemon: str) -> None:
    payload = _daemon_models(live_daemon)
    assert isinstance(payload, dict)
    models = payload["models"]
    assert isinstance(models, list)
    assert models, "daemon reports no decision models"


@pytest.fixture
def live_daemon() -> str:
    """Base URL of a responding ollaya daemon, else skip."""
    base_url = (os.environ.get("OLLAYA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    try:
        payload = _daemon_models(base_url)
    except (OSError, ValueError) as exc:
        pytest.skip(f"no ollaya daemon at {base_url}: {exc}")
    if not isinstance(payload, dict) or "models" not in payload:
        pytest.skip(f"{base_url} answered without a models list")
    return base_url


# ── Live daemon tier (opt-in: --run-judge + a running ollaya) ────────────────────


def _daemon_models(base_url: str) -> object:
    request = urllib.request.Request(f"{base_url}/v1/models")  # noqa: S310 — configured local URL
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))
