"""Regression pins for pc-3vo: pipe-heavy fixture guard, list fidelity, frontmatter.

The trigger: a 9 KB pipe-heavy Markdown file hung docling's marko parser at
98.5% CPU for over an hour (kompend production incident, docling 2.142.1 /
marko 2.2.4, reproduced on 2.145.0 / 2.2.5). Root cause, narrowed by
kompend-1 — retracting the earlier 93-byte "last block, no trailing blank"
shape, which measured docling import time, not parse: nesting opens the
recursion (an outer list item, a nested item one level deeper, then a
continuation at the nested indent containing any ``|``), and the pipe merely
feeds marko's ``Table.match`` via the patched ``break_paragraph``. The
native-markdown adapter must never reproduce that class of failure: the
fixture below carries the proven shape in its last item, and the tests pin
literal preservation, per-item chunking, and parse time.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from parsecraft.adapters import parse_markdown
from parsecraft.ir.models import ChunkKind
from tests.fixtures.sources import synthetic_pipe_heavy_markdown

PRODUCED = datetime(2026, 9, 27, tzinfo=UTC)


# ── fixture guard: parses fast, pipes survive, no table hallucinated ───────
# Measured 2026-10-30 (worktree, this machine): ~6 ms for the 4.5 KB fixture;
# adversarial battery on the same build peaked at 103 ms (757 KB flat text),
# 75 ms (14 KB link storm), 56 ms (11 KB table storm) — no super-linear path.
# The 1.0 s bound is ~10x headroom over the worst measured case, so a future
# parser regression (dependency bump, algorithm change) fails the build long
# before it can approach production-ingest timescales.
#
# The parse runs in a SUBPROCESS with a generous 60 s wall: an in-process
# timing assertion cannot fail on a hang — it hangs the suite with it. Here
# a hang raises TimeoutExpired out of `subprocess.run`, which pytest reports
# as a FAILED test (red, with a traceback) instead of a stalled suite, and the parse time the child reports must still sit under 1.0 s.
_GUARD_SCRIPT = (
    "import time; from datetime import UTC, datetime; "
    "from parsecraft.adapters import parse_markdown; "
    "from tests.fixtures.sources import synthetic_pipe_heavy_markdown; "
    "t0 = time.perf_counter(); "
    "r = parse_markdown(synthetic_pipe_heavy_markdown(), 'mem://guard', "
    "produced_at=datetime(2026, 9, 27, tzinfo=UTC)); "
    "print(f'{(time.perf_counter() - t0) * 1000:.1f} {len(r.pages[0].blocks)}')"
)


# kompend-1's matrix (2026-10-30), transcribed verbatim — 11 of the 12
# reported cases (the 12th is unreconstructable; ask kompend-1, don't invent
# it). Harness: real `DocumentConverter`, timer strictly around `convert()`,
# stack dumped on timeout, process killed at 60 s. "patched" = docling's
# `_GfmParagraph.break_paragraph` monkey-patch; "plain gfm" = bare marko.
# Times are THEIR measurements on docling 2.142.1/marko 2.2.4 (and
# 2.145.0/2.2.5), NOT measured here. Pinned here: our adapter on the same
# shapes — sub-ms to single-digit ms, exact chunks, never TABLE.
#   minimal (`- a`/`  - b`/`  |c|`)                  patched HANG / plain 47 ms
#   no final newline (same, no trailing `\n`)         patched HANG / plain 41 ms
#   single unpaired pipe (`  x |`)                    patched HANG / plain 116 ms
#   deeper nesting (4-space bullet + pipe line)       patched HANG / plain 218 ms
#   mixed indent (4-space bullet, 2-space pipe)      patched HANG / plain 233 ms
#   sibling bullet (`- a`/`- b`/`  |c|`)              patched 49 ms
#   blank-line separated (`- a`/blank/`  |c|`)       patched 53 ms
#   no pipe at all (`- a`/`  - b`/`  c`)              patched 86 ms
#   nested only, no outer (`  - b`/`  |c|`)          patched 114 ms
#   no nesting (`- b`/`  |c|`)                        patched 89 ms
#   outer at top level (`a`/`  - b`/`  |c|`)         patched 165 ms
@pytest.mark.parametrize(
    ("source", "kinds", "contents"),
    [
        ("- a\n  - b\n  |c|\n", [ChunkKind.LIST], ["a\nb\n|c|"]),
        ("- a\n  - b\n  |c|", [ChunkKind.LIST], ["a\nb\n|c|"]),
        ("- a\n  - b\n  x |\n", [ChunkKind.LIST], ["a\nb\nx |"]),
        ("- a\n    - b\n    |c|\n", [ChunkKind.LIST], ["a\nb\n|c|"]),
        ("- a\n    - b\n  |c|\n", [ChunkKind.LIST], ["a\nb\n|c|"]),
        ("- a\n- b\n  |c|\n", [ChunkKind.LIST, ChunkKind.LIST], ["a", "b\n|c|"]),
        ("- a\n\n  |c|\n", [ChunkKind.LIST], ["a\n|c|"]),
        ("- a\n  - b\n  c\n", [ChunkKind.LIST], ["a\nb\nc"]),
        ("  - b\n  |c|\n", [ChunkKind.LIST], ["b\n|c|"]),
        ("- b\n  |c|\n", [ChunkKind.LIST], ["b\n|c|"]),
        ("a\n  - b\n  |c|\n", [ChunkKind.PARAGRAPH, ChunkKind.LIST], ["a", "b\n|c|"]),
    ],
    ids=[
        "hang-minimal",
        "hang-no-final-newline",
        "hang-unpaired-pipe",
        "hang-deeper-nesting",
        "hang-mixed-indent",
        "ok-49ms-sibling-bullet",
        "ok-53ms-blank-separated",
        "ok-86ms-no-pipe",
        "ok-114ms-nested-no-outer",
        "ok-89ms-no-nesting",
        "ok-165ms-outer-at-top",
    ],
)
def test_docling_matrix_shape_parses_on_our_adapter(source: str, kinds: list[ChunkKind], contents: list[str]) -> None:
    result = parse_markdown(source, "mem://matrix", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == kinds
    assert [block.content for block in _blocks(result)] == contents


# Flat-list characterisation (NOT the trigger — the hang needs nesting, see
# above). kompend-1's retracted 93-byte shape, kept as parsing-behaviour
# pins: our adapter must treat trailing-blank / sibling variants identically,
# and a future regression names the variant that changed.
_FLAT_SHAPE_TEXT = "- Constant-delay indicator: mean over frames start+2..stop of\n  |delay[i] - delay[i-2]| ms where < 10 ms."


def test_pipe_heavy_fixture_parses_well_under_one_second() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(  # noqa: S603 - trusted local interpreter, fixed argv, no shell
        [sys.executable, "-c", _GUARD_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=root,
        check=True,
    )
    reported_ms, chunk_count = completed.stdout.split()
    assert float(reported_ms) < 1000.0
    assert int(chunk_count) == 31  # heading + 30 items, list is the last block.


def test_pipe_trigger_at_eof_parses() -> None:
    result = parse_markdown(_FLAT_SHAPE_TEXT + "\n", "mem://trigger-eof", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.LIST]
    assert "|delay[i] - delay[i-2]|" in _blocks(result)[0].content


def test_pipe_trigger_with_trailing_blank_line_parses() -> None:
    result = parse_markdown(_FLAT_SHAPE_TEXT + "\n\n", "mem://trigger-blank", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.LIST]


def test_pipe_trigger_with_following_sibling_parses() -> None:
    result = parse_markdown(_FLAT_SHAPE_TEXT + "\n- next\n", "mem://trigger-sibling", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.LIST, ChunkKind.LIST]


@pytest.mark.parametrize(
    "source",
    [
        "- a\n  |start| samples of the window\n",  # lone pipe-opening continuation
        "para\n\na | b |\n",  # plain paragraph bars at EOF
        "| x |\n\n# H\n",  # bars followed by a heading
        "a | b\n\nc | d\n",  # single unpaired pipes, no table possible
    ],
)
def test_pipe_near_misses_parse_without_tables(source: str) -> None:
    result = parse_markdown(source, "mem://near-miss", produced_at=PRODUCED)
    assert all(block.kind is not ChunkKind.TABLE for block in _blocks(result))


def test_pipe_heavy_fixture_preserves_every_pipe() -> None:
    source = synthetic_pipe_heavy_markdown()
    result = parse_markdown(source, "mem://pipe-heavy", produced_at=PRODUCED)
    assert sum(block.content.count("|") for block in _blocks(result)) == source.count("|")
    assert all(block.kind is not ChunkKind.TABLE for block in _blocks(result))


# ── list fidelity: one chunk per item, continuations stay attached ─────────


def test_list_item_with_continuation_stays_one_chunk() -> None:
    result = parse_markdown("- item one\n  tightened by C/I = 3 dB\n- item two\n", "mem://li", produced_at=PRODUCED)
    assert [block.content for block in _blocks(result)] == ["item one\ntightened by C/I = 3 dB", "item two"]
    assert [block.metadata["item_index"] for block in _blocks(result)] == ["0", "1"]


def test_nested_list_stays_attached_to_parent_item() -> None:
    result = parse_markdown("- outer\n  - nested a\n  - nested b\n", "mem://nl", produced_at=PRODUCED)
    assert len(_blocks(result)) == 1
    assert "nested a" in _blocks(result)[0].content
    assert "nested b" in _blocks(result)[0].content


def test_single_item_list_gets_one_chunk_with_index_zero() -> None:
    result = parse_markdown("- solo\n", "mem://solo", produced_at=PRODUCED)
    assert len(_blocks(result)) == 1
    assert _blocks(result)[0].metadata == {"list_type": "bullet", "item_index": "0"}


# ── frontmatter positives: one verbatim UNKNOWN chunk, body survives ───────


def test_frontmatter_with_blank_line_after_fence() -> None:
    source = "---\ntitle: Probe Note\nkind: page\ntags:\n- alpha\n- beta\n---\n\n# Real Heading\n"
    result = parse_markdown(source, "mem://fm1", produced_at=PRODUCED)
    head, heading = _blocks(result)
    assert head.kind is ChunkKind.UNKNOWN
    assert head.metadata == {"frontmatter": "true"}
    assert head.reading_order == 0
    assert heading.kind is ChunkKind.HEADING
    assert heading.content == "Real Heading"


def test_frontmatter_without_blank_line_after_fence() -> None:
    source = "---\ntitle: Pair Process\nokf_version: 0.2\n---\n\n# hello\n"
    result = parse_markdown(source, "mem://fm2", produced_at=PRODUCED)
    head, heading = _blocks(result)
    assert head.kind is ChunkKind.UNKNOWN
    assert head.metadata == {"frontmatter": "true"}
    assert heading.content == "hello"


def test_frontmatter_with_only_empty_valued_keys() -> None:
    source = "---\ntags:\n- alpha\n- beta\n---\n\nbody\n"
    result = parse_markdown(source, "mem://fm3", produced_at=PRODUCED)
    head, body = _blocks(result)
    assert head.kind is ChunkKind.UNKNOWN
    assert head.metadata == {"frontmatter": "true"}
    assert head.content == "---\ntags:\n- alpha\n- beta\n---"
    assert body.kind is ChunkKind.PARAGRAPH
    assert body.content == "body"


def test_frontmatter_accepts_yaml_end_marker() -> None:
    source = "---\ntitle: x\n...\n\n# H\n"
    result = parse_markdown(source, "mem://fm4", produced_at=PRODUCED)
    head, heading = _blocks(result)
    assert head.metadata == {"frontmatter": "true"}
    assert head.content == "---\ntitle: x\n..."
    assert heading.content == "H"


def test_frontmatter_tolerates_leading_bom_then_strips_it() -> None:
    source = "\ufeff---\ntitle: x\n---\n\n# H\n"
    result = parse_markdown(source, "mem://fm5", produced_at=PRODUCED)
    head, heading = _blocks(result)
    assert head.metadata == {"frontmatter": "true"}
    assert head.content == "---\ntitle: x\n---"
    assert (head.source_span.start, head.source_span.end) == (0, 17)
    assert heading.content == "H"


def test_frontmatter_crlf_normalizes_to_lf_with_source_span() -> None:
    source = "---\r\ntitle: x\r\n---\r\n\r\n# H\r\n"
    result = parse_markdown(source, "mem://fm6", produced_at=PRODUCED)
    head = _blocks(result)[0]
    assert head.content == "---\ntitle: x\n---"
    assert (head.source_span.start, head.source_span.end) == (0, 18)


def test_frontmatter_head_span_covers_source_fence_lf() -> None:
    source = "---\ntitle: Probe\n---\n\n# H\n"
    result = parse_markdown(source, "mem://fm7", produced_at=PRODUCED)
    head = _blocks(result)[0]
    assert (head.source_span.start, head.source_span.end) == (0, 20)
    assert head.source_span.end - head.source_span.start == len(head.content)


def test_frontmatter_only_document_is_one_chunk() -> None:
    source = "---\ntitle: x\n---\n"
    result = parse_markdown(source, "mem://fm8", produced_at=PRODUCED)
    assert len(_blocks(result)) == 1
    assert _blocks(result)[0].kind is ChunkKind.UNKNOWN
    assert _blocks(result)[0].metadata == {"frontmatter": "true"}


def test_frontmatter_span_shifts_by_fence_length_lf() -> None:
    _assert_shifted(
        "---\ntitle: Probe\n---\n\n# Real Heading\n\nbody para\n",
        "---\ntitle: Probe\n---\n\n",
        "# Real Heading\n\nbody para\n",
    )


def test_frontmatter_span_shifts_by_fence_length_crlf() -> None:
    _assert_shifted(
        "---\r\ntitle: Probe\r\n---\r\n\r\n# Real Heading\r\n\r\nbody para\r\n",
        "---\r\ntitle: Probe\r\n---\r\n\r\n",
        "# Real Heading\r\n\r\nbody para\r\n",
    )


def test_frontmatter_span_shifts_with_no_blank_line_after_fence() -> None:
    _assert_shifted("---\ntitle: Probe\n---\n# H\n", "---\ntitle: Probe\n---\n", "# H\n")


# ── frontmatter span invariant: post-fence spans shift by the fence length ──
# The span is a SOURCE span (it covers markup), so `text[span] == content`
# does NOT hold — even without frontmatter. What must hold is positional:
# every post-fence chunk sits exactly `len(consumed_prefix)` further into the
# text than its no-frontmatter equivalent. That is what proves `line_base`
# is right, for LF, CRLF, and fence-with-no-blank-line alike.


def _assert_shifted(source: str, prefix: str, plain: str) -> None:
    fenced = parse_markdown(source, "mem://f", produced_at=PRODUCED)
    bare = parse_markdown(plain, "mem://b", produced_at=PRODUCED)
    assert len(_blocks(fenced)) == len(_blocks(bare)) + 1
    for fenced_block, bare_block in zip(_blocks(fenced)[1:], _blocks(bare), strict=True):
        assert (fenced_block.source_span.start, fenced_block.source_span.end) == (
            bare_block.source_span.start + len(prefix),
            bare_block.source_span.end + len(prefix),
        )
        assert _span_text(source, fenced_block) == _span_text(plain, bare_block)


def _span_text(source: str, block: Any) -> str:
    span = block.source_span
    return source[span.start : span.end]


# ── frontmatter negatives: break-like shapes must tokenise normally ─────────


def test_setext_heading_after_break_is_not_frontmatter() -> None:
    result = parse_markdown("---\nSome Title\n---\n", "mem://n1", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.UNKNOWN, ChunkKind.HEADING]
    assert _blocks(result)[1].content == "Some Title"


def test_prose_colon_after_break_is_not_frontmatter() -> None:
    result = parse_markdown("---\nSome Title:\n---\n", "mem://n2", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.UNKNOWN, ChunkKind.HEADING]
    assert all("frontmatter" not in block.metadata for block in _blocks(result))


def test_document_opening_with_break_is_not_frontmatter() -> None:
    result = parse_markdown("---\n\n# Heading\n", "mem://n3", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.UNKNOWN, ChunkKind.HEADING]


def test_mid_document_break_with_colon_content_is_not_frontmatter() -> None:
    result = parse_markdown("para\n\n---\ntitle: x\n---\n", "mem://n4", produced_at=PRODUCED)
    assert [block.kind for block in _blocks(result)] == [ChunkKind.PARAGRAPH, ChunkKind.UNKNOWN, ChunkKind.HEADING]
    assert all("frontmatter" not in block.metadata for block in _blocks(result))


def test_unclosed_fence_is_not_frontmatter() -> None:
    result = parse_markdown("---\ntitle: x\n\nbody\n", "mem://n5", produced_at=PRODUCED)
    assert all("frontmatter" not in block.metadata for block in _blocks(result))


def test_list_only_fence_is_not_frontmatter() -> None:
    result = parse_markdown("---\n- just a list\n---\n", "mem://n6", produced_at=PRODUCED)
    assert all("frontmatter" not in block.metadata for block in _blocks(result))
    assert any(block.kind is ChunkKind.LIST for block in _blocks(result))


def test_end_marker_without_key_is_not_frontmatter() -> None:
    result = parse_markdown("---\n...\n\nbody\n", "mem://n7", produced_at=PRODUCED)
    assert all("frontmatter" not in block.metadata for block in _blocks(result))


def _blocks(result: Any) -> list[Any]:
    return result.pages[0].blocks
