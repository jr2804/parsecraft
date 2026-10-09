# trafilatura backend

## Purpose

HTML → typed Markdown chunks via [trafilatura](https://github.com/adbar/trafilatura),
shipped behind the library-named `trafilatura` extra (the docling pattern).
Opt-in only: the auto mode keeps the deterministic native-html lead.

## Ownership

Owns the `trafilatura` backend name and its light/heavy split. Does not own the
Markdown→blocks primitive (shared `adapters.markdown.markdown_blocks`) or the
backend registry (public entry-point API only).

## Local Contracts

- **Opt-in, never the auto default.** trafilatura's readability heuristics
  under-extract RFC-class single-page specs (measured 5,592 of 503,420 chars in
  the pc-1wn recon); native-html stays the deterministic lead.
- **Raw bytes in, never a pre-decoded string.** `source_bytes` goes straight to
  `trafilatura.extract`, which reads the declared charset itself. Decoding first
  (UTF-8, `errors="replace"`) destroyed every non-ASCII character on a non-UTF-8
  page — verified against the real library, where a latin-1 `café` came back as
  `caf�`.
- **Three verified shape quirks, all documented, none patched.** A single-row
  `<table>` with no `<thead>` loses its `|---|` separator; a single-line
  `<pre><code>` comes back as an inline code span rather than a fence; and
  trafilatura sometimes passes raw HTML (`<img …>`) straight through, which the
  shared Markdown projection can only place as `UNKNOWN`. All three are recorded
  in `docs/reference/backends.md`; none is papered over with a heuristic.
- **Behaviour contract (verbatim, do not loosen without a decision):**
  `extract(output_format="markdown", include_tables=True, include_links=False,
  no_fallback=False)`. An empty Markdown result falls back to plain-text
  extraction before it is called a failure.
- **Typed chunks, never one paragraph.** The Markdown is projected through
  `markdown_blocks` so headings, lists, tables and code fences land as
  `ChunkKind.CODE` etc.
- **Asset-free.** No `model_asset`, no download notice, no offline carve-out —
  this is the first heavy backend with no weights (ADR-0006 does not apply).
- **HTML has no page model:** one `PageResult` at page 1, matching
  liteparse/docling.
- **Converter, not analyzer.** `analyze` returns a minimal structural signal;
  the honest analyzer stays native text/HTML.

## Work Guidance

- `trafilatura.py` is the light half: no heavy imports (ADR-0005), so the
  backend is listable and resolvable without the dependency.
- `_impl.py` is imported only inside the factory body.
- The `trafilatura>=2.3.1` pin lives once, in the extra. There is deliberately
  no runtime version gate: unlike the OCR window there is no verified
  silent-corruption failure mode, and a correct check would need `packaging`,
  which trafilatura does not depend on and parsecraft does not declare. An old
  trafilatura surfaces as a typed `BACKEND_ERROR` failure from `_extract`.

## Verification

- `tests/test_backends_trafilatura.py` — offline-stubbed (the trafilatura
  import is stubbed; no network), covering the knox call shape, the TXT
  fallback, typed-chunk projection, the dependency/format/failure boundaries
  and the raw-bytes charset contract. The real-library tests
  (`test_real_trafilatura_*`) are active only with the extra and skipped
  without it — the light `mise dev` environment has no trafilatura, which is
  what makes the `ty: ignore` on the module-level import load-bearing.
- `tests/test_offline_import.py` — the backend must not pull heavy imports at
  package import.

## Child DOX Index

None.
