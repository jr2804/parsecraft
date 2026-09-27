"""Typed execution records: per-group attempts and the pipeline outcome."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from parsecraft.ir.models import DocumentResult, PassFailure, PassStatus
from parsecraft.routing import Intent, RoutingPlan


class PassAttempt(BaseModel):
    """One candidate tried for a group: winner or typed failure record."""

    backend: str = Field(min_length=1)
    status: PassStatus
    failure: PassFailure | None = None

    @model_validator(mode="after")
    def _failure_matches_status(self) -> PassAttempt:
        if (self.status is PassStatus.FAILED) != (self.failure is not None):
            msg = "status 'failed' and failure record must appear together"
            raise ValueError(msg)
        return self


class PageGroup(BaseModel):
    """Contiguous pages executed together (per-page plan → per-range dispatch)."""

    page_numbers: list[int] = Field(min_length=1)
    intent: Intent
    candidates: list[str] = Field(min_length=1)
    winner: str | None = None
    attempts: list[PassAttempt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _group_consistent(self) -> PageGroup:
        expected = list(range(self.page_numbers[0], self.page_numbers[0] + len(self.page_numbers)))
        if self.page_numbers != expected:
            msg = f"page_numbers must be ascending and contiguous, got {self.page_numbers}"
            raise ValueError(msg)
        ok_backends = [attempt.backend for attempt in self.attempts if attempt.status is PassStatus.OK]
        if self.winner is None and ok_backends:
            msg = "winner missing although an attempt succeeded"
            raise ValueError(msg)
        if self.winner is not None and (self.winner not in self.candidates or self.winner not in ok_backends):
            msg = f"winner {self.winner!r} must be an OK-attempted candidate"
            raise ValueError(msg)
        return self


class PipelineResult(BaseModel):
    """Full outcome: aggregated document, the plan, and execution records."""

    document: DocumentResult
    plan: RoutingPlan
    groups: list[PageGroup] = Field(min_length=1)
