"""Typed records, enums, and errors for the auto-mode routing harness."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Intent(StrEnum):
    """What a page needs: native extraction or a specific OCR flavor."""

    NATIVE = "native"
    OCR_GENERAL = "ocr"
    OCR_TABLES = "ocr-tables"
    OCR_VISION = "ocr-vision"


class RoutingPreference(StrEnum):
    """How hard the caller wants the ranking to lean towards capability.

    A *preference*, never a permission: it only orders candidates the hard
    constraints already admitted, and it never crosses the family precedence
    (a native lead stays ahead of OCR for ``Intent.NATIVE``).
    """

    SPEED = "speed"
    BALANCED = "balanced"
    QUALITY = "quality"


class RoutingError(Exception):
    """Base class for all routing-harness failures."""


class NoEligibleBackendError(RoutingError):
    """No backend survives the code-owned hard constraints."""

    def __init__(self, intent: Intent | None, detail: str) -> None:
        self.intent = intent
        self.detail = detail
        suffix = f" for intent {intent.value!r}" if intent is not None else ""
        super().__init__(f"no eligible backend{suffix}: {detail}")


class JudgeViolationError(RoutingError):
    """An injected judge returned a candidate the hard rules reject."""

    def __init__(self, name: str, detail: str, intent: Intent) -> None:
        self.name = name
        self.detail = detail
        self.intent = intent
        super().__init__(f"judge returned invalid candidate {name!r} for intent {intent.value!r}: {detail}")


class RoutingConstraints(BaseModel):
    """Hard, code-owned planning constraints — never delegated to a judge.

    ``formats`` is the set of source **MIME media types** the plan must
    serve; a candidate must support every one of them. An empty set means no
    format restriction.
    """

    formats: set[str] = Field(default_factory=set)
    installed_extras: set[str] = Field(default_factory=set)
    vram_budget_gb: float = Field(default=0.0, ge=0)
    max_passes: int = Field(default=1, ge=1)
    allow_ocr: bool = True
    offline: bool = True
    #: Requested document language (BCP-47); ``None`` places no language
    #: restriction. A language-agnostic candidate is never excluded by it.
    language: str | None = None
    #: Ranking preference *within* the eligible family (see
    #: :class:`RoutingPreference`); it orders candidates, it never widens them.
    preference: RoutingPreference = RoutingPreference.BALANCED


class PageRoute(BaseModel):
    """One page's plan: ordered candidate passes and the chosen reason.

    ``candidates`` is capped at ``constraints.max_passes``; the first entry
    is the pass-1 route and any later entries are fallback passes.
    """

    page_number: int = Field(ge=1)
    intent: Intent
    candidates: list[str] = Field(min_length=1)
    chosen: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    #: Set only when ``plan_route`` degraded this page (NATIVE-lead mirror):
    #: distinguishes mojibake (garbled) from thin content (short text).
    degradation_code: str | None = None
    #: Threshold fraction met, in [0, 1]; set together with ``degradation_code``.
    degradation_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _chosen_is_first(self) -> PageRoute:
        if self.chosen != self.candidates[0]:
            msg = f"chosen {self.chosen!r} must be candidates[0] {self.candidates[0]!r}"
            raise ValueError(msg)
        if (self.degradation_code is None) != (self.degradation_score is None):
            msg = "degradation_code and degradation_score must appear together"
            raise ValueError(msg)
        return self


class RoutingPlan(BaseModel):
    """Deterministic outcome of ``plan_route``: identical inputs → identical plan."""

    primary: str = Field(min_length=1)
    pages: list[PageRoute] = Field(min_length=1)
