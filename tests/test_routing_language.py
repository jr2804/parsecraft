"""Language-aware routing: eligibility narrowing + the injectable detector seam.

Everything here is offline: the ollaya detector is exercised with a faked wire
(`_post_json` patched), and the core's eligibility rule is a pure function.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from parsecraft.backends.ocr._models import (
    OVIS_CAPABILITIES,
    QIANFAN_CAPABILITIES,
    TELE_CAPABILITIES,
    UNLIMITED_CAPABILITIES,
)
from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
from parsecraft.providers import ollaya as ollaya_module
from parsecraft.providers.ollaya import (
    DEFAULT_LANGUAGE_CANDIDATES,
    OllayaJudgeError,
    OllayaLanguageDetector,
    load_language_detector,
)
from parsecraft.routing import LanguageDetector
from parsecraft.routing.models import RoutingConstraints
from parsecraft.routing.rules import is_hard_eligible

# ── Eligibility (rules.is_hard_eligible) ─────────────────────────────────────────


def test_language_request_excludes_only_declaring_backends() -> None:
    declaring = _language_caps("zh", "en")
    assert _eligible(declaring, None) is True  # no request → no restriction
    assert _eligible(declaring, "zh") is True
    assert _eligible(declaring, "en") is True
    assert _eligible(declaring, "de") is False  # declared set does not cover it


def test_language_agnostic_backends_are_never_excluded() -> None:
    """Native and friends declare no languages — a request must not drop them."""
    agnostic = _language_caps()
    assert _eligible(agnostic, None) is True
    assert _eligible(agnostic, "de") is True
    assert _eligible(agnostic, "zh") is True


def test_language_rule_is_deterministic() -> None:
    capabilities = _language_caps("zh", "en")
    results = [_eligible(capabilities, "de") for _ in range(5)]
    assert results == [False] * 5
    agnostic = [_eligible(_language_caps(), "de") for _ in range(5)]
    assert agnostic == [True] * 5


def _eligible(capabilities: BackendCapabilities, language: str | None) -> bool:
    """Eligibility with every other constraint neutralised (isolate language)."""
    constraints = RoutingConstraints(
        language=language,
        allow_ocr=True,
        offline=False,
        vram_budget_gb=8.0,
        installed_extras={"ocr-tele", "ocr-ovis", "ocr-unlimited", "ocr-qianfan"},
    )
    return is_hard_eligible(_descriptor(capabilities), constraints)


def _descriptor(capabilities: BackendCapabilities, name: str = "candidate") -> BackendDescriptor:
    return BackendDescriptor(name=name, capabilities=capabilities)


def _language_caps(*languages: str) -> BackendCapabilities:
    return BackendCapabilities(
        supported_formats=["application/pdf"],
        requires_gpu=False,
        languages=tuple(languages),
    )


def test_declared_capability_languages_match_the_model_cards() -> None:
    # TeleOCR's HF cardData.language is exactly zh + en (verified 2026-09-27):
    assert TELE_CAPABILITIES.languages == ("zh", "en")
    # Cards that say nothing (Ovis) or "multilingual" (Unlimited, Qianfan's 192
    # languages) stay language-agnostic on purpose: set-membership cannot
    # express broad coverage, and agnostic is the fail-safe direction.
    assert OVIS_CAPABILITIES.languages == ()
    assert UNLIMITED_CAPABILITIES.languages == ()
    assert QIANFAN_CAPABILITIES.languages == ()


def test_constraints_language_defaults_to_unrestricted() -> None:
    assert RoutingConstraints().language is None
    assert RoutingConstraints(language="de").language == "de"


# ── The injectable detection seam ─────────────────────────────────────────────────


def test_detector_satisfies_the_core_protocol() -> None:
    detector = load_language_detector()
    assert isinstance(detector, LanguageDetector)
    assert isinstance(detector, OllayaLanguageDetector)


def test_core_routing_never_imports_the_provider() -> None:
    """routing/ imports no provider module — detection is injectable, not baked in."""
    proc = subprocess.run(  # noqa: S603 — fixed interpreter, repo-local imports
        [
            sys.executable,
            "-c",
            "import sys; import parsecraft.routing; assert 'parsecraft.providers.ollaya' not in sys.modules; print('CORE_OK')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CORE_OK" in proc.stdout


def test_detector_returns_the_tag_when_confidence_is_high(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _detector(monkeypatch, _language_response("de", 0.93))
    detector = load_language_detector()
    assert detector.detect_language("Guten Tag, wie geht es Ihnen?") == "de"
    payload = payloads[0]
    assert payload["model"] == "laya"
    questions = payload["questions"]
    assert isinstance(questions, dict)
    language = questions["language"]
    assert isinstance(language, dict)
    assert language["type"] == "choice"
    criteria = language["criteria"]
    assert isinstance(criteria, dict)
    assert set(criteria) == set(DEFAULT_LANGUAGE_CANDIDATES)


def test_detector_returns_none_below_the_confidence_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    _detector(monkeypatch, _language_response("fr", 0.31))
    detector = load_language_detector(min_confidence=0.5)
    assert detector.detect_language("some words") is None  # calibrated doubt = not identified


def test_detector_truncates_the_text_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _detector(monkeypatch, _language_response("en", 0.9))
    detector = load_language_detector()
    detector.detect_language("x" * 10_000)
    state = payloads[0]["state"]
    assert isinstance(state, dict)
    sample = state["sample"]
    assert isinstance(sample, str)
    assert len(sample) == 4000


def test_detector_rejects_choices_outside_the_candidate_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _detector(monkeypatch, _language_response("klingon", 0.99))
    detector = load_language_detector()
    with pytest.raises(OllayaJudgeError, match="outside the candidate set"):
        detector.detect_language("text")


def test_detector_raises_typed_when_the_daemon_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _detector(monkeypatch, OSError("connection refused"))
    detector = load_language_detector()
    with pytest.raises(OllayaJudgeError, match="daemon unreachable"):
        detector.detect_language("text")


def test_detector_needs_at_least_two_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _detector(monkeypatch, _language_response("en", 0.9))
    detector = OllayaLanguageDetector(
        model="laya",
        base_url="http://localhost:11435",
        candidates=("en",),
    )
    with pytest.raises(OllayaJudgeError, match="at least two candidate tags"):
        detector.detect_language("text")
    assert payloads == []  # rejected before any HTTP


def test_detector_is_deterministic_for_identical_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    _detector(monkeypatch, _language_response("ja", 0.88))
    detector = load_language_detector()
    assert detector.detect_language("same input") == detector.detect_language("same input")


def _detector(monkeypatch: pytest.MonkeyPatch, response: object) -> list[dict[str, object]]:
    """Fake the daemon wire; returns the captured payloads."""
    captured: list[dict[str, object]] = []

    def _fake_post(url: str, payload: dict[str, object], *, timeout_s: float) -> object:
        assert url.endswith("/api/decide")
        assert timeout_s > 0
        captured.append(payload)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ollaya_module, "_post_json", _fake_post)
    return captured


def test_detector_honors_the_base_url_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAYA_BASE_URL", "http://127.0.0.1:9999/")
    urls: list[str] = []

    def _fake_post(url: str, _payload: dict[str, object], *, timeout_s: float) -> object:
        assert timeout_s > 0
        urls.append(url)
        return _language_response("es", 0.99)

    monkeypatch.setattr(ollaya_module, "_post_json", _fake_post)
    detector = load_language_detector()
    assert detector.detect_language("hola") == "es"
    assert urls[0].startswith("http://127.0.0.1:9999/api/decide")  # env + slash normalization


def _language_response(choice: str, confidence: float) -> dict[str, object]:
    return {"answers": {"language": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {choice: confidence}}}}
