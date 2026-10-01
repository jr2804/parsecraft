"""Optional per-page OCR-need classifier — an injectable seam; the core never
imports a classifier implementation.

A classifier produces :class:`OcrFacts` from a source document; a pure fold
(:func:`parsecraft.pipeline.analysis.apply_classifier`) merges those facts into
an ``AnalysisResult`` before ``plan_route`` runs. The seam is augment-only and
local-only (ADR-0004 A1-A4):

- It may ADD OCR-need for a page, never remove it: OCR-need is
  ``page_needs_ocr(signal) OR signal.classifier_needs_ocr``. A ``text_based``
  verdict must never force ``NATIVE`` on a page whose native text genuinely
  fails.
- Implementations MUST be a local structural text-layer scan — no network, no
  model downloads, no model loads. The classifier only produces signal data; it
  is never a decision authority (eligibility stays code-owned).
- Facts page numbers are IR 1-based by contract; the provider normalizes
  upstream indexing once, nothing downstream converts.
- :func:`resolve_classifier` maps ``None`` to ``None`` — there is NO default
  implementation, unlike the judge's ``DeterministicJudge``: absence of a
  classifier IS today's behaviour (the rule table).

Provider modules live in ``parsecraft.providers.<provider>`` and export
``load_classifier(spec) -> PageOcrClassifier``; they import only at resolve time
via ``import_module`` (never at module import, never inline).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field, ValidationError, field_validator

from parsecraft.backends.protocol import SourceDocument
from parsecraft.routing.models import RoutingError

#: Diagnostic code the fold emits to record classifier provenance (ADR-0004 A5).
CLASSIFIER_PROVENANCE_CODE = "classifier-provenance"

#: Lazy default module path for a provider's loader (export: ``load_classifier``).
DEFAULT_PROVIDER_MODULE = "parsecraft.providers.{provider}"
_LOADER_EXPORT = "load_classifier"


class OcrFacts(BaseModel):
    """Per-page OCR-need facts a classifier produces (planner-input data).

    Page numbers are IR 1-based **by contract**: the provider normalizes
    upstream indexing once, nothing downstream converts. A page listed here can
    only gain OCR-need — the fold never clears text-statistics OCR-need.
    """

    pages_needing_ocr: frozenset[int] = Field(default_factory=frozenset[int])
    pages_with_tables: frozenset[int] = Field(default_factory=frozenset[int])
    pdf_type: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    #: Provenance: classifier name AND upstream version (e.g. "pdf-inspector 1.25.2").
    source: str = Field(min_length=1)

    model_config = {"frozen": True}

    @field_validator("pages_needing_ocr", "pages_with_tables")
    @classmethod
    def _page_numbers_are_ir_based(cls, pages: frozenset[int]) -> frozenset[int]:
        if pages and min(pages) < 1:
            msg = f"page numbers must be IR 1-based (>= 1), got {sorted(pages)}"
            raise ValueError(msg)
        return pages


@runtime_checkable
class PageOcrClassifier(Protocol):
    """Derives per-page OCR-need facts from a source document (local-only).

    Implementations are structural text-layer scans: no network, no model
    downloads, no model loads (ADR-0004 A4). Raise :class:`ClassifierError`
    (or a subclass) when classification cannot complete, so the analysis
    boundary falls back to the rule table (ADR-0004 A3).
    """

    def classify(self, source: SourceDocument) -> OcrFacts:
        """Return IR-1-based OCR-need facts for ``source``."""
        ...


class ClassifierSpec(BaseModel):
    """Parsed ``provider/model[:variant]`` classifier-spec string (config input)."""

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model: str = Field(min_length=1)
    variant: str | None = Field(default=None, min_length=1)


#: What a registered classifier provider receives and must return.
ClassifierProviderLoader = Callable[[ClassifierSpec], PageOcrClassifier]


#: Runtime-registered providers; explicit registration beats the lazy path.
_PROVIDERS: dict[str, ClassifierProviderLoader] = {}


class ClassifierError(RoutingError):
    """Base class for classifier resolution and classification failures."""


class ClassifierSpecError(ClassifierError):
    """The classifier spec string is malformed or unsupported."""


class ClassifierProviderUnavailableError(ClassifierError):
    """No loader is registered and the provider module cannot be loaded."""

    def __init__(self, provider: str, module_name: str, hint: str) -> None:
        self.provider = provider
        self.module_name = module_name
        self.hint = hint
        super().__init__(f"classifier provider {provider!r} unavailable: {hint}")


class ClassifierProviderLoadError(ClassifierError):
    """The provider loader failed or returned something that is not a classifier."""

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"classifier provider {provider!r} failed: {detail}")


def register_classifier_provider(name: str, loader: ClassifierProviderLoader) -> None:
    """Register (or replace) the loader for a provider name.

    Last registration wins — re-registering enables overrides and test
    isolation. An explicit registration always beats the lazy module path.
    """
    if not name or "/" in name:
        raise ClassifierSpecError(f"provider name must be non-empty and slash-free, got {name!r}")
    if not callable(loader):
        raise ClassifierError(f"loader for provider {name!r} must be callable")
    _PROVIDERS[name] = loader


def resolve_classifier(spec: str | PageOcrClassifier | None) -> PageOcrClassifier | None:
    """Turn a config/CLI classifier spec into an instance.

    ``None`` → ``None`` (no default implementation — absence is today's
    behaviour); a classifier instance passes through; a string is parsed and
    dispatched to its provider (runtime registry first, then the lazy module
    path). The classifier only supplies data — ``plan_route`` never trusts its
    facts to widen eligibility.
    """
    if spec is None:
        return None
    if isinstance(spec, PageOcrClassifier):
        return spec
    if not isinstance(spec, str):
        raise ClassifierSpecError(f"unsupported classifier spec type: {type(spec).__name__}")
    parsed = parse_classifier_spec(spec)
    loader = _PROVIDERS.get(parsed.provider)
    if loader is None:
        loader = _lazy_loader(parsed.provider)
    try:
        classifier = loader(parsed)
    except ClassifierError:
        raise
    except Exception as exc:
        raise ClassifierProviderLoadError(parsed.provider, str(exc)) from exc
    if not isinstance(classifier, PageOcrClassifier):
        raise ClassifierProviderLoadError(parsed.provider, f"loader returned {type(classifier).__name__}, not a PageOcrClassifier")
    return classifier


def parse_classifier_spec(spec: str) -> ClassifierSpec:
    """Parse ``provider/model[:variant]`` into a typed :class:`ClassifierSpec`."""
    text = spec.strip()
    provider, separator, rest = text.partition("/")
    if not separator:
        raise ClassifierSpecError(f"classifier spec {spec!r} must look like 'provider/model[:variant]'")
    model, colon, variant = rest.partition(":")
    try:
        return ClassifierSpec(provider=provider, model=model, variant=variant if colon else None)
    except ValidationError as exc:
        raise ClassifierSpecError(f"invalid classifier spec {spec!r}: {exc}") from exc


def _lazy_loader(provider: str) -> ClassifierProviderLoader:
    module_name = DEFAULT_PROVIDER_MODULE.format(provider=provider)
    try:
        module = import_module(module_name)
    except ImportError as exc:
        hint = (
            f"no loader registered and {module_name!r} could not be imported — "
            f"install the {provider!r} extra or call register_classifier_provider({provider!r}, loader)"
        )
        raise ClassifierProviderUnavailableError(provider, module_name, hint) from exc
    loader = getattr(module, _LOADER_EXPORT, None)
    if not callable(loader):
        raise ClassifierProviderUnavailableError(provider, module_name, f"{module_name} does not export {_LOADER_EXPORT}(spec)")
    return cast(ClassifierProviderLoader, loader)
