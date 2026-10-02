"""Offline tests for the auto-mode routing harness (planner + judge + rules)."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
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
from parsecraft.routing.judge import PREFERRED_BACKENDS, RoutingJudge
from parsecraft.routing.rules import (
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
NATIVE_NAMES = ("native-html", "native-markdown", "native-pdf", "native-text")

_SOURCES_PATH = Path(__file__).parent / "fixtures" / "sources.toml"
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


def test_format_coverage_rules() -> None:
    pdf_backend = make_desc("native-pdf", ("application/pdf",))
    assert is_hard_eligible(pdf_backend, full_constraints(formats={"application/pdf"})) is True
    assert is_hard_eligible(pdf_backend, full_constraints(formats={"text/plain"})) is False
    assert is_hard_eligible(pdf_backend, full_constraints(formats=set())) is True


def test_allow_ocr_and_offline_rules() -> None:
    tele = make_desc("ocr-tele", ALL_FORMATS, group="ocr-tele")
    assert is_hard_eligible(tele, full_constraints(allow_ocr=False)) is False
    assetful = make_desc("ocr-ovis", ALL_FORMATS, gpu=True, vram=6.0, group="ocr-ovis", with_asset=True)
    assert is_hard_eligible(assetful, full_constraints(offline=True)) is False
    assert is_hard_eligible(assetful, full_constraints(offline=False)) is True


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
    # Two pages: one routed native, one OCR — no majority, so name order wins.
    signals = [make_signal(1), make_signal(2, blank=True)]
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


def test_plan_hands_a_context_to_ocr_pages_only() -> None:
    """A judge sees bounded page facts for an OCR page and nothing extra for a native one (A8)."""
    seen: list[tuple[str, PageContext | None]] = []

    class RecordingJudge:
        @staticmethod
        def rank(intent: Intent, candidates: Any, context: Any = None) -> list[str]:
            seen.append((intent.value, context))
            return [descriptor.name for descriptor in candidates]

    analysis = make_analysis([*good_native(), *garbled(1)], 2)
    plan_route(analysis, all_backends(), full_constraints(formats=set(), max_passes=9), RecordingJudge())

    assert [intent for intent, _ in seen] == ["native", "ocr"]
    native_context = seen[0][1]
    ocr_context = seen[1][1]
    assert native_context is None  # a native ordering question carries no page facts
    assert ocr_context is not None
    assert ocr_context.needs_ocr is True
    assert ocr_context.blank is False
    assert ocr_context.hints == extract_hints(analysis)
    # hints are document-level hence plan-constant, so they stay out of the memo key
    assert ocr_context.memo_key() == (True, False)


def test_page_context_is_none_for_pages_degraded_to_native() -> None:
    """A page degraded to native is ranked as a native page — no context, no memo churn."""
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
    assert seen == [None]  # degraded: ranked as native, so the judge sees what it always saw


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

    with pytest.raises(JudgeViolationError, match="may not lead a NATIVE page"):
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
    assert plan.pages[0].chosen == "native-text"  # last of the reversed native group


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
    # A blank page never degrades (native would emit nothing) — with the OCR
    # family forbidden, routing still fails loudly.
    blank = make_analysis([make_signal(1, text_chars=0, native=False, blank=True)], 1)
    with pytest.raises(NoEligibleBackendError) as exc_info:
        plan_route(blank, all_backends(), full_constraints(allow_ocr=False, formats=set()))
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
        make_desc("native-pdf", ("application/pdf",)),
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
    assert "OCR unavailable (no eligible OCR backend)" in page.reason
    assert "degraded to native" in page.reason
    assert "text_chars=34" in page.reason


def test_blank_page_still_errors_when_ocr_family_empty() -> None:
    signal = make_signal(1, text_chars=0, native=False, blank=True)
    analysis = make_analysis([signal], 1)
    assert classify_page(signal, 1, extract_hints(analysis)) is Intent.OCR_GENERAL
    with pytest.raises(NoEligibleBackendError) as exc_info:
        plan_route(
            analysis,
            [make_desc("native-text")],
            full_constraints(formats={"text/plain"}, installed_extras=set()),
        )
    assert exc_info.value.intent is Intent.OCR_GENERAL  # native would emit nothing


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
    assert can_degrade_to_native(blank, [make_desc("native-text")]) is False


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


def make_analysis(signals: list[PageSignal], page_count: int, codes: tuple[str, ...] = ()) -> AnalysisResult:
    diagnostics = [Diagnostic(level=DiagnosticLevel.INFO, code=code, message=f"synthetic hint {code}") for code in codes]
    return AnalysisResult(source_hash="0" * 64, page_count=page_count, signals=signals, diagnostics=diagnostics)


def make_desc(
    name: str,
    formats: Iterable[str] = ("text/plain",),
    *,
    gpu: bool = False,
    vram: float | None = None,
    group: str | None = None,
    with_asset: bool = False,
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
        ),
    )


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
