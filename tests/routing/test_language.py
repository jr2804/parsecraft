"""Language-aware routing: eligibility narrowing + the injectable detector seam.

Everything here is offline and provider-free: the core's eligibility rule is a
pure function, and the detection seam is a structural protocol.
"""

from __future__ import annotations

import subprocess
import sys

from parsecraft.backends.ocr._models import (
    OVIS_CAPABILITIES,
    QIANFAN_CAPABILITIES,
    TELE_CAPABILITIES,
    UNLIMITED_CAPABILITIES,
)
from parsecraft.backends.protocol import BackendCapabilities, BackendDescriptor
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
        gpu_requirement=0.0,
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


def test_a_plain_callable_satisfies_the_core_protocol() -> None:
    """The seam is structural: any object with detect_language conforms."""

    class _Detector:
        @staticmethod
        def detect_language(text: str) -> str | None:
            return "de" if "ü" in text or "ä" in text or "ö" in text else None

    detector: LanguageDetector = _Detector()
    assert isinstance(detector, LanguageDetector)
    assert detector.detect_language("Grüße") == "de"
    assert detector.detect_language("hello") is None


def test_core_routing_never_imports_a_detector_implementation() -> None:
    """routing/ imports no provider module — detection is injectable, not baked in."""
    proc = subprocess.run(  # noqa: S603 — fixed interpreter, repo-local imports
        [
            sys.executable,
            "-c",
            "import sys; import parsecraft.routing; assert not [m for m in sys.modules if m.startswith('parsecraft.providers')]; print('CORE_OK')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
