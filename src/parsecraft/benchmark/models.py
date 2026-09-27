"""Typed benchmark records: one metrics row per (document, backend)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from parsecraft.ir.models import FailureCode


class BenchmarkFailure(BaseModel):
    """One typed failure observed during a benchmarked conversion."""

    code: FailureCode
    detail: str


class BenchmarkMetrics(BaseModel):
    """Metrics for one (document, backend) cell of the benchmark matrix."""

    document: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    elapsed_s: float = Field(ge=0)
    pages: int = Field(ge=0)
    pages_per_second: float | None = Field(default=None, ge=0)
    chunk_counts: dict[str, int] = Field(default_factory=dict)
    emitted_chars: int = Field(ge=0)
    analyzer_text_chars: int = Field(ge=0)
    text_coverage: float | None = Field(default=None, ge=0)
    failures: list[BenchmarkFailure] = Field(default_factory=list)
    peak_memory_bytes: int = Field(ge=0)
    selected: bool | None = None


class BenchmarkSkip(BaseModel):
    """A document that was not benchmarked, with a stable reason."""

    document: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class BenchmarkReport(BaseModel):
    """Full report. No timestamps — reports must diff cleanly between runs."""

    package_version: str = Field(min_length=1)
    results: list[BenchmarkMetrics] = Field(default_factory=list)
    skips: list[BenchmarkSkip] = Field(default_factory=list)
