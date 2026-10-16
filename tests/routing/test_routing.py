"""Offline tests for the auto-mode routing harness (planner + judge + rules)."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendDescriptor,
    Diagnostic,
    ModelAssetDescriptor,
)
from parsecraft.ir.models import DiagnosticLevel, PageSignal
from parsecraft.routing import (
    DeterministicJudge,
    Intent,
    JudgeViolationError,
    NoEligibleBackendError,
    PageContext,
    PageRoute,
    RoutingConstraints,
    RoutingError,
    RoutingPreference,
    plan_route,
)
from parsecraft.routing.judge import DENSITY_REFERENCE_CHARS, PREFERRED_BACKENDS, RoutingJudge, page_context
from parsecraft.routing.planner import _apply_preference, _default_lead_cap, _lead_tier_cap
from parsecraft.routing.rules import (
    DEGRADED_BLANK_PAGE_CODE,
    FEATURE_EQUATIONS_CODE,
    FEATURE_FIGURES_CODE,
    FEATURE_TABLE_CODE,
    INTENT_RULES,
    FeatureHints,
    can_degrade_to_native,
    classify_page,
    extract_hints,
    in_intent_family,
    is_hard_eligible,
    is_ocr,
    page_needs_ocr,
)

ALL_FORMATS = {"text/plain", "text/markdown", "text/html", "application/pdf", "text/csv", "application/json"}
OCR_EXTRAS = {"ocr-ovis", "ocr-tele", "ocr-unlimited", "ocr-qianfan"}
OCR_NAMES = ("ocr-ovis", "ocr-qianfan", "ocr-tele", "ocr-unlimited")
NATIVE_NAMES = ("native-html", "native-markdown", "pymupdf", "native-text")

_SOURCES_PATH = Path(__file__).parent.parent / "fixtures" / "sources.toml"
# Module constant, not a helper call: decorators evaluate at import time and
# pyreorder's stepdown reorders functions (callers before callees).
_MANIFEST_SOURCES: list[dict[str, Any]] = list(tomllib.loads(_SOURCES_PATH.read_text(encoding="utf-8"))["sources"])


_EXPECTED_INTENT_BY_ID: dict[str, Intent] = {
    "gutenberg-pride-prejudice": Intent.NATIVE,
    "rfc5322": Intent.NATIVE,
    "commonmark-spec": Intent.NATIVE,
    "wikidata-entity-q42": Intent.NATIVE,
    "whatwg-html-standard": Intent.OCR_VISION,  # images → figures hint
    "wikipedia-calculus": Intent.OCR_VISION,  # single page → tables rule can't fire
    "nist-sp-800-53r5": Intent.OCR_TABLES,  # 492 pages + tables
    "itu-t-p863": Intent.OCR_TABLES,  # tables beat equations by precedence
    "etsi-ts-103558": Intent.OCR_TABLES,
}
_UNCOVERED_FORMATS = {"csv", "json"}  # no backend supports these yet


# ── models ─────────────────────────────────────────────────────────────────


def test_native_pages_prefer_the_native_family_over_extra_backed_parsers() -> None:
    """Name order must not hand plain text to a heavyweight parser.

    Every parser claims ``text/plain``-family MIME, and the deterministic judge
    ranks the NATIVE intent by execution tier — dependency-free native family
    first, extra-backed parsers after — or a heavyweight engine wins by
    alphabetical accident the moment it is installed.
    """

    def _descriptor(name: str, group: str | None) -> BackendDescriptor:
        return BackendDescriptor(
            name=name,
            version="1.0.0",
            capabilities=BackendCapabilities(
                supported_formats=["text/plain"],
                optional_dependency_group=group,
            ),
        )

    candidates = [
        _descriptor("docling", "docling"),
        _descriptor("liteparse", "liteparse"),
        _descriptor("native-text", None),
    ]
    ranked = DeterministicJudge().rank(Intent.NATIVE, candidates)
    assert ranked[0] == "native-text"
    assert set(ranked[1:]) == {"docling", "liteparse"}


def test_intent_values() -> None:
    assert [intent.value for intent in Intent] == ["native", "ocr", "ocr-tables", "ocr-vision"]


def test_constraints_defaults_and_bounds() -> None:
    defaults = RoutingConstraints()
    assert defaults.formats == set()
    assert defaults.installed_extras == set()
    assert defaults.vram_budget_gb == 0.0
    assert defaults.max_passes == 1
    assert defaults.allow_ocr is True
    assert defaults.offline is True
    with pytest.raises(ValidationError):
        RoutingConstraints(vram_budget_gb=-1.0)
    with pytest.raises(ValidationError):
        RoutingConstraints(max_passes=0)


def test_pageroute_chosen_must_be_first_candidate() -> None:
    with pytest.raises(ValidationError, match="must be candidates\\[0\\]"):
        PageRoute(page_number=1, intent=Intent.NATIVE, candidates=["a", "b"], chosen="b", reason="r")


def test_errors_carry_typed_context() -> None:
    no_eligible = NoEligibleBackendError(Intent.OCR_TABLES, "gone")
    assert no_eligible.intent is Intent.OCR_TABLES
    assert "ocr-tables" in str(no_eligible)
    bare = NoEligibleBackendError(None, "nothing at all")
    assert "ocr" not in str(bare).split(":")[0]
    violation = JudgeViolationError("ocr-ovis", "not eligible", Intent.NATIVE)
    assert violation.name == "ocr-ovis"
    assert isinstance(violation, RoutingError)
    assert isinstance(no_eligible, RoutingError)


# ── rules: eligibility ─────────────────────────────────────────────────────


def test_is_ocr_detection() -> None:
    assert is_ocr(make_desc("ocr-tele", ALL_FORMATS)) is True
    assert is_ocr(make_desc("other", ALL_FORMATS, group="ocr-special")) is True
    assert is_ocr(make_desc("native-text")) is False


def test_missing_extra_is_ineligible() -> None:
    constraints = full_constraints(installed_extras=set())
    assert is_hard_eligible(make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele"), constraints) is False
    assert is_hard_eligible(make_desc("native-text"), constraints) is True


def test_gpu_vram_budget_rules() -> None:
    within = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis")
    over = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=9.0, group="ocr-ovis")
    unknown = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, group="ocr-ovis")
    budget = full_constraints(vram_budget_gb=8.0)
    assert is_hard_eligible(within, budget) is True
    assert is_hard_eligible(over, budget) is False
    assert is_hard_eligible(unknown, budget) is False
    cpu = make_desc("cpu-only")
    assert is_hard_eligible(cpu, full_constraints(vram_budget_gb=0.0)) is True


def test_hard_gpu_backend_needs_a_usable_runtime() -> None:
    """Hardware present is not runtime usable: a ``+cpu`` torch must drop it.

    A GPU invisible to the planner (``vram_budget_gb=0``) or a GPU the runtime
    cannot use (``gpu_usable=False``) both exclude a ``GPU_REQUIRED`` backend,
    while a merely CPU-capable one stays eligible next to it.
    """
    hard = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis")
    optional = BackendDescriptor(
        name="cpu-or-gpu",
        capabilities=BackendCapabilities(
            supported_formats=list(ALL_FORMATS),
            gpu_requirement=0.5,
            estimated_vram_gb=2.0,
            optional_dependency_group="ocr-ovis",
        ),
    )
    assert is_hard_eligible(hard, full_constraints(gpu_usable=True)) is True
    assert is_hard_eligible(hard, full_constraints(gpu_usable=False)) is False
    assert is_hard_eligible(hard, full_constraints(vram_budget_gb=0.0, gpu_usable=False)) is False
    assert is_hard_eligible(optional, full_constraints(gpu_usable=False)) is True


def test_required_engine_gates_eligibility() -> None:
    """A declared engine is a precondition, exactly like a hard GPU requirement.

    Admitting a backend whose binary is absent would route pages into a pass
    that can only fail, so the host fact drops it up front. The rule compares
    the declared engine against the probed set — never a backend name.
    """
    tesseract = make_desc("ocr-tesseract", required_engine="tesseract")

    assert is_hard_eligible(tesseract, full_constraints(engines=frozenset({"tesseract"}))) is True
    assert is_hard_eligible(tesseract, full_constraints(engines=frozenset())) is False
    # Another engine's presence says nothing about this one's:
    assert is_hard_eligible(tesseract, full_constraints(engines=frozenset({"pandoc"}))) is False


def test_backend_without_a_required_engine_ignores_the_engine_set() -> None:
    """The default is "needs no engine", so the fact cannot exclude anything."""
    native = make_desc("native-text")

    assert is_hard_eligible(native, full_constraints(engines=frozenset())) is True


def test_engine_gate_reads_the_declaration_not_the_backend_name() -> None:
    """Generic by construction: an oddly named backend is treated the same."""
    odd = make_desc("some-future-thing", required_engine="tesseract")

    assert is_hard_eligible(odd, full_constraints(engines=frozenset({"tesseract"}))) is True
    assert is_hard_eligible(odd, full_constraints(engines=frozenset())) is False


def test_format_coverage_rules() -> None:
    pdf_backend = make_desc("pymupdf", ("application/pdf",))
    assert is_hard_eligible(pdf_backend, full_constraints(formats={"application/pdf"})) is True
    assert is_hard_eligible(pdf_backend, full_constraints(formats={"text/plain"})) is False
    assert is_hard_eligible(pdf_backend, full_constraints(formats=set())) is True


def test_allow_ocr_and_offline_rules() -> None:
    tele = make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele")
    assert is_hard_eligible(tele, full_constraints(allow_ocr=False)) is False
    assetful = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis", with_asset=True)
    # Offline excludes DOWNLOADS, not assets (pc-m0k): the asset counts as
    # absent unless its "model_id@revision" is in cached_assets.
    assert is_hard_eligible(assetful, full_constraints(offline=True)) is False
    assert is_hard_eligible(assetful, full_constraints(offline=True, cached_assets={"acme/model@r1"})) is True
    assert is_hard_eligible(assetful, full_constraints(offline=False)) is True


def test_offline_eligibility_tracks_the_asset_identity_and_online_rule() -> None:
    """Warm-verified stays eligible offline; wrong revision and online cases pin the edges."""
    assetful = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis", with_asset=True)
    # A cache entry for another revision does not admit this pin (fail-safe).
    stale = full_constraints(offline=True, cached_assets={"acme/model@r0"})
    assert is_hard_eligible(assetful, stale) is False
    # A different asset's entry neither admits nor blocks anyone else.
    other = full_constraints(offline=True, cached_assets={"acme/other@r1"})
    assert is_hard_eligible(assetful, other) is False
    # Online is unaffected by the fact entirely.
    assert is_hard_eligible(assetful, full_constraints(offline=False, cached_assets=set())) is True


def test_offline_plan_includes_a_cached_model_asset_backend() -> None:
    """Real planning run (pc-m0k): the offline plan now admits the cached backend."""
    cached = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis", with_asset=True)
    fallback = make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele")
    analysis = make_analysis(garbled(2), 2)
    constraints = full_constraints(offline=True, cached_assets={"acme/model@r1"}, formats=ALL_FORMATS, allow_ocr=True)
    plan = plan_route(analysis, [fallback, cached], constraints)
    assert all(page.chosen == "ocr-ovis" for page in plan.pages)  # the cached heavy backend leads
    # Clear the cache (drop the fact) and the same plan excludes it again.
    cleared = full_constraints(offline=True, formats=ALL_FORMATS, allow_ocr=True)
    plan_cleared = plan_route(analysis, [fallback, cached], cleared)
    assert all(page.chosen == "ocr-tele" for page in plan_cleared.pages)


def test_intent_family_rules() -> None:
    native = make_desc("native-text")
    ocr = make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele")
    assert in_intent_family(Intent.NATIVE, native) is True
    assert in_intent_family(Intent.NATIVE, ocr) is True
    assert in_intent_family(Intent.OCR_GENERAL, ocr) is True
    assert in_intent_family(Intent.OCR_GENERAL, native) is False
    assert in_intent_family(Intent.OCR_TABLES, native) is False


# ── rules: signal → intent ─────────────────────────────────────────────────


def test_intent_rule_table_precedence() -> None:
    assert [rule.intent for rule in INTENT_RULES] == [
        Intent.OCR_TABLES,
        Intent.OCR_VISION,
        Intent.OCR_GENERAL,
        Intent.NATIVE,
    ]
    assert all(rule.condition for rule in INTENT_RULES)


@pytest.mark.parametrize(
    ("signal_kwargs", "expected"),
    [
        ({"blank": True}, True),
        ({"native": False}, True),
        ({"text_chars": 10}, True),
        ({"replacement": 0.2}, True),
        ({"replacement": None}, False),
        ({}, False),
    ],
)
def test_page_needs_ocr_conditions(signal_kwargs: dict[str, Any], expected: bool) -> None:
    assert page_needs_ocr(make_signal(**signal_kwargs)) is expected


def test_page_needs_ocr_honours_a_classifier_verdict() -> None:
    """A classifier verdict is augment-only: it can ADD ocr-need, never remove it (ADR-0004 A2)."""
    signal = make_signal().model_copy(update={"classifier_needs_ocr": True})
    assert page_needs_ocr(signal) is True


def test_extract_hints_from_codes_and_image_mass() -> None:
    analysis = make_analysis([make_signal(images=7)], 1, (FEATURE_TABLE_CODE, FEATURE_EQUATIONS_CODE))
    hints = extract_hints(analysis)
    assert hints.tables is True
    assert hints.equations is True
    assert hints.figures is True  # image mass ≥ HEAVY_IMAGE_COUNT
    empty = extract_hints(make_analysis(good_native(), 1))
    assert (empty.tables, empty.equations, empty.figures) == (False, False, False)
    figures_by_code = extract_hints(make_analysis(good_native(), 1, (FEATURE_FIGURES_CODE,)))
    assert figures_by_code.figures is True


def test_image_mass_trigger_flips_exactly_at_heavy_count() -> None:
    """pc-svr: the figures feature is a sum-of-page-images trigger, not a constant.

    Four image XObjects stay below the threshold, five cross it. If
    ``image_count`` were a hardcoded constant 0, this boundary would not exist
    and the image-mass trigger would be dead for the common text-layer PDF path.
    """
    below = extract_hints(make_analysis([make_signal(1, images=4)], 1))
    assert below.figures is False
    at = extract_hints(make_analysis([make_signal(1, images=5)], 1))
    assert at.figures is True


def test_classify_native_and_general_ocr() -> None:
    hints = extract_hints(make_analysis(good_native(), 1))
    assert classify_page(make_signal(), 1, hints) is Intent.NATIVE
    ocr_hints = extract_hints(make_analysis(garbled(), 1))
    assert classify_page(garbled()[0], 1, ocr_hints) is Intent.OCR_GENERAL


def test_classify_tables_requires_multi_page() -> None:
    hints = extract_hints(make_analysis(garbled(), 3, (FEATURE_TABLE_CODE,)))
    assert classify_page(garbled()[0], 3, hints) is Intent.OCR_TABLES
    assert classify_page(garbled()[0], 1, hints) is Intent.OCR_GENERAL


def test_classify_vision_for_equations_and_figures() -> None:
    equation_hints = extract_hints(make_analysis(garbled(), 2, (FEATURE_EQUATIONS_CODE,)))
    assert classify_page(garbled()[0], 2, equation_hints) is Intent.OCR_VISION
    image_hints = extract_hints(make_analysis([make_signal(1, images=5)], 1))
    assert classify_page(garbled()[0], 1, image_hints) is Intent.OCR_VISION


# ── judge ──────────────────────────────────────────────────────────────────


def test_preferred_backends_cover_every_intent() -> None:
    assert set(PREFERRED_BACKENDS) == set(Intent)


def test_deterministic_judge_native_family_ordering() -> None:
    judge = DeterministicJudge()
    heavy = make_desc("native-heavy", ("text/plain",), gpu=True, vram=7.5)
    light = make_desc("native-light", ("text/plain",), gpu=True, vram=1.0)
    plain = make_desc("native-aaa", ("text/plain",))
    ocr = make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele")
    order = judge.rank(Intent.NATIVE, [ocr, heavy, plain, light])
    assert order == ["native-light", "native-heavy", "native-aaa", "ocr-tele"]  # vram, then name, OCR last


def test_deterministic_judge_prefers_model_per_intent() -> None:
    judge = DeterministicJudge()
    tables = judge.rank(Intent.OCR_TABLES, ocr_backends())
    assert tables[0] == "ocr-unlimited"
    vision = judge.rank(Intent.OCR_VISION, ocr_backends())
    assert vision[0] == "ocr-ovis"
    general = judge.rank(Intent.OCR_GENERAL, ocr_backends())
    assert general == ["ocr-unlimited", "ocr-ovis", "ocr-qianfan", "ocr-tele"]  # vram, then name


def test_judge_protocol_runtime_conformance() -> None:
    assert isinstance(DeterministicJudge(), RoutingJudge)


# ── preference: a ranking axis, never a permission ─────────────────────────


def test_routing_preference_is_a_lowercase_str_enum() -> None:
    """A StrEnum, so the values are the wire/CLI spelling and no Literal is needed."""
    assert [member.value for member in RoutingPreference] == ["speed", "balanced", "quality"]
    assert all(isinstance(member, str) for member in RoutingPreference)
    assert RoutingPreference("quality") is RoutingPreference.QUALITY  # typer/pydantic parse free
    assert f"{RoutingPreference.SPEED}" == "speed"  # str identity, not "RoutingPreference.SPEED"
    assert RoutingConstraints().preference is RoutingPreference.BALANCED


def test_preference_quality_reverses_the_vram_tiebreak_within_a_family() -> None:
    """QUALITY takes the largest declared model first; unsized candidates stay last."""
    balanced = list(DeterministicJudge().rank(Intent.OCR_GENERAL, ocr_backends()))
    quality = list(DeterministicJudge(RoutingPreference.QUALITY).rank(Intent.OCR_GENERAL, ocr_backends()))
    assert balanced == ["ocr-unlimited", "ocr-ovis", "ocr-qianfan", "ocr-tele"]  # 4.0 GB, then 6.0 GB
    assert quality == ["ocr-ovis", "ocr-unlimited", "ocr-qianfan", "ocr-tele"]  # 6.0 GB first
    # No declared size is unknown strength, not zero: those two stay last either way.
    assert balanced[2:] == quality[2:] == ["ocr-qianfan", "ocr-tele"]


def test_speed_matches_balanced_and_is_the_explicit_cheapest() -> None:
    heavy = make_desc("native-heavy", ("text/plain",), gpu=True, vram=7.5)
    light = make_desc("native-light", ("text/plain",), gpu=True, vram=1.0)
    candidates = [heavy, light]
    assert list(DeterministicJudge(RoutingPreference.SPEED).rank(Intent.NATIVE, candidates)) == ["native-light", "native-heavy"]
    assert list(DeterministicJudge(RoutingPreference.BALANCED).rank(Intent.NATIVE, candidates)) == ["native-light", "native-heavy"]


def test_preference_never_crosses_the_family_precedence() -> None:
    """A big OCR model never overtakes the native lead for a NATIVE-intent page."""
    heavy = make_desc("native-heavy", ("text/plain",), gpu=True, vram=7.5)
    plain = make_desc("native-aaa", ("text/plain",))
    ocr = make_desc("ocr-tele", ALL_FORMATS, gpu=True, vram=200.0)
    for preference in RoutingPreference:
        assert list(DeterministicJudge(preference).rank(Intent.NATIVE, [ocr, heavy, plain]))[-1] == "ocr-tele"


def test_preference_never_overrides_the_preferred_model() -> None:
    """The code-owned preferred backend for a flavor stays first under any preference."""
    for preference in RoutingPreference:
        order = DeterministicJudge(preference).rank(Intent.OCR_TABLES, ocr_backends())
        assert order[0] == "ocr-unlimited"


def test_plan_uses_the_constraint_preference_for_its_own_judge() -> None:
    """With no injected judge, the planner's default judge reads the constraint."""
    heavy = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis")
    light = make_desc("ocr-unlimited", ALL_FORMATS, gpu=True, vram=4.0, group="ocr-unlimited")
    analysis = make_analysis(garbled(), 1)
    balanced = plan_route(analysis, [heavy, light], full_constraints(formats=ALL_FORMATS, allow_ocr=True))
    quality = plan_route(
        analysis,
        [heavy, light],
        full_constraints(formats=ALL_FORMATS, allow_ocr=True, preference=RoutingPreference.QUALITY),
    )
    assert balanced.pages[0].chosen == "ocr-unlimited"
    assert quality.pages[0].chosen == "ocr-ovis"


# ── planner: happy paths ───────────────────────────────────────────────────


def test_plan_native_route_with_reason() -> None:
    plan = plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(formats={"text/plain"}))
    assert plan.pages[0].intent is Intent.NATIVE
    assert plan.pages[0].chosen == "native-text"
    assert plan.pages[0].candidates[0] == "native-text"
    assert "native text sufficient" in plan.pages[0].reason
    assert "native-text" in plan.pages[0].reason
    assert plan.primary == "native-text"


def test_plan_ocr_routes_prefer_matching_model() -> None:
    tables = plan_route(make_analysis(garbled(3), 3, (FEATURE_TABLE_CODE,)), all_backends(), full_constraints(formats={"application/pdf"}))
    assert all(page.intent is Intent.OCR_TABLES for page in tables.pages)
    assert all(page.chosen == "ocr-unlimited" for page in tables.pages)
    assert all("multi-page dense tables" in page.reason for page in tables.pages)
    vision = plan_route(make_analysis(garbled(1), 1, (FEATURE_EQUATIONS_CODE,)), all_backends(), full_constraints(formats={"text/html"}))
    assert vision.pages[0].intent is Intent.OCR_VISION
    assert vision.pages[0].chosen == "ocr-ovis"
    assert "equations/figures-heavy" in vision.pages[0].reason


def test_plan_deterministic_across_runs_and_input_order() -> None:
    analysis = make_analysis(garbled(2) + good_native(1), 3, (FEATURE_TABLE_CODE,))
    constraints = full_constraints(formats=set())
    first = plan_route(analysis, all_backends(), constraints)
    second = plan_route(analysis, all_backends(), constraints)
    assert first.model_dump() == second.model_dump()
    shuffled = list(reversed(all_backends()))
    third = plan_route(analysis, shuffled, constraints)
    assert first.model_dump() == third.model_dump()


def test_plan_sorts_pages_by_page_number() -> None:
    signals = [make_signal(3, text_chars=10), make_signal(1), make_signal(2)]
    plan = plan_route(make_analysis(signals, 3), all_backends(), full_constraints(formats={"text/plain"}))
    assert [page.page_number for page in plan.pages] == [1, 2, 3]
    assert [page.intent for page in plan.pages] == [Intent.NATIVE, Intent.NATIVE, Intent.OCR_GENERAL]


def test_plan_primary_is_mode_with_name_tiebreak() -> None:
    mixed = [make_signal(1), make_signal(2), make_signal(3, text_chars=5, blank=True)]
    plan = plan_route(make_analysis(mixed, 3), all_backends(), full_constraints(formats=set()))
    assert plan.primary == "native-html"  # 2 native wins beat 1 OCR; name tiebreak not needed


def test_plan_primary_tiebreak_by_name() -> None:
    # Two pages: one routed native, one OCR (a figure page — a blank page would
    # degrade now) — no majority, so name order wins.
    signals = [make_signal(1), make_signal(2, text_chars=0, native=False, blank=True, images=2)]
    analysis = make_analysis(signals, 2, (FEATURE_EQUATIONS_CODE,))
    plan = plan_route(analysis, all_backends(), full_constraints(formats=set()))
    chosen = {page.chosen for page in plan.pages}
    assert chosen == {"native-html", "ocr-ovis"}
    assert plan.primary == "native-html"  # lexicographic tiebreak


def test_deterministic_judge_ignores_the_page_context() -> None:
    """The fallback judge stays a pure function of family + preference (A8)."""
    context = PageContext(needs_ocr=True, blank=True, hints=FeatureHints(tables=True, figures=True))
    plain = list(DeterministicJudge().rank(Intent.OCR_GENERAL, ocr_backends()))
    with_context = list(DeterministicJudge().rank(Intent.OCR_GENERAL, ocr_backends(), context))
    assert plain == with_context
    assert list(DeterministicJudge(RoutingPreference.QUALITY).rank(Intent.OCR_TABLES, ocr_backends(), context)) == list(
        DeterministicJudge(RoutingPreference.QUALITY).rank(Intent.OCR_TABLES, ocr_backends())
    )


def test_plan_hands_a_context_to_every_page() -> None:
    """Every page carries bounded facts — native ones too (A8 widened by A9/pc-86l)."""
    seen: list[tuple[str, PageContext | None]] = []

    class RecordingJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            seen.append((intent.value, context))
            # The NATIVE-lead guard requires native-capable candidates before OCR
            # fallbacks, so an identity order is not a legal answer.
            native = [d.name for d in candidates if not d.name.startswith("ocr-")]
            ocr = [d.name for d in candidates if d.name.startswith("ocr-")]
            return [*native, *ocr]

    analysis = make_analysis([*good_native(), *garbled(1)], 2)
    plan_route(analysis, all_backends(), full_constraints(formats=set(), max_passes=9), RecordingJudge())

    assert [intent for intent, _ in seen] == ["native", "ocr"]
    native_context = seen[0][1]
    ocr_context = seen[1][1]
    assert native_context is not None  # a native page carries its class too (pc-86l)
    assert native_context.needs_ocr is False
    assert native_context.density == pytest.approx(0.5)  # good_native carries 500 chars
    assert ocr_context is not None
    assert ocr_context.needs_ocr is True
    assert ocr_context.blank is False
    assert ocr_context.hints == extract_hints(analysis)
    assert native_context.memo_key() != ocr_context.memo_key()  # needs_ocr differs
    # hints are document-level hence plan-constant, so they stay out of the memo key;
    # density is per-page-variable and joined it (A9) — garbled() carries 90 chars → 0.09
    assert ocr_context.memo_key() == (True, False, 0.09)


def test_degraded_pages_carry_the_native_page_context() -> None:
    """A page degraded to native is ranked as a native page — needs_ocr false, density present."""
    seen: list[Any] = []

    class RecordingJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            seen.append(context)
            return [descriptor.name for descriptor in candidates]

    native_only = [make_desc("native-text", ("text/plain",)), make_desc("native-markdown", ("text/markdown",))]
    plan_route(
        make_analysis(garbled(1), 1),
        native_only,
        full_constraints(formats=set(), allow_ocr=False, max_passes=1),
        RecordingJudge(),
    )
    assert len(seen) == 1  # one ranking call for the one page
    assert seen[0] is not None  # pc-86l: a degraded page carries its class too
    assert seen[0].needs_ocr is False  # ranked as native
    assert seen[0].density == pytest.approx(0.09)  # garbled() carries 90 chars


def test_plan_respects_injected_judge() -> None:
    class ReverseJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return [descriptor.name for descriptor in sorted(candidates, key=lambda d: d.name, reverse=True)]

    plan = plan_route(
        make_analysis(garbled(1), 1),
        all_backends(),
        full_constraints(formats=set(), max_passes=9),
        ReverseJudge(),
    )
    assert plan.pages[0].intent is Intent.OCR_GENERAL
    assert plan.pages[0].chosen == "ocr-unlimited"  # injected judge's first pick wins
    assert plan.pages[0].candidates[-1] == "ocr-ovis"  # ...and its order holds throughout


def test_plan_rejects_a_judge_that_leads_a_native_page_with_ocr() -> None:
    """A NATIVE page's order may reorder, but never promote an OCR fallback.

    The rules admit OCR backends into a NATIVE family as fallbacks; leading with
    one spends minutes on text a native backend reads in milliseconds.
    """

    class OcrFirstJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            others = [descriptor.name for descriptor in candidates if descriptor.name != "ocr-unlimited"]
            return ["ocr-unlimited", *others]

    with pytest.raises(JudgeViolationError, match="leads above the caller"):
        plan_route(
            make_analysis(good_native(), 1),
            all_backends(),
            full_constraints(formats=set(), max_passes=9),
            OcrFirstJudge(),
        )


def test_plan_accepts_a_judge_that_keeps_native_candidates_first() -> None:
    """The contract constrains the family order, not the judge's taste within it."""

    class NativeFirstJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            native = [descriptor.name for descriptor in candidates if not descriptor.name.startswith("ocr-")]
            ocr = [descriptor.name for descriptor in candidates if descriptor.name.startswith("ocr-")]
            return [*reversed(native), *reversed(ocr)]

    plan = plan_route(
        make_analysis(good_native(), 1),
        all_backends(),
        full_constraints(formats=set(), max_passes=9),
        NativeFirstJudge(),
    )
    # The contract constrains the FAMILY order (native-capable before OCR), not
    # the judge's taste WITHIN the native group — so the winner is the first of
    # the judge's reversed native group, and it is a native-capable backend.
    # Asserted name-independently: backend names sort, and pymupdf sorts after
    # the native-* names, so a hardcoded name would pin the accident of sorting.
    native_group = [d.name for d in all_backends() if not d.name.startswith("ocr-")]
    expected = sorted(native_group, reverse=True)[0]
    assert plan.pages[0].chosen == expected
    assert not plan.pages[0].chosen.startswith("ocr-")


def test_plan_max_passes_truncates_candidates() -> None:
    plan = plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(formats=set(), max_passes=2))
    assert len(plan.pages[0].candidates) == 2
    assert plan.pages[0].chosen == plan.pages[0].candidates[0]


def test_plan_judge_may_return_subset() -> None:
    class OneShotJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return [candidates[0].name]

    plan = plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(), OneShotJudge())
    assert plan.pages[0].candidates == [sorted((d.name for d in all_backends()), key=str)[0]]


# ── planner: typed failures ────────────────────────────────────────────────


def test_plan_empty_signals_raises() -> None:
    with pytest.raises(RoutingError, match="no page signals"):
        plan_route(make_analysis([], 0), all_backends(), full_constraints())


def test_plan_no_eligible_backend_raises() -> None:
    with pytest.raises(NoEligibleBackendError) as exc_info:
        plan_route(make_analysis(good_native(), 1), native_backends(), full_constraints(formats={"text/csv"}))
    assert exc_info.value.intent is None


def test_plan_intent_family_empty_raises_with_intent() -> None:
    # A page that visibly holds content but has no text layer still cannot
    # degrade (native would emit an empty page for a scan) — with the OCR family
    # forbidden, routing still fails loudly and names the intent.
    scanned = make_analysis([make_signal(1, text_chars=0, native=False, blank=False, images=1)], 1)
    with pytest.raises(NoEligibleBackendError) as exc_info:
        plan_route(scanned, all_backends(), full_constraints(allow_ocr=False, formats=set()))
    assert exc_info.value.intent is Intent.OCR_GENERAL


def test_plan_judge_returning_ineligible_raises() -> None:
    class BadJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return ["not-a-real-backend"]

    with pytest.raises(JudgeViolationError, match="not an eligible candidate"):
        plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(), BadJudge())


def test_plan_judge_returning_duplicate_raises() -> None:
    class DupJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return [candidates[0].name, candidates[0].name]

    with pytest.raises(JudgeViolationError, match="duplicate"):
        plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(), DupJudge())


def test_plan_judge_returning_empty_raises() -> None:
    class EmptyJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            return []

    with pytest.raises(JudgeViolationError, match="empty order"):
        plan_route(make_analysis(good_native(), 1), all_backends(), full_constraints(), EmptyJudge())


def test_manifest_ids_match_expectations_map() -> None:
    ids = {source["id"] for source in _MANIFEST_SOURCES}
    assert ids == set(_EXPECTED_INTENT_BY_ID) | {"nasa-exoplanet-catalog"}


@pytest.mark.parametrize("source", _MANIFEST_SOURCES, ids=lambda s: s["id"])
def test_manifest_expectations_route_deterministically(source: dict[str, Any]) -> None:
    features = set(source.get("features", ()))
    page_count = int(source.get("pages", 1))
    codes = []
    if "tables" in features:
        codes.append(FEATURE_TABLE_CODE)
    if "equations" in features:
        codes.append(FEATURE_EQUATIONS_CODE)
    if features & {"images", "figures"}:
        codes.append(FEATURE_FIGURES_CODE)
    signals = garbled(page_count) if source["difficulty"] == "complex" else good_native(page_count)
    analysis = make_analysis(signals, page_count, tuple(codes))
    constraints = full_constraints(formats={source["media_type"]})

    if source["format"] in _UNCOVERED_FORMATS:
        with pytest.raises(NoEligibleBackendError) as exc_info:
            plan_route(analysis, all_backends(), constraints)
        assert exc_info.value.intent is Intent.NATIVE  # good text, but no native backend for the format
        return

    plan = plan_route(analysis, all_backends(), constraints)
    assert plan == plan_route(analysis, all_backends(), constraints)  # deterministic
    expected_intent = _EXPECTED_INTENT_BY_ID[source["id"]]
    for page in plan.pages:
        assert page.intent is expected_intent
        assert page.chosen == page.candidates[0]
        assert page.reason
        assert set(page.candidates) <= {descriptor.name for descriptor in all_backends()}
    if expected_intent is Intent.NATIVE:
        assert plan.primary.startswith("native-")
    elif expected_intent is Intent.OCR_TABLES:
        assert plan.primary == "ocr-unlimited"
    else:
        assert plan.primary == "ocr-ovis"


def all_backends() -> list[BackendDescriptor]:
    return [*native_backends(), *ocr_backends()]


def native_backends() -> list[BackendDescriptor]:
    return [
        make_desc("native-text", ("text/plain",)),
        make_desc("native-markdown", ("text/markdown",)),
        make_desc("native-html", ("text/html",)),
        make_desc("pymupdf", ("application/pdf",)),
    ]


def ocr_backends() -> list[BackendDescriptor]:
    return [
        make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis"),
        make_desc("ocr-qianfan", ALL_FORMATS, group="ocr-qianfan"),
        make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele"),
        make_desc("ocr-unlimited", ALL_FORMATS, gpu=True, vram=4.0, group="ocr-unlimited"),
    ]


def garbled(pages: int = 1) -> list[PageSignal]:
    return [make_signal(index, text_chars=90, replacement=0.25) for index in range(1, pages + 1)]


# ── manifest expectations (the corpus the harness routes against) ──────────


def _manifest_sources() -> list[dict[str, Any]]:
    return list(tomllib.loads(_SOURCES_PATH.read_text(encoding="utf-8"))["sources"])


# ── degradation: OCR family empty but the page can emit native text ────────


def test_short_native_text_page_degrades_instead_of_erroring() -> None:
    # 34 chars < NATIVE_MIN_TEXT_CHARS(40) → classification says OCR…
    signal = make_signal(1, text_chars=34)
    analysis = make_analysis([signal], 1)
    hints = extract_hints(analysis)
    assert classify_page(signal, 1, hints) is Intent.OCR_GENERAL
    # …but with no OCR family installed, a native backend must serve it.
    plan = plan_route(
        analysis,
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    page = plan.pages[0]
    assert page.intent is Intent.NATIVE
    assert page.chosen == "native-text"
    assert "OCR unavailable (no OCR backend is installed)" in page.reason
    assert "degraded to native" in page.reason
    assert "text_chars=34" in page.reason


def test_a_blank_page_does_not_claim_native_text_was_present() -> None:
    """A blank page has no native text, so the reason must not assert any.

    Observed on a real textless PDF before this was fixed: the message read
    "degraded to native (native text present, text_chars=0)" — two claims, both
    false, in the one message whose purpose is to say honestly why a page was not
    OCR'd.
    """
    analysis = make_analysis([make_signal(1, text_chars=0, native=False, blank=True)], 1)

    plan = plan_route(analysis, [make_desc("native-text")], full_constraints(formats={"text/plain"}, installed_extras=set()))
    reason = plan.pages[0].reason

    assert "blank page: no text or images to lose" in reason
    assert "native text present" not in reason
    assert "text_chars=0" not in reason


def test_a_scanned_page_reason_names_the_images_not_blank() -> None:
    """pc-svr: a textless page with image XObjects is a scan, not a blank page.

    Saying there is "nothing to lose" about a page full of pixels is exactly the
    false claim pc-svr fixes. A scanned page's honest reason names the images
    it cannot extract; a truly blank page's does not.
    """
    analysis = make_analysis([make_signal(1, text_chars=0, native=False, blank=True, images=1)], 1)
    plan = plan_route(analysis, [make_desc("native-text")], full_constraints(formats={"text/plain"}, installed_extras=set()))
    reason = plan.pages[0].reason

    assert "scanned page: 1 image, no text layer" in reason
    assert "blank" not in reason


def test_a_scanned_page_reason_pluralises_images() -> None:
    analysis = make_analysis([make_signal(1, text_chars=0, native=False, blank=True, images=3)], 1)
    plan = plan_route(analysis, [make_desc("native-text")], full_constraints(formats={"text/plain"}, installed_extras=set()))
    assert "scanned page: 3 images, no text layer" in plan.pages[0].reason


def test_a_missing_engine_is_named_in_the_degradation_reason() -> None:
    """Decision 5: a host-configuration problem must not read like a routing failure.

    The engine case names the engine, because the remedy is one OS package. A
    generic "no eligible OCR backend" here would send the operator looking at
    routing rules instead of at their host.
    """
    signal = make_signal(1, text_chars=34)
    analysis = make_analysis([signal], 1)
    backends = [make_desc("native-text"), make_desc("ocr-tesseract", required_engine="tesseract")]

    plan = plan_route(analysis, backends, full_constraints(formats={"text/plain"}, installed_extras=set(), engines=frozenset()))
    reason = plan.pages[0].reason

    assert "the tesseract engine is not installed on this host" in reason
    assert "no eligible OCR backend" not in reason


def test_an_eligible_engine_leaves_the_degradation_reading_as_a_routing_fact() -> None:
    """With the engine present, whatever still blocks OCR is a routing-level cause."""
    signal = make_signal(1, text_chars=34)
    analysis = make_analysis([signal], 1)
    backends = [make_desc("native-text"), make_desc("ocr-tesseract", required_engine="tesseract")]

    plan = plan_route(
        analysis,
        backends,
        full_constraints(formats={"text/plain"}, installed_extras=set(), engines=frozenset({"tesseract"}), allow_ocr=False),
    )

    assert "no eligible OCR backend (missing extras, OCR disabled, or no usable GPU)" in plan.pages[0].reason


def test_blank_page_degrades_when_ocr_family_is_empty() -> None:
    """pc-ztq flipped this: a blank page used to fail the whole document.

    Nothing to read means nothing to lose, so the empty native page is the honest
    result; the OCR intent is still what the rules decided, and the degradation is
    recorded rather than silent.
    """
    signal = make_signal(1, text_chars=0, native=False, blank=True)
    analysis = make_analysis([signal], 1)
    assert classify_page(signal, 1, extract_hints(analysis)) is Intent.OCR_GENERAL
    plan = plan_route(
        analysis,
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    page = plan.pages[0]
    assert page.intent is Intent.NATIVE
    assert page.candidates == ["native-text"]
    assert page.degradation_code == "degraded-blank-page"
    assert page.degradation_score == 0.0


def test_textless_nonblank_page_still_errors_when_ocr_family_empty() -> None:
    signal = make_signal(1, text_chars=0, native=False, blank=False)
    analysis = make_analysis([signal], 1)
    with pytest.raises(NoEligibleBackendError) as exc_info:
        plan_route(
            analysis,
            [make_desc("native-text")],
            full_constraints(formats={"text/plain"}, installed_extras=set()),
        )
    assert exc_info.value.intent is Intent.OCR_GENERAL


def test_no_degradation_when_ocr_family_is_available() -> None:
    signal = make_signal(1, text_chars=34)
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-text"), make_desc("ocr-stub", ALL_FORMATS, group="ocr-stub")],
        full_constraints(formats={"text/plain"}, installed_extras={"ocr-stub"}),  # its extra IS installed
    )
    page = plan.pages[0]
    assert page.intent is Intent.OCR_GENERAL  # OCR exists → no fallback
    assert page.chosen == "ocr-stub"
    assert "degraded to native" not in page.reason


def test_docling_extra_makes_the_backend_routable() -> None:
    # ff741ad regression pin: the docling extra was declared in pyproject but
    # absent from EXTRA_IMPORTS, so the probe could never report it installed
    # and the planner silently dropped the backend even when the extra was
    # present. Eligibility must follow the installed extra, not the map.
    signal = make_signal(1, text_chars=120)
    descriptors = [make_desc("native-text"), make_desc("docling", ALL_FORMATS, group="docling")]

    routed = plan_route(
        make_analysis([signal], 1),
        descriptors,
        full_constraints(formats={"text/plain"}, installed_extras={"docling"}),
    )
    assert "docling" in routed.pages[0].candidates  # eligible: the extra is installed

    unrouted = plan_route(
        make_analysis([signal], 1),
        descriptors,
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    assert "docling" not in unrouted.pages[0].candidates


def test_can_degrade_requires_a_non_ocr_backend() -> None:
    signal = make_signal(1, text_chars=34)
    assert can_degrade_to_native(signal, [make_desc("native-text")]) is True
    assert can_degrade_to_native(signal, [make_desc("ocr-only", ALL_FORMATS, group="ocr-x")]) is False
    blank = make_signal(1, text_chars=0, native=False, blank=True)
    # pc-ztq: a blank page has no content to lose, so an empty native page is the
    # honest result rather than a reason to fail the whole document.
    assert can_degrade_to_native(blank, [make_desc("native-text")]) is True
    # A page that visibly holds content but has no text layer must still refuse:
    # native would emit an empty page for a scan.
    scanned = make_signal(1, text_chars=0, native=False, blank=False, images=1)
    assert can_degrade_to_native(scanned, [make_desc("native-text")]) is False


# ── degradation records: codes, scores, consistency ────────────────────────


def test_short_text_degradation_records_code_and_score() -> None:
    plan = plan_route(
        make_analysis([make_signal(1, text_chars=34)], 1),
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    page = plan.pages[0]
    assert page.degradation_code == "degraded-short-text"
    assert page.degradation_score == round(34 / 40, 6)
    assert page.degradation_score is not None
    assert 0 <= page.degradation_score < 1


def test_garbled_text_degradation_is_flagged_distinctly() -> None:
    # 90 chars ≥ 40 but replacement 0.25 → OCR intent via mojibake, degrades.
    signal = make_signal(1, text_chars=90, replacement=0.25)
    assert classify_page(signal, 1, extract_hints(make_analysis([signal], 1))) is Intent.OCR_GENERAL
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    page = plan.pages[0]
    assert page.intent is Intent.NATIVE
    assert page.degradation_code == "degraded-garbled-text"  # mojibake ≠ thin content
    assert page.degradation_score == 0.75  # clean-text share
    assert page.degradation_code != "degraded-short-text"


def test_blank_page_without_images_degrades_even_when_ocr_is_eligible() -> None:
    """A blank page with no images has nothing an OCR pass could return.

    Regression (polqa-re): with a usable GPU the OCR family is eligible, so the
    old rule sent the page to a VLM — measured at 527 s of ocr-ovis for an empty
    page. Content is not coverage: the page is fully covered and still has
    nothing to lose, so it degrades with the same recorded reason it gets on a
    host without OCR.
    """
    signal = make_signal(1, text_chars=0, native=False, blank=True, images=0)
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-text"), make_desc("ocr-ovis", ALL_FORMATS, group="ocr-ovis", vram=2.0)],
        full_constraints(formats={"text/plain"}),
    )
    page = plan.pages[0]
    assert page.intent is Intent.NATIVE
    assert page.chosen == "native-text"
    assert page.degradation_code == DEGRADED_BLANK_PAGE_CODE
    assert page.degradation_score == 0.0


def test_blank_page_with_images_keeps_its_ocr_intent() -> None:
    """A blank page holding a figure stays in the OCR family: a vision pass can read it."""
    signal = make_signal(1, text_chars=0, native=False, blank=True, images=2)
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-text"), make_desc("ocr-ovis", ALL_FORMATS, group="ocr-ovis", vram=2.0)],
        full_constraints(formats={"text/plain"}),
    )
    page = plan.pages[0]
    assert page.intent is Intent.OCR_GENERAL
    assert page.candidates == ["ocr-ovis"]
    assert page.degradation_code is None


def test_an_all_blank_document_plans_instead_of_failing() -> None:
    """An all-blank source is a valid, empty-ish document — not an error."""
    signals = [make_signal(page, text_chars=0, native=False, blank=True, images=0) for page in (1, 2, 3)]
    plan = plan_route(
        make_analysis(signals, 3),
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}, installed_extras=set()),
    )
    assert [page.intent for page in plan.pages] == [Intent.NATIVE] * 3
    assert {page.degradation_code for page in plan.pages} == {"degraded-blank-page"}


def test_non_degraded_route_has_no_degradation_fields() -> None:
    plan = plan_route(
        make_analysis(good_native(), 1),
        [make_desc("native-text")],
        full_constraints(formats={"text/plain"}),
    )
    page = plan.pages[0]
    assert page.degradation_code is None
    assert page.degradation_score is None


def good_native(signals_count: int = 1) -> list[PageSignal]:
    return [make_signal(index) for index in range(1, signals_count + 1)]


# ── A10: the NATIVE tier is host-fact aware (pc-9tr) ──────────────────────


def test_native_pdf_is_demoted_when_pdf_extra_absent() -> None:
    """A host without ``parsecraft[pymupdf]`` must not be led by ``pymupdf``.

    knox-1 measured the cost this removes: on the 2026.10.3 tier model,
    ``pymupdf`` led every ``application/pdf`` NATIVE page because its
    ``analyze()`` runs on core pypdf, while its ``convert()`` needs PyMuPDF —
    so each document paid one failed lead attempt before falling through.
    A10 demotes it into the extra-backed tier for exactly that host.
    """
    candidates = [
        make_desc("pymupdf", ("application/pdf",), convert_extra="pymupdf"),
        make_desc("docling", ("application/pdf",), group="docling"),
        make_desc("liteparse", ("application/pdf",), group="liteparse"),
        make_desc("mineru", ("application/pdf",), group="mineru"),
        make_desc("ocr-ovis", ("application/pdf",), gpu=True, vram=6.0, group="ocr"),
    ]
    judge = DeterministicJudge(installed_extras=frozenset({"docling", "liteparse", "mineru", "ocr"}))

    order = list(judge.rank(Intent.NATIVE, candidates))

    assert order[0] == "docling"  # the first installed extra-backed parser, alphabetical
    assert order.index("pymupdf") > order.index("mineru")  # demoted into tier 1, behind the installed parsers
    assert order[-1] == "ocr-ovis"  # OCR stays the last resort


def test_native_pdf_leads_when_pdf_extra_installed() -> None:
    """Regression (2026.10.3): with ``[pdf]`` installed pymupdf is dependency-free and leads."""
    candidates = [
        make_desc("pymupdf", ("application/pdf",), convert_extra="pymupdf"),
        make_desc("docling", ("application/pdf",), group="docling"),
        make_desc("ocr-ovis", ("application/pdf",), gpu=True, vram=6.0, group="ocr"),
    ]
    judge = DeterministicJudge(installed_extras=frozenset({"pymupdf", "docling", "ocr"}))

    assert list(judge.rank(Intent.NATIVE, candidates))[0] == "pymupdf"


def test_every_base_install_native_backend_is_always_eligible() -> None:
    """The native text family never depends on an extra, whatever the host has.

    ``native-text``/``-markdown``/``-html`` declare no ``convert_extra`` and no
    group, so a base install (empty extras) still ranks them tier 0 — and
    ``pymupdf`` joins them the moment its extra is present.
    """
    candidates = [
        make_desc("native-markdown"),
        make_desc("native-text"),
        make_desc("pymupdf", ("application/pdf",), convert_extra="pymupdf"),
        make_desc("docling", ("text/plain", "application/pdf"), group="docling"),
    ]
    base = DeterministicJudge(installed_extras=frozenset())
    with_pdf = DeterministicJudge(installed_extras=frozenset({"pymupdf", "docling"}))

    assert list(base.rank(Intent.NATIVE, candidates))[:2] == ["native-markdown", "native-text"]
    assert list(with_pdf.rank(Intent.NATIVE, candidates))[0] == "native-markdown"  # tier 0 alphabetical; pymupdf is tier 0 too


def test_empty_candidate_set_yields_typed_planner_error() -> None:
    """A host with no PDF-capable backend at all fails typed, not silently."""
    analysis = make_analysis([make_signal(1, native=True)], 1)
    backends = [make_desc("native-text"), make_desc("native-markdown")]

    with pytest.raises(NoEligibleBackendError):
        plan_route(analysis, backends, full_constraints(formats={"application/pdf"}))


def test_existing_tier_ordering_preserved_when_all_convert_extras_satisfied() -> None:
    """Regression (2026.10.3): a fully-satisfied host keeps the tier model."""
    candidates = [
        make_desc("pymupdf", ("application/pdf",), convert_extra="pymupdf"),
        make_desc("docling", ("application/pdf",), group="docling"),
        make_desc("liteparse", ("application/pdf",), group="liteparse"),
        make_desc("ocr-ovis", ("application/pdf",), gpu=True, vram=6.0, group="ocr"),
    ]
    judge = DeterministicJudge(installed_extras=frozenset({"pymupdf", "docling", "liteparse", "ocr"}))

    assert list(judge.rank(Intent.NATIVE, candidates)) == ["pymupdf", "docling", "liteparse", "ocr-ovis"]


def test_max_passes_1_does_not_waste_a_native_pdf_lead_on_knox_config() -> None:
    """The knox-1 shape, offline: one pass, no ``[pdf]`` — the lead is deliverable.

    knox-1 ran ``[auto,docling,liteparse,mineru,ocr,pandoc,web]`` (licence-
    consciously omitting ``[pdf]``) and every PDF paid the failed ``pymupdf``
    lead before ``mineru`` converted it. With A10 the single pass goes straight
    to the first installed extra-backed parser.
    """
    analysis = make_analysis([make_signal(1, native=True)], 1)
    backends = [
        make_desc("pymupdf", ("application/pdf",), convert_extra="pymupdf"),
        make_desc("docling", ("application/pdf",), group="docling"),
        make_desc("liteparse", ("application/pdf",), group="liteparse"),
        make_desc("mineru", ("application/pdf",), group="mineru"),
    ]

    plan = plan_route(
        analysis,
        backends,
        full_constraints(formats={"application/pdf"}, installed_extras={"docling", "liteparse", "mineru"}, max_passes=1),
    )

    assert plan.pages[0].candidates == ["docling"]  # exactly one pass, no pymupdf waste
    assert plan.pages[0].chosen == "docling"


# ── A9: the judge context carries a normalized density fact (pc-bzz) ───────


def test_density_field_in_page_context() -> None:
    """A8's memo key grows the density: pages differing only by density never share a verdict."""
    hints = FeatureHints()
    sparse = PageContext(needs_ocr=True, blank=False, hints=hints, density=0.1)
    dense = PageContext(needs_ocr=True, blank=False, hints=hints, density=0.9)
    same = PageContext(needs_ocr=True, blank=False, hints=hints, density=0.1)

    assert sparse.memo_key() != dense.memo_key()
    assert sparse.memo_key() == same.memo_key()

    signal = make_signal(1, text_chars=500, replacement=0.25)
    context = page_context(Intent.OCR_GENERAL, signal, hints)
    assert context is not None
    assert context.density == pytest.approx(0.5)
    native = page_context(Intent.NATIVE, make_signal(1), hints)  # pc-86l: no None arm anymore
    assert native.needs_ocr is False
    assert native.density == pytest.approx(0.5)


def test_native_pages_get_distinct_memo_keys_by_density() -> None:
    """gh#6's NATIVE discrimination: sparse and dense native pages never share a verdict."""
    hints = FeatureHints()
    sparse = page_context(Intent.NATIVE, make_signal(1, text_chars=100), hints)
    dense = page_context(Intent.NATIVE, make_signal(1, text_chars=6000), hints)
    assert sparse.memo_key() != dense.memo_key()  # differ only by density

    # A native page and an OCR page with the same text stay distinct: needs_ocr differs
    ocr = page_context(Intent.OCR_GENERAL, make_signal(1, text_chars=6000), hints)
    assert ocr.memo_key() != dense.memo_key()


def test_density_normalized_to_reference() -> None:
    """text_chars / DENSITY_REFERENCE_CHARS, saturated to [0.0, 1.0] (A9).

    The anchors are the knox-1 corpus range (gh#6): 121 chars/page sparse
    median, 6066 dense max — the 17× resolution the ≥40 binary used to throw
    away. Calibration may move when the corpus rerun lands.
    """
    hints = FeatureHints()

    def _density(chars: int) -> float:
        context = page_context(Intent.OCR_GENERAL, make_signal(1, text_chars=chars, replacement=0.25), hints)
        assert context is not None
        return context.density

    assert _density(0) == 0.0
    assert _density(121) == pytest.approx(0.121)  # knox-1 sparse median
    assert _density(DENSITY_REFERENCE_CHARS) == 1.0
    assert _density(6066) == 1.0  # knox-1 dense max saturates
    assert _density(20_000) == 1.0  # saturation, not unbounded growth


def test_reason_string_unchanged_for_shape_b() -> None:
    """The prose channel stays audit-only: the typed fact is what the model reads."""
    analysis = make_analysis([make_signal(1, native=True)], 1)
    backends = [make_desc("native-text"), make_desc("docling", group="docling")]

    plan = plan_route(analysis, backends, full_constraints(formats={"text/plain"}))

    reason = plan.pages[0].reason
    assert "text_chars=500" in reason
    assert "density" not in reason


def test_end_to_end_stub_judge_uses_density() -> None:
    """A density-aware judge sees the typed fact through plan_route and acts on it."""

    class _DensityStubJudge(RoutingJudge):
        """Prefers ocr-tele for sparse pages (<0.3), ocr-ovis for dense ones."""

        def __init__(self) -> None:
            self.seen_densities: list[float] = []

        def rank(
            self,
            intent: Intent,
            candidates: Sequence[BackendDescriptor],
            context: PageContext | None = None,
        ) -> Sequence[str]:
            assert context is not None  # an OCR page carries the A8 context
            self.seen_densities.append(context.density)
            return ["ocr-tele", "ocr-ovis"] if context.density < 0.3 else ["ocr-ovis", "ocr-tele"]

    def _chosen(chars: int) -> tuple[str, list[float]]:
        stub = _DensityStubJudge()
        analysis = make_analysis([make_signal(1, text_chars=chars, replacement=0.25)], 1)
        backends = [
            make_desc("ocr-ovis", gpu=True, vram=6.0, group="ocr-ovis"),
            make_desc("ocr-tele", gpu=True, vram=2.0, group="ocr-tele"),
        ]
        plan = plan_route(analysis, backends, full_constraints(), stub)
        return plan.pages[0].chosen, stub.seen_densities

    sparse_chosen, sparse_seen = _chosen(90)  # density 0.09
    dense_chosen, dense_seen = _chosen(900)  # density 0.9
    assert sparse_seen == [pytest.approx(0.09)]
    assert sparse_chosen == "ocr-tele"
    assert dense_seen == [pytest.approx(0.9)]
    assert dense_chosen == "ocr-ovis"


def test_end_to_end_stub_judge_sees_native_density() -> None:
    """pc-86l: a NATIVE page's density reaches the judge too (needs_ocr false)."""

    class _NativeDensityStubJudge(RoutingJudge):
        """Prefers native-markdown for dense native pages, native-text for sparse ones."""

        def __init__(self) -> None:
            self.seen: list[PageContext | None] = []

        def rank(
            self,
            intent: Intent,
            candidates: Sequence[BackendDescriptor],
            context: PageContext | None = None,
        ) -> Sequence[str]:
            self.seen.append(context)
            assert context is not None  # pc-86l: native pages carry a context
            return ["native-markdown", "native-text"] if context.density >= 0.3 else ["native-text", "native-markdown"]

    def _chosen(chars: int) -> str:
        stub = _NativeDensityStubJudge()
        analysis = make_analysis([make_signal(1, native=True, text_chars=chars)], 1)
        backends = [make_desc("native-text"), make_desc("native-markdown")]
        plan = plan_route(analysis, backends, full_constraints(formats={"text/plain"}), stub)
        return plan.pages[0].chosen

    assert _chosen(100) == "native-text"  # sparse
    assert _chosen(6000) == "native-markdown"  # dense


def test_degradation_fields_must_travel_together() -> None:
    with pytest.raises(ValidationError, match="must appear together"):
        PageRoute(
            page_number=1,
            intent=Intent.NATIVE,
            candidates=["native-text"],
            chosen="native-text",
            reason="r",
            degradation_code="degraded-short-text",
        )
    with pytest.raises(ValidationError, match="must appear together"):
        PageRoute(
            page_number=1,
            intent=Intent.NATIVE,
            candidates=["native-text"],
            chosen="native-text",
            reason="r",
            degradation_score=0.5,
        )


def test_apply_preference_rotates_eligible_preferred_to_front() -> None:
    """Preferred backend goes to the front of an eligible order; never wider."""
    preferred = BackendDescriptor(
        name="preferred",
        version="1.0.0",
        capabilities=BackendCapabilities(supported_formats=["text/plain"]),
    )
    other = BackendDescriptor(
        name="other",
        version="1.0.0",
        capabilities=BackendCapabilities(supported_formats=["text/plain"]),
    )
    family = [preferred, other]
    constraints = RoutingConstraints(preferred_backend="preferred")
    assert _apply_preference(["other", "preferred"], family, Intent.NATIVE, constraints) == ["preferred", "other"]
    # Not eligible on this page: order is the judge's output, unchanged.
    assert _apply_preference(["other"], family, Intent.NATIVE, constraints) == ["other"]


def test_apply_preference_moves_a_named_ocr_backend_to_the_front() -> None:
    """Naming an OCR backend is the caller paying for its tier (pc-4u7.41).

    The old rule parked a named OCR backend behind a native lead, which made the
    quality tier unreachable on digital PDFs. Naming the backend is an explicit
    choice, so it leads; the guard that remains is against the UNPRICED case (a
    judge leading with a VLM nobody asked for), covered by
    ``test_plan_rejects_a_judge_that_leads_a_native_page_with_ocr``.
    """
    family = native_only_family()
    constraints = full_constraints(formats=ALL_FORMATS, preferred_backend="ocr-tesseract")
    assert _apply_preference(["ocr-tesseract", "native-lead", "ocr-other"], family, Intent.NATIVE, constraints) == [
        "ocr-tesseract",
        "native-lead",
        "ocr-other",
    ]


def test_only_backend_pins_the_plan_to_one_backend() -> None:
    """``only_backend`` is a hard filter; the pinned backend leads every page it covers."""
    signals = [make_signal(1), make_signal(2, text_chars=0, native=False, blank=True, images=2)]
    constraints = full_constraints(formats={"text/plain"}, only_backend="native-text")
    plan = plan_route(
        make_analysis(signals, 2),
        [make_desc("native-text"), make_desc("ocr-ovis", ALL_FORMATS, group="ocr-ovis", vram=2.0)],
        constraints,
    )
    # Page 1 is native; page 2 is blank with a figure, so the pin excludes the
    # OCR family and it degrades to the pinned native backend (content is not
    # coverage). A page the pin cannot cover at all raises — see the next test.
    assert [page.chosen for page in plan.pages] == ["native-text", "native-text"]


def test_only_backend_that_cannot_serve_raises() -> None:
    constraints = full_constraints(formats={"text/plain"}, only_backend="native-html")
    with pytest.raises(NoEligibleBackendError, match="pinned backend 'native-html'"):
        plan_route(
            make_analysis([make_signal(1)], 1),
            [make_desc("native-text"), make_desc("native-html", ("text/html",))],
            constraints,
        )


def test_only_backend_still_degrades_a_blank_page() -> None:
    """Coverage is not content: a pinned native backend serves a blank page by degrade."""
    signal = make_signal(1, text_chars=0, native=False, blank=True, images=0)
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-text"), make_desc("ocr-ovis", ALL_FORMATS, group="ocr-ovis", vram=2.0)],
        full_constraints(formats={"text/plain"}, only_backend="native-text"),
    )
    page = plan.pages[0]
    assert page.chosen == "native-text"
    assert page.degradation_code == DEGRADED_BLANK_PAGE_CODE


def test_a_named_ocr_backend_may_lead_a_native_page() -> None:
    """The correction (pc-4u7.41): naming an OCR backend PAYS for its tier.

    The OCR family is the deliberate high-accuracy harness for figures, tables and
    equations, reachable on digital PDFs too. The default guard stays (a judge
    cannot spend a VLM on text a native backend reads in milliseconds), and it is
    overridden by the caller's own choice - never by the judge.
    """
    signal = make_signal(1)  # native intent: real text, no images
    constraints = full_constraints(formats={"application/pdf"}, preferred_backend="ocr-ovis")
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-pdf", ("application/pdf",)), make_desc("ocr-ovis", ("application/pdf",))],
        constraints,
    )
    page = plan.pages[0]
    assert page.intent is Intent.NATIVE
    assert page.chosen == "ocr-ovis"


def test_a_native_backend_still_leads_without_a_name() -> None:
    """The default guard, unchanged: with nothing named, tier 2 cannot lead."""
    signal = make_signal(1)
    plan = plan_route(
        make_analysis([signal], 1),
        [make_desc("native-pdf", ("application/pdf",)), make_desc("ocr-ovis", ("application/pdf",))],
        full_constraints(formats={"application/pdf"}),
    )
    assert plan.pages[0].chosen == "native-pdf"


def make_analysis(signals: list[PageSignal], page_count: int, codes: tuple[str, ...] = ()) -> AnalysisResult:
    diagnostics = [Diagnostic(level=DiagnosticLevel.INFO, code=code, message=f"synthetic hint {code}") for code in codes]
    return AnalysisResult(source_hash="0" * 64, page_count=page_count, signals=signals, diagnostics=diagnostics)


def make_signal(
    page: int = 1, *, text_chars: int = 500, native: bool = True, blank: bool = False, replacement: float | None = None, images: int = 0
) -> PageSignal:
    return PageSignal(
        page_number=page,
        has_native_text=native,
        text_chars=text_chars,
        image_count=images,
        blank=blank,
        replacement_char_ratio=replacement,
    )


def native_only_family() -> list[Any]:
    """A NATIVE page's family: one native candidate and two OCR candidates."""
    return [
        make_desc("native-lead", ALL_FORMATS),
        make_desc("ocr-tesseract", ALL_FORMATS, vram=1.0),
        make_desc("ocr-other", ALL_FORMATS, vram=2.0),
    ]


def test_default_lead_cap_follows_an_all_ocr_family() -> None:
    """An all-tier-2 family caps at its own tier instead of rejecting the only option.

    Covered directly rather than through a routing scenario: the earlier CI run
    measured planner.py:225 uncovered on ubuntu/3.14 while a Windows run covered
    it, which means the only path reaching it was platform-dependent - the
    helper is pure, so pin it as one.
    """
    ocr_only = [make_desc("ocr-ovis", ALL_FORMATS), make_desc("ocr-tesseract", ALL_FORMATS)]
    assert _default_lead_cap(ocr_only) == 2

    # Tiers 0 and 1 lead freely: the cap is 1 whenever anything below 2 exists.
    mixed = [make_desc("native-pdf", ALL_FORMATS), make_desc("ocr-ovis", ALL_FORMATS)]
    assert _default_lead_cap(mixed) == 1
    tier_one_only = [make_desc("docling", ALL_FORMATS)]
    assert _default_lead_cap(tier_one_only) == 1


def test_lead_tier_cap_is_none_unless_the_named_backend_is_in_the_family() -> None:
    """A name outside this page's family leaves the default cap in place."""
    family = [make_desc("native-pdf", ALL_FORMATS), make_desc("ocr-ovis", ALL_FORMATS)]
    assert _lead_tier_cap(full_constraints(formats=ALL_FORMATS), family) is None
    assert _lead_tier_cap(full_constraints(formats=ALL_FORMATS, preferred_backend="ocr-ovis"), family) == 2
    assert _lead_tier_cap(full_constraints(formats=ALL_FORMATS, preferred_backend="absent"), family) is None


def full_constraints(**overrides: Any) -> RoutingConstraints:
    base: dict[str, Any] = {
        "installed_extras": set(OCR_EXTRAS),
        "vram_budget_gb": 8.0,
        # These fixtures model a GPU host whose runtime can actually use it;
        # the unusable-runtime case is covered on its own below.
        "gpu_usable": True,
        "max_passes": 3,
    }
    base.update(overrides)
    return RoutingConstraints(**base)


def make_desc(
    name: str,
    formats: Iterable[str] = ("text/plain",),
    *,
    gpu: bool = False,
    vram: float | None = None,
    group: str | None = None,
    with_asset: bool = False,
    required_engine: str | None = None,
    convert_extra: str | None = None,
) -> BackendDescriptor:
    asset = (
        ModelAssetDescriptor(
            model_id="acme/model",
            model_revision="r1",
            model_license="apache-2.0",
            code_license="apache-2.0",
            asset_license="cc-by-4.0",
        )
        if with_asset
        else None
    )
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(
            supported_formats=list(formats),
            gpu_requirement=1.0 if gpu else 0.0,
            estimated_vram_gb=vram,
            optional_dependency_group=group,
            model_asset=asset,
            required_engine=required_engine,
            convert_extra=convert_extra,
        ),
    )
