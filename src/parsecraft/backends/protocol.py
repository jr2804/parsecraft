"""Public backend protocol: what a backend is and what a request carries.

Third parties implement :class:`DocumentBackend` and expose a
:class:`BackendFactory` — see ``AGENTS.md`` next to this file for the
registration contract. Heavy runtimes (vLLM, Transformers, model code) must be
imported *inside* factory/backend methods, never at module import time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from parsecraft.ir.models import (
    Diagnostic,
    PageRange,
    PageResult,
    PageSignal,
    PassFailure,
)


class SourceDocument(BaseModel):
    """A document handed to a backend: path-based or in-memory."""

    uri: str = Field(min_length=1)
    media_type: str | None = None
    content: bytes | None = None


class AssetFilePin(BaseModel):
    """One pinned file of a model asset: repo path, sha256, size in bytes."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int | None = Field(default=None, ge=0)


class ModelAssetDescriptor(BaseModel):
    """Pinned model-asset metadata (license + reproducibility contract)."""

    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_license: str = Field(min_length=1)
    code_license: str = Field(min_length=1)
    asset_license: str = Field(min_length=1)
    requires_user_acceptance: bool = False
    source_urls: list[str] = Field(default_factory=list)
    size_bytes: int | None = Field(default=None, ge=0)
    quantization: str | None = None
    estimated_vram_gb: float | None = Field(default=None, ge=0)
    #: Per-file integrity manifest for downloads (empty until pinned).
    file_pins: tuple[AssetFilePin, ...] = ()


class BackendCapabilities(BaseModel):
    """Static, import-free facts about a backend."""

    #: MIME media types this backend can parse — a *capability* statement, not a
    #: file-discovery list. Walking a filesystem needs extension→MIME mapping,
    #: which is lossy and OS-dependent and therefore the consumer's job.
    supported_formats: list[str] = Field(default_factory=list)
    supports_page_ranges: bool = True
    supports_multi_page: bool = True
    requires_gpu: bool = False
    estimated_vram_gb: float | None = Field(default=None, ge=0)
    optional_dependency_group: str | None = None
    model_asset: ModelAssetDescriptor | None = None


class BackendDescriptor(BaseModel):
    """What the registry hands out: identity + capabilities, no code."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    version: str = Field(default="0.0.0", min_length=1)
    capabilities: BackendCapabilities

    # `factory` is deliberately NOT a field: the registry binds descriptor to
    # factory internally, keeping this a pure data record (serializable, no
    # callable on the model). Deviation from the plan's descriptor sketch.


class BackendConfig(BaseModel):
    """Per-instance backend configuration passed to the factory."""

    name: str = Field(min_length=1)
    options: dict[str, str | int | float | bool] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """Output of ``DocumentBackend.analyze`` — planner input state."""

    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0)
    signals: list[PageSignal] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class ConversionRequest(BaseModel):
    """Everything a backend may do — bounds live here, not in global state.

    ``cancellation`` is polled for a ``True`` result; timeouts and budgets are
    hard expectations a backend must honor or return a structured failure.
    """

    source: SourceDocument
    page_range: PageRange | None = None
    region_ids: frozenset[str] = Field(default_factory=frozenset)
    timeout_s: float | None = Field(default=None, gt=0)
    cancellation: Callable[[], bool] | None = None
    max_output_chars: int | None = Field(default=None, gt=0)
    max_context_tokens: int | None = Field(default=None, gt=0)


class BackendRef(BaseModel):
    """Identity of the backend that produced a result (provenance anchor)."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_id: str | None = None
    model_revision: str | None = None


class BackendResult(BaseModel):
    """Converged IR output of ``DocumentBackend.convert``."""

    backend: BackendRef
    pages: list[PageResult] = Field(default_factory=list)
    failures: list[PassFailure] = Field(default_factory=list)
    elapsed_s: float = Field(ge=0)


@runtime_checkable
class DocumentBackend(Protocol):
    """A backend: analyzes a source and converts bounded slices of it."""

    name: str
    capabilities: BackendCapabilities

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        """Collect deterministic signals without converting content."""
        ...

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert the requested slice into canonical IR pages."""
        ...


@runtime_checkable
class BackendFactory(Protocol):
    """What registration accepts: a light module-level object.

    ``descriptor`` must carry the registry name; ``__call__`` is the *only*
    place heavy imports may happen.
    """

    descriptor: BackendDescriptor

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        """Build a backend instance for ``config``."""
        ...


__all__ = [
    "AnalysisResult",
    "BackendCapabilities",
    "BackendConfig",
    "BackendDescriptor",
    "BackendFactory",
    "BackendRef",
    "BackendResult",
    "ConversionRequest",
    "DocumentBackend",
    "ModelAssetDescriptor",
    "PageSignal",
    "SourceDocument",
]
