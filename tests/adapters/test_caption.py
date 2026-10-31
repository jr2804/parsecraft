"""Caption attribution tests: real SDK objects, controlled transport bytes.

Every network-adjacent test builds a REAL ``TypeSafeClient`` (real ``Choice``
objects, real request serialization, real response parsing) and only controls
the HTTP bytes via ``httpx2.MockTransport``. A stubbed endpoint is therefore
never presented as model evidence: the one real-model check is the
``judge``-marked live test, which runs only with credentials and stays out of
the default gate. Validator edge cases that JSON cannot express (NaN,
infinity) are exercised directly against the pure validators with
attribute-shaped stand-ins — labelled as such.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib import import_module
from types import ModuleType, SimpleNamespace
from typing import NoReturn, Protocol, cast

import httpx2
import pytest

from parsecraft.adapters import caption as caption_mod
from parsecraft.adapters.caption import (
    CaptionFailure,
    CaptionReport,
    ChoiceAnswer,
    SystemOneClient,
    SystemOneResponse,
    attribute_captions,
    link_same_span_captions,
)
from parsecraft.ir.models import (
    ChunkKind,
    ChunkRelation,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    RelationKind,
    SourceSpan,
    StructuredChunk,
)

_PRODUCED_AT = datetime(2026, 10, 31, tzinfo=UTC)


class ServerModel(Protocol):
    """Test seam only: the server-reported model name on a live response."""

    @property
    def model(self) -> str: ...


def test_no_candidates_never_touches_config_or_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paragraph with no table/figure sibling is a pure no-op."""

    def boom(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("must not resolve anything without candidates")

    monkeypatch.setattr(caption_mod, "import_module", boom)
    report = attribute_captions(_doc(_chunk("p-0", ChunkKind.PARAGRAPH)))
    assert report == CaptionReport(attributed=0, abstained=0, failure=None)


def test_unconfigured_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No spec from argument, env, or file: failure, document untouched."""
    monkeypatch.delenv("PARSECRAFT_JUDGE_MODEL", raising=False)
    endpoints = caption_mod_endpoints()
    monkeypatch.setattr(endpoints, "select_judge_spec", lambda _cli: None)
    document = _pair_doc()
    before = document.model_dump()
    report = attribute_captions(document)
    assert report.failure is CaptionFailure.UNCONFIGURED
    assert (report.attributed, report.abstained) == (0, 0)
    assert document.model_dump() == before


def test_blank_model_falls_back_to_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only model behaves like no argument (no silent default)."""
    monkeypatch.delenv("PARSECRAFT_JUDGE_MODEL", raising=False)
    endpoints = caption_mod_endpoints()
    monkeypatch.setattr(endpoints, "select_judge_spec", lambda _cli: None)
    report = attribute_captions(_pair_doc(), model="   ")
    assert report.failure is CaptionFailure.UNCONFIGURED


def test_invalid_spec_reports_failure() -> None:
    """A malformed spec is a caller bug: failure before any resolution."""
    report = attribute_captions(_pair_doc(), model="nospec")
    assert report.failure is CaptionFailure.INVALID_SPEC


def test_unsupported_provider_reports_failure() -> None:
    """A well-formed but non-System-One provider is refused, never substituted."""
    report = attribute_captions(_pair_doc(), model="magic/jev-latest")
    assert report.failure is CaptionFailure.UNSUPPORTED_PROVIDER


def test_unavailable_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyed provider with no credential anywhere: unavailable, no call."""
    endpoints = caption_mod_endpoints()
    monkeypatch.setattr(
        endpoints,
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: (None, None),
    )
    report = attribute_captions(_pair_doc(), model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.UNAVAILABLE


def test_unavailable_when_provider_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A supported token whose module cannot import is a broken install."""
    monkeypatch.setattr(caption_mod, "CAPTION_PROVIDERS", ("typesafe-ai", "ghost"))
    report = attribute_captions(_pair_doc(), model="ghost/model")
    assert report.failure is CaptionFailure.UNAVAILABLE


def test_unsupported_provider_rejects_local_endpoint() -> None:
    """The local endpoint is excluded: different model family, cold-start semantics."""
    report = attribute_captions(_pair_doc(), model="ollama/nimble")
    assert report.failure is CaptionFailure.UNSUPPORTED_PROVIDER


def test_unavailable_when_resolution_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config error inside endpoint resolution is unavailable, never an escape."""

    def broken(provider: str, *, default_base_url: str | None, legacy_api_key_env: str | None) -> NoReturn:
        raise RuntimeError("unreadable config")

    monkeypatch.setattr(caption_mod_endpoints(), "resolve_endpoint", broken)
    report = attribute_captions(_pair_doc(), model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.UNAVAILABLE


def test_unavailable_when_sdk_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing/broken SDK surfaces as unavailable, never an escape."""
    real_import = caption_mod.import_module

    def fake(name: str, *args: str, **kwargs: str) -> object:
        if name == "typesafe_sdk":
            raise ImportError("no sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(caption_mod, "import_module", fake)
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "test-key"),
    )
    document = _pair_doc()
    before = document.model_dump()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.UNAVAILABLE
    assert document.model_dump() == before


def test_unavailable_when_construction_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client construction validates config: its failure is not a call failure."""

    def factory(*, api_key: str, model: str, base_url: str | None) -> NoReturn:
        raise RuntimeError("bad key shape")

    monkeypatch.setattr(caption_mod, "_open_client", factory)
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "k"),
    )
    report = attribute_captions(_pair_doc(), model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.UNAVAILABLE


def test_transport_error_on_call_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising transport leaves the document byte-identical."""

    class Broken:
        def __init__(self) -> None:
            self._error = ConnectionError("down")

        def __enter__(self) -> Broken:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def system_one(self, state: object, questions: object) -> object:
            raise self._error

    monkeypatch.setattr(caption_mod, "_open_client", lambda **kwargs: Broken())
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "k"),
    )
    document = _pair_doc()
    before = document.model_dump()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.TRANSPORT_ERROR
    assert document.model_dump() == before


def test_transport_error_on_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real SDK error path (HTTP 500 through real parsing) is a call failure."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return import_module("httpx2").Response(500, json={"error": "busy"})

    _install_transport(monkeypatch, handler)
    report = attribute_captions(_pair_doc(), model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.TRANSPORT_ERROR


def test_transport_error_on_unusable_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response object without answers is unusable — validator level."""

    class Odd:
        def __init__(self) -> None:
            self._response = SimpleNamespace()

        def __enter__(self) -> Odd:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def system_one(self, state: object, questions: object) -> object:
            return self._response

    monkeypatch.setattr(caption_mod, "_open_client", lambda **kwargs: Odd())
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "k"),
    )
    report = attribute_captions(_pair_doc(), model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.TRANSPORT_ERROR


def test_transport_error_when_choices_access_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising response.choices property fails the run — never escapes."""

    class BoomChoices:
        @property
        def choices(self) -> NoReturn:
            raise RuntimeError("choices property exploded")

    class BoomClient:
        def __init__(self) -> None:
            self._response = BoomChoices()

        def __enter__(self) -> BoomClient:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def system_one(self, state: object, questions: object) -> object:
            return self._response

    monkeypatch.setattr(caption_mod, "_open_client", lambda **kwargs: BoomClient())
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "k"),
    )
    document = _pair_doc()
    before = document.model_dump()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.TRANSPORT_ERROR
    assert document.model_dump() == before


def test_raising_choice_property_abstains_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising answer.choice property abstains that pair — never escapes."""

    class BoomChoice:
        probabilities = {"caption": 0.9, "none": 0.1}

        @property
        def choice(self) -> str:
            raise RuntimeError("choice property exploded")

    class BoomClient:
        def __init__(self) -> None:
            self._response = SimpleNamespace(choices={"pair_0": BoomChoice()})

        def __enter__(self) -> BoomClient:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def system_one(self, state: object, questions: object) -> object:
            return self._response

    monkeypatch.setattr(caption_mod, "_open_client", lambda **kwargs: BoomClient())
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "k"),
    )
    document = _pair_doc()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report == CaptionReport(attributed=0, abstained=1, failure=None)
    assert document.pages[0].blocks[0].relations == []


def test_caption_link_appended_relations_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A strict caption verdict appends exactly one relation; nothing else moves."""
    captured = _install_transport(monkeypatch, _ok_handler("caption", {"caption": 0.8, "none": 0.2}))
    document = _pair_doc()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report == CaptionReport(attributed=1, abstained=0, failure=None)
    assert captured["model"] == "jev-latest"
    paragraph, table = document.pages[0].blocks
    assert paragraph.relations == [ChunkRelation(kind=RelationKind.CAPTION_OF, target_id=table.id)]
    assert (paragraph.kind, paragraph.content, paragraph.metadata) == (
        ChunkKind.PARAGRAPH,
        "Table 1 shows quarterly revenue.",
        {},
    )
    assert table.relations == []


@pytest.mark.parametrize(
    ("choice", "probabilities"),
    [
        ("none", {"caption": 0.2, "none": 0.8}),
        ("caption", {"caption": 0.5, "none": 0.5}),
        ("none", {"caption": 0.8, "none": 0.2}),
        ("caption", {"caption": 0.9}),
        ("caption", {"caption": 0.6, "none": 0.3, "other": 0.1}),
        ("caption", {"caption": 1.5, "none": -0.5}),
        ("caption", {"caption": 0.6, "none": 0.2}),
    ],
)
def test_malformed_or_negative_answers_abstain(monkeypatch: pytest.MonkeyPatch, choice: str, probabilities: dict[str, object]) -> None:
    """Tie, winner-mismatch, bad keys, out-of-range, or off-unity sums: no link."""
    _install_transport(monkeypatch, _ok_handler(choice, probabilities))
    document = _pair_doc()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report == CaptionReport(attributed=0, abstained=1, failure=None)
    assert document.pages[0].blocks[0].relations == []


def test_wire_strict_non_float_scores_fail_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream strict parsing rejects non-float scores before validation sees them."""
    _install_transport(monkeypatch, _ok_handler("caption", {"caption": "high", "none": "low"}))
    document = _pair_doc()
    before = document.model_dump()
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report.failure is CaptionFailure.TRANSPORT_ERROR
    assert document.model_dump() == before


def _ok_handler(choice: str, probabilities: dict[str, object]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Serve one identical answer for every asked question."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        asked = _decode_body(request)
        answers: dict[str, object] = {name: _choice_payload(choice, probabilities) for name in cast(dict[str, object], asked["questions"])}
        return import_module("httpx2").Response(200, json=_system_one_payload(answers))

    return handler


@pytest.mark.parametrize(
    ("probabilities", "expected"),
    [
        ({"caption": 0.8, "none": 0.2}, "caption"),
        ({"caption": 0.2, "none": 0.8}, "none"),
        ({"caption": 0.5, "none": 0.5}, None),
        ({"caption": 0.9}, None),
        ({"caption": 0.6, "none": 0.3, "other": 0.1}, None),
        ({"caption": float("nan"), "none": 0.2}, None),
        ({"caption": float("inf"), "none": 0.2}, None),
        ({"caption": -0.1, "none": 1.1}, None),
        ({"caption": 1.5, "none": -0.5}, None),
        ({"caption": 0.7, "none": 0.2}, None),
        ({"caption": 0.7, "none": 0.295}, "caption"),
    ],
)
def test_strict_winner_rejects_malformed_distributions(probabilities: object, expected: str | None) -> None:
    """Validator level: exact keys, finite in-range scores near unity, unique max."""
    assert caption_mod._strict_winner(cast(Mapping[str, float], probabilities)) == expected


def test_validator_rejects_answer_objects_without_shape() -> None:
    """Validator level: a mapping-shaped answer abstains via the no-throw boundary."""
    pairs = caption_mod._candidate_pairs(_pair_doc())
    validated = caption_mod._valid_attributions(
        pairs,
        cast(Mapping[str, ChoiceAnswer], {"pair_0": {"choice": "caption"}}),
    )
    assert validated == ([], 1)


def test_validator_non_mapping_envelope_abstains() -> None:
    """Validator level: envelope access failures abstain, never escape."""
    pairs = caption_mod._candidate_pairs(_pair_doc())
    assert caption_mod._valid_attributions(pairs, cast(Mapping[str, ChoiceAnswer], ["pair_0"])) == ([], 1)


def test_truncation_flagged_in_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded snippets: over-cap text is cut and flagged, never silently."""
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(_decode_body(request))
        answers: dict[str, object] = {name: _choice_payload("none", {"caption": 0.1, "none": 0.9}) for name in cast(dict[str, object], seen["questions"])}
        return import_module("httpx2").Response(200, json=_system_one_payload(answers))

    _install_transport(monkeypatch, handler)
    document = _doc(
        _chunk("p-0", ChunkKind.PARAGRAPH, "x" * 1200),
        _chunk("p-1", ChunkKind.TABLE, "y" * 2500),
    )
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report == CaptionReport(attributed=0, abstained=1, failure=None)
    state = cast(dict[str, object], seen["state"])
    (pair_raw,) = cast(list[object], state["pairs"])
    pair_state = cast(dict[str, object], pair_raw)
    assert len(cast(str, pair_state["candidate_text"])) == caption_mod._MAX_CANDIDATE_CHARS
    assert len(cast(str, pair_state["target_text"])) == caption_mod._MAX_TARGET_CHARS
    assert pair_state["candidate_truncated"] is True
    assert pair_state["target_truncated"] is True


def test_repeat_calls_ask_fewer_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idempotency: linked pairs leave the batch; the second run asks nothing."""
    calls: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(_decode_body(request))
        answers: dict[str, object] = {
            name: _choice_payload("caption", {"caption": 0.9, "none": 0.1}) for name in cast(dict[str, object], calls[-1]["questions"])
        }
        return import_module("httpx2").Response(200, json=_system_one_payload(answers))

    _install_transport(monkeypatch, handler)
    document = _pair_doc()
    assert attribute_captions(document, model="typesafe-ai/jev-latest") == CaptionReport(1, 0, None)
    assert attribute_captions(document, model="typesafe-ai/jev-latest") == CaptionReport(0, 0, None)
    assert len(calls) == 1
    assert len(document.pages[0].blocks[0].relations) == 1


def _pair_doc() -> DocumentResult:
    """Paragraph followed by a table: one undecided candidacy."""
    return _doc(
        _chunk("p-0", ChunkKind.PARAGRAPH, "Table 1 shows quarterly revenue."),
        _chunk("p-1", ChunkKind.TABLE, "| q | rev |\n| 1 | 9 |"),
    )


def _choice_payload(choice: str, probabilities: dict[str, object]) -> dict[str, object]:
    """One canned choice answer on the SDK's wire shape."""
    return {
        "type": "choice",
        "choice": choice,
        "confidence": 0.9,
        "probabilities": probabilities,
    }


def _system_one_payload(answers: dict[str, object]) -> dict[str, object]:
    """Canned System One response envelope on the SDK's wire shape."""
    return {
        "model": "test-model",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "answers": answers,
    }


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> dict[str, object]:
    """Patch the client seam with the REAL client over controlled bytes.

    Returns the captured constructor kwargs, so endpoint binding stays
    asserted (key, model, base URL) without ever touching the network.
    """
    pytest.importorskip("httpx2", reason="caption transport tests need httpx2")
    captured: dict[str, object] = {}

    def factory(*, api_key: str, model: str, base_url: str | None) -> SystemOneClient:
        captured.update(api_key=api_key, model=model, base_url=base_url)
        sdk = import_module("typesafe_sdk")
        transport = import_module("httpx2").MockTransport(handler)
        return cast(SystemOneClient, sdk.TypeSafeClient(api_key=api_key, model=model, transport=transport))

    monkeypatch.setattr(caption_mod, "_open_client", factory)
    monkeypatch.setattr(
        caption_mod_endpoints(),
        "resolve_endpoint",
        lambda provider, *, default_base_url, legacy_api_key_env: ("http://caption.test", "test-key"),
    )
    return captured


def caption_mod_endpoints() -> ModuleType:
    """The real endpoints module, imported lazily like production code."""
    return import_module("parsecraft.providers._endpoints")


def _decode_body(request: httpx2.Request) -> dict[str, object]:
    """Decode a MockTransport body, reversing the SDK's default zstd."""
    raw = request.content
    if request.headers.get("content-encoding") == "zstd":
        raw = import_module("compression.zstd").decompress(raw)
    return cast(dict[str, object], json.loads(raw.decode("utf-8")))


def test_figure_target_and_caption_role() -> None:
    """Figures are targets; CAPTION chunks may play the caption role."""
    document = _doc(
        _chunk("p-0", ChunkKind.CAPTION, "Figure 2: throughput."),
        _chunk("p-1", ChunkKind.FIGURE, "<img>"),
    )
    pairs = caption_mod._candidate_pairs(document)
    assert [(pair.paragraph_id, pair.target_id) for pair in pairs] == [("p-0", "p-1")]


def test_wrong_order_and_targetless_sequences_ignored() -> None:
    """A target before prose, or prose with no target sibling: no candidacy."""
    paragraph = _chunk("p-0", ChunkKind.PARAGRAPH, "Table 1 is great.", order=1)
    table = _chunk("p-1", ChunkKind.TABLE, "| a |", order=0)
    assert caption_mod._candidate_pairs(_doc(table, paragraph)) == []
    assert caption_mod._candidate_pairs(_doc(paragraph, _chunk("p-1", ChunkKind.PARAGRAPH, "More."))) == []


def test_real_client_construction_opens_no_connection() -> None:
    """The default seam builds a real client with no network traffic."""
    client = caption_mod._open_client(api_key="test-key", model="test-model", base_url=None)
    with client:
        pass


def test_same_span_links_explicit_pair() -> None:
    """A CAPTION sharing its exact span with an adjacent FIGURE links."""
    span = SourceSpan(start=10, end=40)
    document = _doc(
        _chunk("p-0", ChunkKind.FIGURE, "<img>", span=span),
        _chunk("p-1", ChunkKind.CAPTION, "Throughput.", span=span),
    )
    assert link_same_span_captions(document) == 1
    caption = document.pages[0].blocks[1]
    assert caption.relations == [ChunkRelation(kind=RelationKind.CAPTION_OF, target_id="p-0")]
    assert link_same_span_captions(document) == 0


def test_same_span_ignores_mismatches() -> None:
    """Different spans, spanless captions, wrong kinds, and distance: no links."""
    document = _doc(
        _chunk("p-0", ChunkKind.CAPTION, "No span caption."),
        _chunk("p-1", ChunkKind.FIGURE, "<img>", span=SourceSpan(start=0, end=5)),
        _chunk("p-2", ChunkKind.CAPTION, "Elsewhere.", span=SourceSpan(start=6, end=9)),
        _chunk("p-3", ChunkKind.PARAGRAPH, "Prose.", span=SourceSpan(start=6, end=9)),
        _chunk("p-4", ChunkKind.TABLE, "| a |", span=SourceSpan(start=100, end=110)),
    )
    assert link_same_span_captions(document) == 0


def test_same_span_links_table_target() -> None:
    """The structural link is kind-agnostic across the two target roles."""
    span = SourceSpan(start=0, end=20)
    document = _doc(
        _chunk("p-0", ChunkKind.CAPTION, "Breakdown.", span=span),
        _chunk("p-1", ChunkKind.TABLE, "| a |", span=span),
    )
    assert link_same_span_captions(document) == 1
    assert document.pages[0].blocks[0].relations[0].target_id == "p-1"


@pytest.mark.judge
def test_live_jev_caption_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPT-IN LIVE CHECK (never in the default gate): one real Jev batch.

    Needs ``TYPESAFE_API_KEY`` and network. Reports only the requested alias
    versus the server-returned model plus outcome counts — never the key,
    prompts, or raw answers. Synthetic nonsensitive fixture only.
    """
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("live Jev check needs TYPESAFE_API_KEY")
    real_factory = caption_mod._open_client
    recorded: dict[str, str] = {}

    class Spy:
        """Record the server model, then delegate everything to the real client."""

        def __init__(self, inner: SystemOneClient) -> None:
            self._inner = inner
            self._ctx: SystemOneClient | None = None

        def __enter__(self) -> Spy:
            self._ctx = self._inner.__enter__()
            return self

        def __exit__(self, *exc: object) -> None:
            self._inner.__exit__(*exc)

        def system_one(self, state: object, questions: object) -> SystemOneResponse:
            assert self._ctx is not None
            response = self._ctx.system_one(state, questions)
            recorded["server_model"] = cast(ServerModel, response).model
            return response

    def factory(*, api_key: str, model: str, base_url: str | None) -> Spy:
        return Spy(real_factory(api_key=api_key, model=model, base_url=base_url))

    monkeypatch.setattr(caption_mod, "_open_client", factory)
    document = _doc(
        _chunk("p-0", ChunkKind.PARAGRAPH, "Table 1 lists the quarterly totals."),
        _chunk("p-1", ChunkKind.TABLE, "| quarter | total |\n| Q1 | 42 |"),
        _chunk("p-2", ChunkKind.PARAGRAPH, "The weather was mild that week."),
        _chunk("p-3", ChunkKind.FIGURE, "<img>"),
    )
    report = attribute_captions(document, model="typesafe-ai/jev-latest")
    assert report.failure is None
    assert report.attributed + report.abstained == 2
    print(
        "\nlive caption check: requested=typesafe-ai/jev-latest"
        f" server_model={recorded.get('server_model', '?')}"
        f" attributed={report.attributed} abstained={report.abstained}"
    )


def _chunk(
    chunk_id: str,
    kind: ChunkKind,
    content: str = "text",
    *,
    span: SourceSpan | None = None,
    relations: list[ChunkRelation] | None = None,
    order: int | None = None,
) -> StructuredChunk:
    """One reading-ordered chunk; ids double as reading order."""
    return StructuredChunk(
        id=chunk_id,
        kind=kind,
        content=content,
        page_number=1,
        reading_order=int(chunk_id.rsplit("-", 1)[-1]) if order is None else order,
        source_span=span,
        relations=list(relations or []),
    )


def _doc(*blocks: StructuredChunk) -> DocumentResult:
    """One-page document; metadata is fixed so diffs isolate the change."""
    return DocumentResult(
        metadata=DocumentMetadata(
            source_uri="test://caption",
            source_hash="0" * 64,
            format="text/plain",
            page_count=1,
            title=None,
            produced_at=_PRODUCED_AT,
            package_version="0.0.0-test",
        ),
        pages=[PageResult(page_number=1, blocks=list(blocks))],
    )
