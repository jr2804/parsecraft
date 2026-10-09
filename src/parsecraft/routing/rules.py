"""Code-owned eligibility rules and the signal→intent rule table.

Everything here is a hard constraint or a documented rule constant — the
judge only ever re-ranks what this module accepts. Rules live in one table
(:data:`INTENT_RULES` order is precedence), never scattered.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from parsecraft.backends.protocol import GPU_REQUIRED, AnalysisResult, BackendDescriptor
from parsecraft.ir.models import PageSignal
from parsecraft.routing.models import Intent, RoutingConstraints

#: A page with fewer text characters than this is not natively usable.
NATIVE_MIN_TEXT_CHARS = 40
#: Replacement-character ratio above this means the native text is garbled.
MAX_REPLACEMENT_RATIO = 0.05
#: Total image count at or above this makes a document figures-heavy.
HEAVY_IMAGE_COUNT = 5

#: Diagnostic codes a backend (or a synthesized test analysis) emits to
#: advertise document-level feature hints to the planner.
FEATURE_TABLE_CODE = "feature:tables"
FEATURE_EQUATIONS_CODE = "feature:equations"
FEATURE_FIGURES_CODE = "feature:figures"
#: Convert-time feature code (pc-0uv): the page's text is significantly
#: monospace, so the IR carries CODE chunks and the projection fences them.
#: ``analyze()`` runs on pypdf alone and sees no fonts, so this code reaches the
#: IR but never the planner — no intent rule consumes it, and code pages keep
#: routing NATIVE, which is correct.
FEATURE_CODE_CODE = "feature:code"


#: Degradation codes: mojibake vs thin content vs nothing-at-all mean different
#: things downstream.
DEGRADED_GARBLED_TEXT_CODE = "degraded-garbled-text"
DEGRADED_SHORT_TEXT_CODE = "degraded-short-text"
#: A blank page (pc-ztq): it holds no content, so an empty native page loses
#: nothing — unlike mojibake or thin text, there is no extraction to lose.
DEGRADED_BLANK_PAGE_CODE = "degraded-blank-page"


class IntentRule(BaseModel):
    """One row of the signal→intent table (first match wins)."""

    intent: Intent
    condition: str


#: The signal→intent rule table, in evaluation precedence order.
INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        intent=Intent.OCR_TABLES,
        condition="page needs OCR AND feature:tables hint AND page_count > 1 → dense multi-page tables",
    ),
    IntentRule(
        intent=Intent.OCR_VISION,
        condition="page needs OCR AND (feature:equations OR feature:figures hint OR image_count sum ≥ 5) → vision OCR",
    ),
    IntentRule(
        intent=Intent.OCR_GENERAL,
        condition="page needs OCR AND no feature hint → generic OCR",
    ),
    IntentRule(
        intent=Intent.NATIVE,
        condition="native text present, text_chars ≥ 40, replacement_char_ratio ≤ 0.05",
    ),
)


class FeatureHints(BaseModel):
    """Document-level hints distilled from analysis diagnostics/signals."""

    tables: bool = False
    equations: bool = False
    figures: bool = False

    model_config = {"frozen": True}


def extract_hints(analysis: AnalysisResult) -> FeatureHints:
    """Distill document-level feature hints (diagnostics codes + image mass)."""
    codes = {diagnostic.code for diagnostic in analysis.diagnostics}
    total_images = sum(signal.image_count for signal in analysis.signals)
    return FeatureHints(
        tables=FEATURE_TABLE_CODE in codes,
        equations=FEATURE_EQUATIONS_CODE in codes,
        figures=FEATURE_FIGURES_CODE in codes or total_images >= HEAVY_IMAGE_COUNT,
    )


def is_hard_eligible(descriptor: BackendDescriptor, constraints: RoutingConstraints) -> bool:
    """Code-owned hard constraints — never delegated to a judge."""
    capabilities = descriptor.capabilities
    ocr_allowed = constraints.allow_ocr or not is_ocr(descriptor)
    group = capabilities.optional_dependency_group
    extra_installed = group is None or group in constraints.installed_extras
    # Only a HARD GPU requirement gates here: a CPU-capable backend stays
    # eligible without a GPU (a slow answer beats no answer), while a hard one
    # is dropped unless the runtime can actually use the GPU *and* fits budget.
    gpu_ok = capabilities.gpu_requirement < GPU_REQUIRED or (
        constraints.gpu_usable and capabilities.estimated_vram_gb is not None and capabilities.estimated_vram_gb <= constraints.vram_budget_gb
    )
    formats_covered = not constraints.formats or constraints.formats.issubset(set(capabilities.supported_formats))
    offline_ok = not constraints.offline or capabilities.model_asset is None
    declared_languages = capabilities.languages
    # A language request only narrows backends that DECLARE languages:
    # language-agnostic candidates (native included) are never excluded.
    language_ok = constraints.language is None or not declared_languages or constraints.language in declared_languages
    # A declared engine is a precondition, exactly like a hard GPU requirement:
    # a backend that needs a binary the host does not have cannot answer, and
    # admitting it would route pages into a pass that can only fail. The name is
    # compared against the host's probed engines and nothing else — no backend
    # name is special-cased (ADR-0008 decision 13).
    engine_ok = capabilities.required_engine is None or capabilities.required_engine in constraints.engines
    return ocr_allowed and extra_installed and gpu_ok and formats_covered and offline_ok and language_ok and engine_ok


def in_intent_family(intent: Intent, descriptor: BackendDescriptor) -> bool:
    """OCR intents accept only OCR backends; NATIVE admits native + OCR fallbacks.

    Membership here is necessary, not sufficient: for a NATIVE page an OCR
    backend is a *fallback*, and ``plan_route`` keeps it behind every
    native-capable candidate (``_validate_order``).
    """
    if intent is Intent.NATIVE:
        return True
    return is_ocr(descriptor)


def degradation_for(signal: PageSignal) -> tuple[str, float]:
    """``(code, score)`` recorded when an OCR-intent page degrades to native.

    A blank page scores 0.0: it holds none of the text a native pass is asked
    for, and an empty page is the honest result rather than a loss. Garbled pages
    report the clean-text share (``1 - replacement_ratio``); every other
    degrading page reports the share of the native-text threshold met
    (``text_chars / NATIVE_MIN_TEXT_CHARS``) — both in ``[0, 1]``.
    """
    if signal.blank:
        return DEGRADED_BLANK_PAGE_CODE, 0.0
    ratio = signal.replacement_char_ratio
    if ratio is not None and ratio > MAX_REPLACEMENT_RATIO:
        return DEGRADED_GARBLED_TEXT_CODE, round(max(1.0 - ratio, 0.0), 6)
    return DEGRADED_SHORT_TEXT_CODE, round(min(signal.text_chars / NATIVE_MIN_TEXT_CHARS, 1.0), 6)


def can_degrade_to_native(signal: PageSignal, eligible: Sequence[BackendDescriptor]) -> bool:
    """OCR-intent fallback: whether a native pass can honestly stand in.

    Mirror of the planner's NATIVE-lead guard: with no OCR family available
    (missing extras, ``allow_ocr`` off, no usable GPU) a page may degrade to
    NATIVE with a recorded reason — but only when native can tell the truth about
    it. Three shapes differ, and only two may degrade:

    - **blank** — there is nothing to read, so native's empty page *is* the
      result. Refusing here failed a whole document over a page with no content
      (pc-ztq); the OCR intent is untouched, so the page still routes to OCR
      whenever an OCR backend is eligible.
    - **thin or garbled text** — native extracts what is there; the recorded
      degradation keeps the loss visible.
    - **scanned, no native text** — native would emit an empty page for a page
      that visibly has content, so the refusal stays.
    """
    return (signal.blank or signal.has_native_text) and any(not is_ocr(descriptor) for descriptor in eligible)


def is_ocr(descriptor: BackendDescriptor) -> bool:
    """Whether a backend is an OCR/VLM route (by name or dependency group)."""
    group = descriptor.capabilities.optional_dependency_group
    return descriptor.name.startswith("ocr-") or (group is not None and group.startswith("ocr-"))


def classify_page(signal: PageSignal, page_count: int, hints: FeatureHints) -> Intent:
    """Apply the rule table: OCR need first, then the OCR flavor, else NATIVE."""
    if not page_needs_ocr(signal):
        return Intent.NATIVE
    if hints.tables and page_count > 1:
        return Intent.OCR_TABLES
    if hints.equations or hints.figures:
        return Intent.OCR_VISION
    return Intent.OCR_GENERAL


def page_needs_ocr(signal: PageSignal) -> bool:
    """Whether a page's own signals rule out native extraction.

    A classifier verdict is augment-only: it can add OCR-need, never remove
    text-statistics OCR-need (ADR-0004 A2).
    """
    if signal.classifier_needs_ocr is True:
        return True
    if signal.blank or not signal.has_native_text:
        return True
    if signal.text_chars < NATIVE_MIN_TEXT_CHARS:
        return True
    return signal.replacement_char_ratio is not None and signal.replacement_char_ratio > MAX_REPLACEMENT_RATIO
