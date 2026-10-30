"""Offline tests for the per-file text-kind classification seam."""

from __future__ import annotations

import hashlib
import importlib
import sys
import types
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from parsecraft.adapters.classification import (
    DETECTED_KIND_KEY,
    TEXT_KIND_CODE,
    DecisionModelTextClassifier,
    TextClassificationError,
    TextClassifierProviderLoadError,
    TextClassifierProviderUnavailableError,
    TextClassifierSpec,
    TextClassifierSpecError,
    TextKind,
    TextKindClassifier,
    _configured_spec,
    classify_text_kind,
    parse_ambiguous_text,
    parse_text_classifier_spec,
    register_text_classifier_provider,
    resolve_text_classifier,
)
from parsecraft.adapters.classification._systemone import SystemOneDecisionModel
from parsecraft.adapters.markdown import parse_markdown
from parsecraft.cli import config as cli_config
from parsecraft.routing.judge_providers import JudgeError


class _StubClassifier:
    """Records texts and returns a canned verdict (counts prove one call per file)."""

    def __init__(self, verdict: tuple[TextKind, float]) -> None:
        self._verdict = verdict
        self.texts: list[str] = []

    def classify_text(self, text: str) -> tuple[TextKind, float]:
        self.texts.append(text)
        return self._verdict


class _RaisingClassifier:
    """Raises the given error from ``classify_text`` (typed or bug)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def classify_text(self, text: str) -> tuple[TextKind, float]:
        raise self._error


class _StubDecisionModel:
    """Records texts and returns a canned raw answer."""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.texts: list[str] = []

    def decide(self, text: str) -> str:
        self.texts.append(text)
        return self._answer


class _RaisingDecisionModel:
    """Raises the given error from ``decide``."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def decide(self, text: str) -> str:
        raise self._error


class _LazyProviderModule(types.ModuleType):
    """A provider module exposing the text-classifier loader."""

    def __init__(self, classifier: TextKindClassifier) -> None:
        super().__init__("parsecraft.providers.lazy_provider")
        self._classifier = classifier

    def load_text_classifier(self, spec: TextClassifierSpec) -> TextKindClassifier:
        return self._classifier


# ── _systemone ─────────────────────────────────────────────────────────────


class _FakeAnswer:
    """The Choice answer surface the model reads (choice unused by the impl)."""

    def __init__(self, probabilities: Mapping[str, Any]) -> None:
        self.choice = next(iter(probabilities))
        self.probabilities = dict(probabilities)
        self.confidence = 0.9


class _FakeSdkModule(types.ModuleType):
    """A fake typesafe_sdk: records calls, answers from canned probabilities."""

    def __init__(
        self,
        probabilities: Mapping[str, Any] | None,
        *,
        raise_on_call: Exception | None = None,
    ) -> None:
        super().__init__("typesafe_sdk")
        self._probabilities = probabilities
        self._raise_on_call = raise_on_call
        self.clients: list[dict[str, Any]] = []
        self.choices_made: list[dict[str, Any]] = []
        self.states: list[dict[str, object]] = []

    def TypeSafeClient(self, *, api_key: str, model: str, base_url: str | None = None) -> _FakeClient:
        self.clients.append({"api_key": api_key, "model": model, "base_url": base_url})
        return _FakeClient(self)

    def Choice(self, *, instructions: str, criteria: Mapping[str, object]) -> object:
        self.choices_made.append({"instructions": instructions, "criteria": criteria})
        return object()


class _FakeClient:
    """A context-managed fake client returning the canned distribution."""

    def __init__(self, module: _FakeSdkModule) -> None:
        self._module = module

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def system_one(self, state: Mapping[str, object], questions: Mapping[str, object]) -> Any:
        self._module.states.append(dict(state))
        if self._module._raise_on_call is not None:
            raise self._module._raise_on_call
        if self._module._probabilities is None:
            return types.SimpleNamespace(choices={})
        return types.SimpleNamespace(choices={"text-kind": _FakeAnswer(self._module._probabilities)})


class _EndpointsModule(types.ModuleType):
    """A fake providers._endpoints exposing select_text_classifier_spec."""

    def __init__(self, func: Callable[[], str | None]) -> None:
        super().__init__("parsecraft.providers._endpoints")
        self.select_text_classifier_spec = func


# ── TextKind ───────────────────────────────────────────────────────────────


def test_text_kind_values_are_the_wire_spelling() -> None:
    assert [kind.value for kind in TextKind] == ["markdown-like", "data-like", "plain"]


# ── spec parse ─────────────────────────────────────────────────────────────


def test_parse_text_classifier_spec_slash_and_colon() -> None:
    assert parse_text_classifier_spec("stub-provider/stub-model") == _spec()
    assert parse_text_classifier_spec("stub-provider:stub-model") == _spec()


def test_parse_text_classifier_spec_rejects_separatorless() -> None:
    with pytest.raises(TextClassifierSpecError):
        parse_text_classifier_spec("nosuchthing")


def test_parse_text_classifier_spec_rejects_bad_provider() -> None:
    with pytest.raises(TextClassifierSpecError):
        parse_text_classifier_spec("Bad Provider/model")


def test_parse_text_classifier_spec_rejects_empty_model() -> None:
    with pytest.raises(TextClassifierSpecError):
        parse_text_classifier_spec("stub-provider/")


# ── register ───────────────────────────────────────────────────────────────


def test_register_rejects_empty_slash_colon_names() -> None:
    def _loader(spec: TextClassifierSpec) -> TextKindClassifier:
        raise AssertionError("unreachable")

    for bad in ("", "has/slash", "has:colon"):
        with pytest.raises(TextClassifierSpecError):
            register_text_classifier_provider(bad, _loader)


def test_register_rejects_non_callable_loader() -> None:
    loader: Any = "not-a-loader"
    with pytest.raises(TextClassificationError):
        register_text_classifier_provider("stub-provider", loader)


# ── resolve ────────────────────────────────────────────────────────────────


def test_resolve_passes_an_instance_through() -> None:
    stub = _StubClassifier((TextKind.PLAIN, 0.0))
    assert resolve_text_classifier(stub) is stub


def test_resolve_rejects_a_non_spec_type() -> None:
    spec: Any = 123
    with pytest.raises(TextClassifierSpecError):
        resolve_text_classifier(spec)


def test_resolve_none_without_a_configured_model_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PARSECRAFT_JUDGE_MODEL", raising=False)
    monkeypatch.setattr("parsecraft.adapters.classification._configured_spec", lambda: None)
    assert resolve_text_classifier(None) is None


def test_resolve_none_uses_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClassifier((TextKind.PLAIN, 0.0))
    monkeypatch.setattr("parsecraft.adapters.classification._configured_spec", lambda: "reg-provider/reg-model")
    monkeypatch.setattr(
        "parsecraft.adapters.classification._PROVIDERS",
        {"reg-provider": lambda spec: stub},
    )
    assert resolve_text_classifier(None) is stub


def test_resolve_configured_broken_provider_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.adapters.classification._configured_spec", lambda: "gone-provider/gone-model")
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier(None)


def test_resolve_configured_error_becomes_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:

    def _boom() -> str | None:
        raise JudgeError("broken config file")

    monkeypatch.setitem(sys.modules, "parsecraft.providers._endpoints", _EndpointsModule(_boom))
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier(None)


def test_resolve_configured_missing_export_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "parsecraft.providers._endpoints", types.ModuleType("parsecraft.providers._endpoints"))
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier(None)


def test_configured_spec_reads_only_the_text_classifier_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pc-9p6.5 acceptance 5: a routing ``[judge]`` model alone resolves nothing."""
    config_file = tmp_path / "global" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('[judge]\nmodel = "typesafe-ai/jev-latest"\n', encoding="utf-8")
    monkeypatch.setattr(cli_config, "global_config_path", lambda: config_file)
    monkeypatch.delenv("PARSECRAFT_JUDGE_MODEL", raising=False)
    assert _configured_spec() is None
    assert resolve_text_classifier(None) is None
    config_file.write_text(
        '[judge]\nmodel = "typesafe-ai/jev-latest"\n[text_classifier]\nmodel = "stub-provider/stub-model"\n',
        encoding="utf-8",
    )
    assert _configured_spec() == "stub-provider/stub-model"


def test_resolve_registry_beats_the_lazy_path() -> None:
    stub = _StubClassifier((TextKind.MARKDOWN_LIKE, 1.0))
    register_text_classifier_provider("reg-beats-lazy", lambda spec: stub)
    assert resolve_text_classifier("reg-beats-lazy/anything") is stub


def test_resolve_unknown_provider_is_unavailable() -> None:
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier("no-such-provider-xyz/model")


def test_resolve_module_without_export_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("parsecraft.providers.empty_provider")
    monkeypatch.setitem(sys.modules, "parsecraft.providers.empty_provider", module)
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier("empty-provider/model")


def test_resolve_loader_failure_is_a_load_error() -> None:
    def _boom(spec: TextClassifierSpec) -> TextKindClassifier:
        raise RuntimeError("provider bug")

    register_text_classifier_provider("load-boom", _boom)
    with pytest.raises(TextClassifierProviderLoadError):
        resolve_text_classifier("load-boom/model")


def test_resolve_loader_typed_error_passes_through() -> None:
    def _typed(spec: TextClassifierSpec) -> TextKindClassifier:
        raise TextClassifierProviderUnavailableError("load-boom-typed", "m", "gone")

    register_text_classifier_provider("load-boom-typed", _typed)
    with pytest.raises(TextClassifierProviderUnavailableError):
        resolve_text_classifier("load-boom-typed/model")


def test_resolve_loader_wrong_type_is_a_load_error() -> None:
    def _wrong(spec: TextClassifierSpec) -> Any:
        return "not-a-classifier"

    register_text_classifier_provider("load-wrong-type", _wrong)
    with pytest.raises(TextClassifierProviderLoadError):
        resolve_text_classifier("load-wrong-type/model")


def test_resolve_lazy_module_export_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClassifier((TextKind.DATA_LIKE, 1.0))
    monkeypatch.setitem(sys.modules, "parsecraft.providers.lazy_provider", _LazyProviderModule(stub))
    assert resolve_text_classifier("lazy-provider/model") is stub


# ── DecisionModelTextClassifier ────────────────────────────────────────────


def test_decision_model_answer_maps_to_the_closed_set() -> None:
    classifier = DecisionModelTextClassifier(_StubDecisionModel("markdown-like"))
    assert classifier.classify_text("text") == (TextKind.MARKDOWN_LIKE, 1.0)


def test_decision_model_markdown_with_table_keeps_markdown() -> None:
    """Descriptive words must not decide: only canonical labels count (pc-2)."""
    classifier = DecisionModelTextClassifier(_StubDecisionModel("markdown-like, contains a table"))
    assert classifier.classify_text("text") == (TextKind.MARKDOWN_LIKE, 1.0)


def test_decision_model_bare_csv_degrades_to_plain() -> None:
    """A non-canonical answer carries no verdict — safe default, not a guess."""
    classifier = DecisionModelTextClassifier(_StubDecisionModel("csv"))
    assert classifier.classify_text("text") == (TextKind.PLAIN, 0.0)


def test_decision_model_multi_label_answer_degrades_to_plain() -> None:
    classifier = DecisionModelTextClassifier(_StubDecisionModel("markdown-like or data-like"))
    assert classifier.classify_text("text") == (TextKind.PLAIN, 0.0)


def test_decision_model_typed_failure_passes_through() -> None:
    classifier = DecisionModelTextClassifier(_RaisingDecisionModel(TextClassificationError("down")))
    with pytest.raises(TextClassificationError):
        classifier.classify_text("text")


def test_decision_model_bug_is_wrapped_typed() -> None:
    classifier = DecisionModelTextClassifier(_RaisingDecisionModel(ValueError("bug")))
    with pytest.raises(TextClassificationError, match="decision model failed"):
        classifier.classify_text("text")


# ── classify_text_kind ─────────────────────────────────────────────────────


def test_blank_input_costs_zero_calls_and_is_plain() -> None:
    stub = _StubClassifier((TextKind.MARKDOWN_LIKE, 1.0))
    assert classify_text_kind("   \n  ", classifier=stub) == (TextKind.PLAIN, 0.0)
    assert stub.texts == []


def test_exactly_one_call_per_file() -> None:
    stub = _StubClassifier((TextKind.DATA_LIKE, 1.0))
    kind, confidence = classify_text_kind("a\n\nb\n\nc\nd\ne\nf", classifier=stub)
    assert (kind, confidence) == (TextKind.DATA_LIKE, 1.0)
    assert len(stub.texts) == 1


def test_unresolvable_spec_degrades_to_plain() -> None:
    assert classify_text_kind("text", classifier="no-such-provider-xyz/model") == (TextKind.PLAIN, 0.0)


def test_classifier_failure_degrades_to_plain() -> None:
    assert classify_text_kind("text", classifier=_RaisingClassifier(TextClassificationError("down"))) == (TextKind.PLAIN, 0.0)


def test_classifier_bug_degrades_to_plain_not_out() -> None:
    """The pipeline never sees an exception: even a buggy classifier degrades."""
    assert classify_text_kind("text", classifier=_RaisingClassifier(ValueError("bug"))) == (TextKind.PLAIN, 0.0)


def test_no_classifier_and_no_configured_model_is_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.adapters.classification._configured_spec", lambda: None)
    assert classify_text_kind("text", classifier=None) == (TextKind.PLAIN, 0.0)


def test_same_bytes_yield_the_same_verdict() -> None:
    stub = _StubClassifier((TextKind.MARKDOWN_LIKE, 1.0))
    first = classify_text_kind("# Title\n\nbody", classifier=stub)
    second = classify_text_kind("# Title\n\nbody", classifier=stub)
    assert first == second == (TextKind.MARKDOWN_LIKE, 1.0)


# ── parse_ambiguous_text ───────────────────────────────────────────────────


def test_markdown_like_matches_parse_markdown_plus_stamps() -> None:
    """Merge-proof: the markdown path is exactly parse_markdown output, stamped."""
    text = "# Title\n\nA paragraph.\n"
    expected = parse_markdown(text, "mem://x")
    result = parse_ambiguous_text(text, "mem://x", classifier=_StubClassifier((TextKind.MARKDOWN_LIKE, 1.0)))

    assert [block.content for block in result.pages[0].blocks] == [block.content for block in expected.pages[0].blocks]
    assert [block.kind for block in result.pages[0].blocks] == [block.kind for block in expected.pages[0].blocks]
    for chunk in result.pages[0].blocks:
        assert chunk.metadata[DETECTED_KIND_KEY] == TextKind.MARKDOWN_LIKE.value
        assert chunk.confidence == 1.0
    assert any(diagnostic.code == TEXT_KIND_CODE for diagnostic in result.pages[0].diagnostics)


def test_data_like_routes_to_the_real_csv_table() -> None:
    """DATA_LIKE goes through the native-csv parser: TABLE IR, source envelope."""
    stub = _StubClassifier((TextKind.DATA_LIKE, 1.0))
    result = parse_ambiguous_text("a,b\n1,2\n", "file:///notes.txt", classifier=stub)

    assert stub.texts == ["a,b\n1,2\n"]
    assert len(result.pages) == 1
    assert len(result.pages[0].blocks) == 1
    chunk = result.pages[0].blocks[0]
    assert chunk.kind.name == "TABLE"
    assert chunk.id == "native-csv-1-b0"
    assert chunk.rows == (("a", "b"), ("1", "2"))
    assert chunk.content == "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert chunk.metadata["backend"] == "native-csv"
    assert chunk.metadata["delimiter"] == ","
    assert chunk.metadata[DETECTED_KIND_KEY] == TextKind.DATA_LIKE.value
    assert chunk.confidence == 1.0
    assert result.metadata.format == "text/plain"
    assert result.metadata.source_uri == "file:///notes.txt"
    assert any(diagnostic.code == TEXT_KIND_CODE and "parser: csv" in diagnostic.message for diagnostic in result.pages[0].diagnostics)


def test_data_like_sniffs_the_delimiter() -> None:
    """The csv backend's own sniff decides — semicolons stay one row, not one column."""
    result = parse_ambiguous_text("a;b\n1;2\n", "mem://x", classifier=_StubClassifier((TextKind.DATA_LIKE, 1.0)))

    chunk = result.pages[0].blocks[0]
    assert chunk.kind.name == "TABLE"
    assert chunk.rows == (("a", "b"), ("1", "2"))
    assert chunk.metadata["delimiter"] == ";"
    assert chunk.metadata[DETECTED_KIND_KEY] == TextKind.DATA_LIKE.value


def test_data_like_sniff_fallback_parses_comma() -> None:
    """No consistent delimiter: the csv backend's comma fallback + INFO, still TABLE."""
    result = parse_ambiguous_text("alpha\nbeta\n", "mem://x", classifier=_StubClassifier((TextKind.DATA_LIKE, 1.0)))

    chunk = result.pages[0].blocks[0]
    assert chunk.kind.name == "TABLE"
    assert chunk.rows == (("alpha",), ("beta",))
    assert chunk.metadata["delimiter"] == ","
    assert any(diagnostic.code == "csv-sniff-fallback" for diagnostic in result.pages[0].diagnostics)


def test_data_like_csv_failure_falls_back_to_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A csv-path failure parses plain, never raises — the verdict is still stamped."""

    def _boom(source: object, data: bytes) -> list[object]:
        raise RuntimeError("csv down")

    monkeypatch.setattr("parsecraft.backends.native.csv.extract_csv_pages", _boom)
    result = parse_ambiguous_text("a,b\n1,2\n", "mem://x", classifier=_StubClassifier((TextKind.DATA_LIKE, 0.8)))

    assert all(block.kind.name == "PARAGRAPH" for block in result.pages[0].blocks)
    for chunk in result.pages[0].blocks:
        assert chunk.metadata[DETECTED_KIND_KEY] == TextKind.DATA_LIKE.value
        assert chunk.confidence == 0.8
    assert any(diagnostic.code == TEXT_KIND_CODE and "parser: plain" in diagnostic.message for diagnostic in result.pages[0].diagnostics)
    assert any(diagnostic.code == TEXT_KIND_CODE and "csv parse failed (RuntimeError)" in diagnostic.message for diagnostic in result.pages[0].diagnostics)


def test_data_like_keeps_source_identity() -> None:
    """Envelope agrees with the plain path: hash over bytes, source format, forwarded fields."""
    produced = datetime(2026, 10, 30, tzinfo=UTC)
    text = "a,b\n1,2\n"
    result = parse_ambiguous_text(
        text,
        "file:///notes.txt",
        classifier=_StubClassifier((TextKind.DATA_LIKE, 1.0)),
        title="notes",
        package_version="9.9.9",
        produced_at=produced,
    )

    assert result.metadata.source_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert result.metadata.format == "text/plain"
    assert result.metadata.title == "notes"
    assert result.metadata.package_version == "9.9.9"
    assert result.metadata.produced_at == produced


def test_markdown_branch_keeps_the_source_format() -> None:
    """Format describes the SOURCE (.txt/unknown → text/plain), never the parser.

    The verdict lives in detected_kind, so identical bytes never key
    differently across runs (pc-2: consumer parse-cache identity).
    """
    result = parse_ambiguous_text("# Title\n\nA paragraph.\n", "file:///note.txt", classifier=_StubClassifier((TextKind.MARKDOWN_LIKE, 1.0)))

    assert result.metadata.format == "text/plain"
    assert result.pages[0].blocks[0].metadata[DETECTED_KIND_KEY] == TextKind.MARKDOWN_LIKE.value


def test_plain_path_keeps_native_text_chunk_ids() -> None:
    """Chunk identity must not change depending on whether a classifier ran (pc-2)."""
    result = parse_ambiguous_text("first\n\nsecond\n", "mem://x", classifier=_StubClassifier((TextKind.PLAIN, 0.0)))

    assert [block.id for block in result.pages[0].blocks] == ["native-text-1-b0", "native-text-1-b1"]


def test_failure_yields_the_plain_path_not_an_exception() -> None:
    result = parse_ambiguous_text("just text", "mem://x", classifier=_RaisingClassifier(TextClassificationError("down")))

    assert all(block.kind.name == "PARAGRAPH" for block in result.pages[0].blocks)
    for chunk in result.pages[0].blocks:
        assert chunk.metadata[DETECTED_KIND_KEY] == TextKind.PLAIN.value
        assert chunk.confidence == 0.0


def test_blank_input_yields_an_empty_plain_page() -> None:
    stub = _StubClassifier((TextKind.MARKDOWN_LIKE, 1.0))
    result = parse_ambiguous_text("  ", "mem://x", classifier=stub)

    assert result.pages[0].blocks == []
    assert stub.texts == []


def test_produced_at_reaches_both_parsers() -> None:
    """Regression (pc-2 review): the stamp path must not drop produced_at."""
    produced = datetime(2026, 1, 1, tzinfo=UTC)
    markdown_result = parse_ambiguous_text("# T\n", "mem://x", classifier=_StubClassifier((TextKind.MARKDOWN_LIKE, 1.0)), produced_at=produced)
    plain_result = parse_ambiguous_text("text", "mem://x", classifier=_StubClassifier((TextKind.PLAIN, 0.0)), produced_at=produced)
    assert markdown_result.metadata.produced_at == produced
    assert plain_result.metadata.produced_at == produced


def test_systemone_unique_maximum_returns_the_label(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule({"markdown-like": 0.7, "data-like": 0.2, "plain": 0.1})
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec("typesafe-ai", "jev-1.13"), api_key="key")

    assert model.decide("# Title") == "markdown-like"
    assert module.clients == [{"api_key": "key", "model": "jev-1.13", "base_url": None}]
    assert module.states[0]["text"] == "# Title"


def test_systemone_shared_maximum_raises_not_a_joined_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tie is undecided: typed failure (degrades to PLAIN), never a joined label."""
    module = _FakeSdkModule({"markdown-like": 0.5, "data-like": 0.5, "plain": 0.0})
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="tied between"):
        model.decide("text")


def test_systemone_non_numeric_probability_counts_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule({"markdown-like": "high", "data-like": 0.2, "plain": 0.0})
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    assert model.decide("text") == "data-like"


def test_systemone_boolean_probability_counts_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule({"markdown-like": True, "data-like": 0.2, "plain": 0.0})
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    assert model.decide("text") == "data-like"


def test_systemone_missing_choice_raises_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule(None)
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="no choice"):
        model.decide("text")


def test_systemone_sdk_failure_is_wrapped_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule({"plain": 1.0}, raise_on_call=RuntimeError("transport down"))
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="system one call failed"):
        model.decide("text")


def test_systemone_typed_sdk_failure_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _FakeSdkModule({"plain": 1.0}, raise_on_call=TextClassificationError("already typed"))
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="already typed"):
        model.decide("text")


def test_systemone_missing_sdk_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="is not installed"):
        model.decide("text")


def test_systemone_missing_exports_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", types.ModuleType("typesafe_sdk"))
    model = SystemOneDecisionModel(_spec(), api_key="key")

    with pytest.raises(TextClassificationError, match="does not export"):
        model.decide("text")


def test_systemone_end_to_end_decides_and_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped path against a fake SDK: one call, closed verdict."""
    module = _FakeSdkModule({"data-like": 0.8, "markdown-like": 0.1, "plain": 0.1})
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    classifier = DecisionModelTextClassifier(SystemOneDecisionModel(_spec(), api_key="key"))

    assert classifier.classify_text("a,b\n1,2") == (TextKind.DATA_LIKE, 1.0)


def _spec(provider: str = "stub-provider", model: str = "stub-model") -> TextClassifierSpec:
    return TextClassifierSpec(provider=provider, model=model)


def test_importing_the_seam_needs_no_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline gate in miniature: importing the seam never imports the SDK."""
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    for name in ("parsecraft.adapters.classification", "parsecraft.adapters.classification._systemone"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.import_module("parsecraft.adapters.classification")
    importlib.import_module("parsecraft.adapters.classification._systemone")
