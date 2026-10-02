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


#: Degradation codes: mojibake vs thin content mean different things downstream.
DEGRADED_GARBLED_TEXT_CODE = "degraded-garbled-text"
DEGRADED_SHORT_TEXT_CODE = "degraded-short-text"


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

#: The closed set of feature codes: the three above are planner hints
#: (:func:`extract_hints`), ``FEATURE_CODE_CODE`` is IR-only for now.
FEATURE_CODES: frozenset[str] = frozenset({FEATURE_TABLE_CODE, FEATURE_EQUATIONS_CODE, FEATURE_FIGURES_CODE, FEATURE_CODE_CODE})


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
    return ocr_allowed and extra_installed and gpu_ok and formats_covered and offline_ok and language_ok


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

    Garbled pages report the clean-text share (``1 - replacement_ratio``);
    thin pages report the share of the native-text threshold met
    (``text_chars / NATIVE_MIN_TEXT_CHARS``) — both in ``[0, 1]``.
    """
    ratio = signal.replacement_char_ratio
    if ratio is not None and ratio > MAX_REPLACEMENT_RATIO:
        return DEGRADED_GARBLED_TEXT_CODE, round(max(1.0 - ratio, 0.0), 6)
    return DEGRADED_SHORT_TEXT_CODE, round(min(signal.text_chars / NATIVE_MIN_TEXT_CHARS, 1.0), 6)


def can_degrade_to_native(signal: PageSignal, eligible: Sequence[BackendDescriptor]) -> bool:
    """OCR-intent fallback: only when the page can actually emit native text.

    Mirror of the planner's NATIVE-lead guard: with no OCR family available,
    a page that still has native text (not blank, not text-less) degrades to
    NATIVE with a recorded reason; a genuinely blank/no-native-text page must
    keep raising, because native would emit nothing.
    """
    return not signal.blank and signal.has_native_text and any(not is_ocr(descriptor) for descriptor in eligible)


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
