"""Per-file text-kind classification for ambiguous text blobs.

An ambiguous blob (``.txt``/unknown — extension says nothing) gets exactly ONE
decision-model call per FILE (never per page, never per chunk) whose verdict
selects the structure parser. The closed verdict set is :class:`TextKind`;
anything the model cannot decide — and any failure at all — degrades to
:data:`TextKind.PLAIN`, which always works (constraint: misclassification may
only degrade structure quality, never block ingestion).

Cost rule: the FULL text reaches the model, untruncated — truncation would bias
the verdict toward the file head, and determinism requires the verdict to be a
pure function of the whole bytes. No sampling: the verdict mapping is a pure
function of the model's answer, ties resolve to PLAIN, and identical bytes
with an identical model answer yield the identical verdict every run.

DATA_LIKE routes to the real csv parser (:func:`extract_csv_pages` from the
native-csv backend — stdlib csv, sniffed delimiter, TABLE IR), preserving the
verdict in chunk metadata + diagnostic. The csv path never blocks: a parse
failure falls back to plain, and an empty grid yields the csv backend's own
no-chunk page.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from parsecraft.adapters.errors import AdapterError
from parsecraft.adapters.markdown import parse_markdown
from parsecraft.backends.native._common import paragraph_chunks
from parsecraft.backends.protocol import SourceDocument
from parsecraft.ir.models import (
    Diagnostic,
    DiagnosticLevel,
    DocumentMetadata,
    DocumentResult,
    PageResult,
)
from parsecraft.routing.judge_providers import JudgeError

#: Diagnostic code marking the pages this module classified (human debugging;
#: machine consumers read ``detected_kind`` chunk metadata instead).
TEXT_KIND_CODE = "text-kind"

#: Chunk metadata key carrying the verdict (pc-2 consumer contract).
DETECTED_KIND_KEY = "detected_kind"

#: Parser names recorded in the verdict diagnostic.
_MARKDOWN_PARSER = "markdown"
_CSV_PARSER = "csv"
_PLAIN_PARSER = "plain"

#: Lazy default module path for a provider's loader (export: ``load_text_classifier``).
DEFAULT_PROVIDER_MODULE = "parsecraft.providers.{provider}"
_LOADER_EXPORT = "load_text_classifier"


class TextKind(StrEnum):
    """Closed verdict set for one file's text shape."""

    MARKDOWN_LIKE = "markdown-like"
    DATA_LIKE = "data-like"
    PLAIN = "plain"


#: Match vocabulary per kind (word-boundary, lowercase). Ambiguity degrades to
#: PLAIN, so a hint that fires inside another word must not count — hence the
#: boundaries (``metadata`` must not read as ``data``).
_KIND_HINTS: tuple[tuple[TextKind, tuple[str, ...]], ...] = (
    (TextKind.MARKDOWN_LIKE, ("markdown-like", "markdown")),
    (TextKind.DATA_LIKE, ("data-like",)),
    (TextKind.PLAIN, ("plain",)),
)


class TextClassificationError(AdapterError):
    """Base class for text-kind resolution and classification failures."""


class TextClassifierSpecError(TextClassificationError):
    """The text-classifier spec string is malformed or unsupported."""


class TextClassifierProviderUnavailableError(TextClassificationError):
    """No loader is registered and the provider module cannot be loaded."""

    def __init__(self, provider: str, module_name: str, hint: str) -> None:
        self.provider = provider
        self.module_name = module_name
        self.hint = hint
        super().__init__(f"text classifier provider {provider!r} unavailable: {hint}")


class TextClassifierProviderLoadError(TextClassificationError):
    """The provider loader failed or returned something that is not a classifier."""

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"text classifier provider {provider!r} failed: {detail}")


@runtime_checkable
class TextKindClassifier(Protocol):
    """One verdict per FILE for an ambiguous text blob.

    Implementations call the decision model at most once per
    :meth:`classify_text` call and return the verdict with its confidence in
    ``[0.0, 1.0]`` (conservative: ``0.0`` = no signal, degrade-safe).
    Raise :class:`TextClassificationError` (or a subclass) when classification
    cannot complete — the entry point degrades to PLAIN, never out.
    """

    def classify_text(self, text: str) -> tuple[TextKind, float]:
        """Return the file's kind and the verdict confidence."""
        ...


@runtime_checkable
class TextKindDecisionModel(Protocol):
    """The raw decision-model ask behind a classifier: one call, one answer.

    The answer is free text naming the kind; the classifier validates it into
    the closed :class:`TextKind` set. Raise :class:`TextClassificationError`
    when the model cannot be reached or answers off-schema.
    """

    def decide(self, text: str) -> str:
        """Ask the model once for ``text`` and return its raw verdict."""
        ...


class DecisionModelTextClassifier:
    """A classifier that validates one decision-model answer into :class:`TextKind`."""

    def __init__(self, model: TextKindDecisionModel) -> None:
        self._model = model

    def classify_text(self, text: str) -> tuple[TextKind, float]:
        """One model call, mapped to the closed verdict set (ties degrade to PLAIN)."""
        try:
            answer = self._model.decide(text)
        except TextClassificationError:
            raise
        except Exception as exc:
            raise TextClassificationError(f"decision model failed: {type(exc).__name__}: {exc}") from exc
        return _map_verdict(answer)


class TextClassifierSpec(BaseModel):
    """Parsed ``provider/model`` (or ``provider:model``) text-classifier spec."""

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model: str = Field(min_length=1)


#: What a registered text-classifier provider receives and must return.
TextClassifierLoader = Callable[[TextClassifierSpec], TextKindClassifier]

#: Runtime-registered providers; explicit registration beats the lazy path.
_PROVIDERS: dict[str, TextClassifierLoader] = {}


def register_text_classifier_provider(name: str, loader: TextClassifierLoader) -> None:
    """Register (or replace) the loader for a provider name.

    Last registration wins — re-registering enables overrides and test
    isolation. An explicit registration always beats the lazy module path.
    """
    if not name or "/" in name or ":" in name:
        raise TextClassifierSpecError(f"provider name must be non-empty and separator-free, got {name!r}")
    if not callable(loader):
        raise TextClassificationError(f"loader for provider {name!r} must be callable")
    _PROVIDERS[name] = loader


def parse_ambiguous_text(
    text: str,
    source_uri: str,
    *,
    classifier: str | TextKindClassifier | None = None,
    title: str | None = None,
    package_version: str = "0.0.0",
    produced_at: datetime | None = None,
) -> DocumentResult:
    """Parse an ambiguous blob: one classification, then the matching parser.

    MARKDOWN_LIKE goes through :func:`parse_markdown`; DATA_LIKE goes through
    the real csv parser (:func:`extract_csv_pages` — stdlib csv, TABLE IR);
    PLAIN goes through the plain paragraph path. ``title``,
    ``package_version`` and ``produced_at`` reach every parser, so all verdict
    paths agree on document identity. Every chunk gains
    ``detected_kind`` metadata and the ``confidence`` field, and every page a
    ``text-kind`` diagnostic. A classifier failure — or a csv-path failure —
    yields the plain path, not an exception.
    """
    kind, confidence = classify_text_kind(text, classifier=classifier)
    if kind is TextKind.MARKDOWN_LIKE:
        result = parse_markdown(text, source_uri, title=title, package_version=package_version, produced_at=produced_at)
        # format describes the SOURCE (.txt/unknown), not the chosen parser —
        # the verdict lives in detected_kind, so identical bytes never key
        # differently across runs (pc-2: consumer parse-cache identity).
        result = result.model_copy(update={"metadata": result.metadata.model_copy(update={"format": "text/plain"})})
        parser = _MARKDOWN_PARSER
    elif kind is TextKind.DATA_LIKE:
        try:
            result = _csv_document(text, source_uri, title=title, package_version=package_version, produced_at=produced_at)
            parser = _CSV_PARSER
        except Exception as exc:
            # Conservative fallback: the csv path never blocks ingestion — a
            # failure here parses plain, exactly like a classifier failure.
            # The failure gets its own diagnostic (type name only, never
            # content) so the degraded result is never silent: the executor
            # caches successful-with-warning documents, and this one carries
            # its explanation with it.
            result = _plain_document(text, source_uri, title=title, package_version=package_version, produced_at=produced_at)
            result.pages[0].diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code=TEXT_KIND_CODE,
                    message=f"csv parse failed ({type(exc).__name__}); plain fallback, no TABLE emitted",
                )
            )
            parser = _PLAIN_PARSER
    else:
        result = _plain_document(text, source_uri, title=title, package_version=package_version, produced_at=produced_at)
        parser = _PLAIN_PARSER
    _stamp(result.pages, kind, confidence, parser)
    return result


def classify_text_kind(text: str, *, classifier: str | TextKindClassifier | None = None) -> tuple[TextKind, float]:
    """Classify one file's text shape; never raises, degrades to ``(PLAIN, 0.0)``.

    Exactly one classifier call per file (zero for blank input, which carries
    no signal). Resolution failure, a missing model, and any classifier
    failure (typed or bug) all yield the plain fallback — the pipeline never
    sees an exception.
    Deterministic: the same bytes with the same configured answer yield the
    same verdict; ties and unparsable answers resolve to PLAIN.
    """
    if not text.strip():
        return (TextKind.PLAIN, 0.0)
    try:
        instance = resolve_text_classifier(classifier)
    except TextClassificationError:
        return (TextKind.PLAIN, 0.0)
    if instance is None:
        return (TextKind.PLAIN, 0.0)
    try:
        return instance.classify_text(text)
    except TextClassificationError:
        return (TextKind.PLAIN, 0.0)
    except Exception:
        # Pipeline boundary: even a buggy classifier degrades, never raises.
        return (TextKind.PLAIN, 0.0)


def resolve_text_classifier(spec: str | TextKindClassifier | None) -> TextKindClassifier | None:
    """Turn a config text-classifier spec into an instance.

    ``None`` resolves the configured text-classifier model (``[text_classifier]``
    section only — there is no CLI flag) and returns ``None`` when nothing is
    configured — absence is today's behaviour (plain). A classifier instance
    passes through; a string is parsed and dispatched to its provider
    (runtime registry first, then the lazy module path).
    """
    if spec is None:
        configured = _configured_spec()
        if configured is None:
            return None
        return resolve_text_classifier(configured)
    if isinstance(spec, TextKindClassifier):
        return spec
    if not isinstance(spec, str):
        raise TextClassifierSpecError(f"unsupported text-classifier spec type: {type(spec).__name__}")
    parsed = parse_text_classifier_spec(spec)
    loader = _PROVIDERS.get(parsed.provider)
    if loader is None:
        loader = _lazy_loader(parsed.provider)
    try:
        classifier = loader(parsed)
    except TextClassificationError:
        raise
    except Exception as exc:
        raise TextClassifierProviderLoadError(parsed.provider, str(exc)) from exc
    if not isinstance(classifier, TextKindClassifier):
        raise TextClassifierProviderLoadError(parsed.provider, f"loader returned {type(classifier).__name__}, not a TextKindClassifier")
    return classifier


def parse_text_classifier_spec(spec: str) -> TextClassifierSpec:
    """Parse ``provider/model`` (or ``provider:model``) into :class:`TextClassifierSpec`.

    Whichever separator comes first wins; the model token is everything after
    it, verbatim.
    """
    text = spec.strip()
    cuts = [pos for pos in (text.find("/"), text.find(":")) if pos >= 0]
    if not cuts:
        raise TextClassifierSpecError(f"text-classifier spec {spec!r} must look like 'provider/model' or 'provider:model'")
    cut = min(cuts)
    provider, model = text[:cut], text[cut + 1 :]
    try:
        return TextClassifierSpec(provider=provider, model=model)
    except ValidationError as exc:
        raise TextClassifierSpecError(f"invalid text-classifier spec {spec!r}: {exc}") from exc


def _configured_spec() -> str | None:
    """The configured text-classifier spec, or ``None`` when nothing configures one.

    Reads ``[text_classifier] model`` ONLY (pc-9p6.5): a routing judge's
    existence must never switch on document shaping, so the old fallback to
    the routing ``[judge]`` configuration is gone — absence now means plain
    by construction, not by accident.

    The provider-endpoint graph (``providers._endpoints`` pulls ``cli.config``)
    is reached here through a call-time :func:`import_module` seam — a
    function-local ``import`` statement would be hoisted back to module scope
    by the formatter, re-importing the CLI layer on every adapter import
    (pc-2: registry discovery imports every factory eagerly — a text adapter
    must not tax every PDF run).
    """
    module = import_module("parsecraft.providers._endpoints")
    select = getattr(module, "select_text_classifier_spec", None)
    if not callable(select):
        raise TextClassifierProviderUnavailableError("text_classifier", "config", "providers._endpoints does not export select_text_classifier_spec")
    try:
        return cast(Callable[[], str | None], select)()
    except JudgeError as exc:
        raise TextClassifierProviderUnavailableError("text_classifier", "config", f"cannot read text-classifier configuration: {exc}") from exc


def _lazy_loader(provider: str) -> TextClassifierLoader:
    # Provider tokens may be hyphenated; module names may not.
    module_name = DEFAULT_PROVIDER_MODULE.format(provider=provider.replace("-", "_"))
    try:
        module = import_module(module_name)
    except ImportError as exc:
        hint = (
            f"no loader registered and {module_name!r} could not be imported — "
            f"install the package that ships it or call register_text_classifier_provider({provider!r}, loader)"
        )
        raise TextClassifierProviderUnavailableError(provider, module_name, hint) from exc
    loader = getattr(module, _LOADER_EXPORT, None)
    if not callable(loader):
        raise TextClassifierProviderUnavailableError(provider, module_name, f"{module_name} does not export {_LOADER_EXPORT}(spec)")
    return cast(TextClassifierLoader, loader)


def _map_verdict(answer: str) -> tuple[TextKind, float]:
    """Validate a free-text answer into the closed verdict set.

    Only the canonical label tokens count — descriptive words (``table``,
    ``csv``, ``prose``) must not decide, or an answer like "markdown-like,
    contains a table" would read as two kinds and discard a correct markdown
    verdict. Exactly one kind named → that kind at ``1.0``; zero or several
    kinds named → ``(PLAIN, 0.0)``. The confidence is mapping decisiveness,
    not a calibrated model probability — ambiguity degrades to plain, never
    to a guessed structure.
    """
    lowered = answer.lower()
    hits = [kind for kind, hints in _KIND_HINTS if any(re.search(r"\b" + re.escape(hint) + r"\b", lowered) for hint in hints)]
    if len(hits) == 1:
        return (hits[0], 1.0)
    return (TextKind.PLAIN, 0.0)


def _csv_document(
    text: str,
    source_uri: str,
    *,
    title: str | None = None,
    package_version: str = "0.0.0",
    produced_at: datetime | None = None,
) -> DocumentResult:
    """DATA_LIKE through the real csv parser: TABLE IR, source envelope.

    The text is handed to the native-csv backend's own extract function with
    the source declared ``text/plain`` — extension routing already decided
    this blob is not ``.csv``, so the delimiter is sniffed, never forced.
    Chunk shape, ids (``native-csv-1-b0``), and csv diagnostics are the csv
    backend's own; document identity (source envelope) is this module's.
    The csv module is imported call-time (not at this module's import):
    importing it runs the csv backend's ``field_size_limit`` raise, which a
    text-only process must not pay for until a DATA_LIKE verdict needs it.
    """
    from_source = SourceDocument(uri=source_uri, media_type="text/plain")
    csv_module = import_module("parsecraft.backends.native.csv")
    pages = csv_module.extract_csv_pages(from_source, text.encode("utf-8"))
    metadata = _envelope(text, source_uri, len(pages), title=title, package_version=package_version, produced_at=produced_at)
    return DocumentResult(metadata=metadata, pages=pages)


def _plain_document(
    text: str,
    source_uri: str,
    *,
    title: str | None = None,
    package_version: str = "0.0.0",
    produced_at: datetime | None = None,
) -> DocumentResult:
    """One page of paragraph chunks split on blank lines (the native text shape)."""
    # The native-text shape, shared not copied: chunk ids stay native-text-* so
    # a .txt document reads identically with or without a classifier (pc-2).
    blocks = paragraph_chunks("native-text", 1, text)
    metadata = _envelope(text, source_uri, 1, title=title, package_version=package_version, produced_at=produced_at)
    return DocumentResult(metadata=metadata, pages=[PageResult(page_number=1, blocks=blocks)])


def _envelope(
    text: str,
    source_uri: str,
    page_count: int,
    *,
    title: str | None = None,
    package_version: str = "0.0.0",
    produced_at: datetime | None = None,
) -> DocumentMetadata:
    """Source-identity envelope shared by the plain and csv verdict paths.

    ``format`` is always the SOURCE format (``text/plain``) — never the chosen
    parser — so identical bytes never key differently across runs.
    """
    produced = produced_at if produced_at is not None else datetime.now(UTC)
    return DocumentMetadata(
        source_uri=source_uri,
        source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        format="text/plain",
        page_count=page_count,
        title=title,
        produced_at=produced,
        package_version=package_version,
    )


def _stamp(pages: list[PageResult], kind: TextKind, confidence: float, parser: str) -> None:
    """Stamp the verdict onto chunks and page diagnostics (post-step).

    The verdict lands on the first-class ``confidence`` field — never on a
    same-named metadata key, which would shadow it with a string copy.
    """
    for page in pages:
        for chunk in page.blocks:
            chunk.metadata[DETECTED_KIND_KEY] = kind.value
            chunk.confidence = confidence
        page.diagnostics.append(
            Diagnostic(
                level=DiagnosticLevel.INFO,
                code=TEXT_KIND_CODE,
                message=f"text-kind verdict: {kind.value} (confidence {confidence:.1f}); parser: {parser}",
            )
        )


__all__ = [
    "DEFAULT_PROVIDER_MODULE",
    "DETECTED_KIND_KEY",
    "TEXT_KIND_CODE",
    "DecisionModelTextClassifier",
    "TextClassificationError",
    "TextClassifierProviderLoadError",
    "TextClassifierProviderUnavailableError",
    "TextClassifierSpec",
    "TextClassifierSpecError",
    "TextKind",
    "TextKindClassifier",
    "TextKindDecisionModel",
    "classify_text_kind",
    "parse_ambiguous_text",
    "parse_text_classifier_spec",
    "register_text_classifier_provider",
    "resolve_text_classifier",
]
