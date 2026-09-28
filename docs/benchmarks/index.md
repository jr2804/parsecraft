# Benchmarks

Phase 2 measurement report for ParseCraft: real backends over the pinned
local corpus, produced by the in-repo harness
(`parsecraft.benchmark`) on **2026-09-27**.

## What was measured

Documents (all local, never fetched at run time) from the corpus staged in
`tests/downloads/`:

| document | format | pages |
| --- | --- | --- |
| `…-pg1342.txt` (Gutenberg, Pride & Prejudice, 754 KiB) | text/plain | 1 |
| `…-rfc5322.txt` (119 KiB) | text/plain | 1 |
| `…-commonmark-spec.md` (201 KiB) | text/markdown | 1 |
| `whatwg-html.html` (151 KiB) | text/html | 1 |
| `wikipedia-calculus.html` (792 KiB) | text/html | 1 |
| `itu-t-p863.pdf` (licensed, 2.3 MiB) | application/pdf | 80 |
| `etsi-ts-103558.pdf` (licensed, 2.7 MiB) | application/pdf | 68 |
| `…-nist-sp-800-53r5.pdf` (5.8 MiB) | application/pdf | 492 |

Backends: `native-text`, `native-markdown`, `native-html`, `native-pdf`
(CPU), plus `liteparse` and the GPU OCR backends where noted below. The
harness measures every backend that is eligible for each document
(`is_hard_eligible` + the document's media type), one conversion per cell.

Environment: Windows laptop, CPython 3.13, CPU only for the table below;
light extras (`download`, `web`, `pdf-lite`, `pdf`, `liteparse`) installed.
No GPU work is included in this snapshot — see [Gaps](#honest-gaps).

## Reproduce

```bash
uv run --extra pdf --extra liteparse --extra download --extra web --extra pdf-lite \
  parsecraft benchmark tests/downloads/*.html tests/downloads/*.md \
    tests/downloads/*.pdf tests/downloads/*.txt \
  -o docs/benchmarks/
```

Notes:

- `--extra pdf` pulls PyMuPDF (AGPL) explicitly — the copyleft choice stays
  an operator decision (ADR-0003); `native-pdf` records a typed
  `dependency_missing` row instead of crashing when it is absent.
- The `--extra` flags install **transiently**: plain `uv run` does not prune
  them afterwards, and the canonical dev environment (which `ty`'s
  optional-import ignore comments assume) is restored with `mise dev`. Run
  `mise dev` before the quality gate if you benchmarked first.
- The report writers are deterministic: stable key and row ordering, fixed
  rounding, LF newlines, no wall-clock fields. Re-running on the same
  inputs yields the same schema and row set — the measured values
  (elapsed, pages/s, peak bytes) are run data and will vary.
- `tests/test_benchmark_report.py` guards the committed artifacts: byte
  round-trip through the current writers, pinned schema, sorted rows.

## Results

The full harness output is committed alongside this page:
[`benchmark.md`](benchmark.md) and [`benchmark.json`](benchmark.json).

Headlines (CPU pass, 2026-09-27; values as committed in
[`benchmark.json`](benchmark.json)):

| document | backend | elapsed | pages/s | coverage |
| --- | --- | --- | --- | --- |
| itu-t-p863.pdf (80 pp) | native-pdf | **0.612 s** | **130.8** | 0.999 |
| nist-sp-800-53r5.pdf (492 pp) | native-pdf | **4.002 s** | **122.9** | 0.995 |
| etsi-ts-103558.pdf (68 pp, image-heavy) | native-pdf | 6.468 s | 10.5 | 0.963 |
| itu-t-p863.pdf (80 pp) | liteparse | 5.672 s | 14.1 | 1.017 |
| nist-sp-800-53r5.pdf (492 pp) | liteparse | 115.647 s | 4.3 | 0.904 |
| etsi-ts-103558.pdf (68 pp) | liteparse | 6.011 s | 11.3 | 1.012 |
| pg1342.txt (754 KiB) | native-text | 0.002 s | 409.9 | 0.988 |
| rfc5322.txt | native-text | 0.009 s | 116.6 | 0.976 |
| commonmark-spec.md (201 KiB) | native-markdown | 0.739 s | 1.4* | 0.717 |
| whatwg-html.html | native-html | 0.873 s | 1.1* | 0.271 |
| wikipedia-calculus.html | native-html | 2.460 s | 0.4* | 0.106 |

\* Single-page formats: "pages/s" is one page over elapsed time — read the
elapsed figure instead. Coverage may exceed 1.0 (liteparse emits slightly
more characters than the analyzer counted — different segmentation, not a
bug).

## GPU OCR legs (bounded fixtures)

The four OCR models ran in the isolated GPU environment (`.venv-gpu`, RTX
A2000 8 GB) over **2-page fixtures** of the three corpus PDFs
(`.tmp/ocr-bench/*-p1-2.pdf`) — full-corpus OCR would take hours. The four
models pin two mutually exclusive `transformers` majors, so the run is two
passes over the same fixtures:

| fixture | pass A: ovis (5.17) | pass A: qianfan (5.17) | pass B: tele (4.57.1) | pass B: unlimited (4.57.1) |
| --- | --- | --- | --- | --- |
| etsi-ts-103558-p1-2 | 197.6 s | 713.7 s | 220.2 s | 751.7 s |
| nist-…-p1-2 | 212.6 s | `backend_error` | 81.7 s | 128.0 s |
| itu-t-p863-p1-2 | 413.8 s | 1024.0 s | 84.0 s | 133.6 s |

Cross-version rows behave exactly as typed: the wrong-major pair records
`backend_error` rows whose detail names the cause (e.g. `qwen3_5`/
`qianfan_ocr` unknown to transformers 4.57.1), never a silent pass.
`liteparse` rows (0.07–0.16 s for the fixtures) appear in pass B after an
entry-point refresh.

Reading rules (also pc-4's run notes):

- `elapsed_s` **includes per-document model load** — the runner constructs
  the backend per document; these are 2-page fixtures, not per-page rates
  you can multiply to the full corpus.
- The two passes are different virtualenvs: merge rows per backend, never
  sum across passes.
- Full-corpus `qianfan` remains a documented **not run** (~145 s/page →
  ≈ 20 h for NIST alone); its weights are now staged and offline-ready.
- `selected` is recorded before load success/failure: a
  `selected: true` + `failures: [backend_error]` row means “planner picked
  it, then it could not load in this transformers” — not a planner bug.

## Cross-check: the motivating incident

ADR-0001 records the incident this package exists to fix: *Docling ran for
**>1 hour** on a 113-page ITU-T P.863 PDF at full CPU/RAM and was
cancelled* (≈ >31.9 s/page, never finished).

Measured here on the staged ITU-T P.863 artifact (80 pages, same document
family — the staged file is not byte-identical to the incident's 113-page
bundle): **native-pdf extracts 80 pages in 0.612 s (≈ 7.7 ms/page)**, and
the 492-page NIST SP 800-53r5 in 4.00 s (122.9 pages/s). That is more than
three orders of magnitude below the incident's per-page cost — with honest
caveats: this is text-layer extraction into typed chunks, not Docling's
full layout/table pipeline. The routing design leans exactly on this gap: run the cheap
native pass first, escalate to OCR/layout backends only for pages whose
signals say native extraction cannot do the job (ADR-0001 §6 keeps the
15-minute cold-cache budget for the whole document, escalations included).

## Honest gaps

- **Full-corpus OCR was not run.** The GPU legs cover 2-page fixtures only
  (see [GPU OCR legs](#gpu-ocr-legs-bounded-fixtures)); scaling any OCR
  number here to a whole document would be fabricated. The pinned corpus
  fetch tier (`mise run test-corpus`) stays opt-in; the licensed PDFs are
  hand-staged and never downloaded by the harness.
- **Full-corpus `qianfan` not run** (≈ 145 s/page measured; ≈ 20 h for NIST
  alone). All four models are staged and sha-verified (20 GB cache), so
  future runs pass offline asset checks.
- **VRAM is not measured here.** Peak bytes are `tracemalloc` (host Python
  allocations). GPU VRAM is covered by the separate `gpu` test tier
  (`mise run test-gpu`, RTX A2000 8 GB pins in `pyproject.toml`).
- **No quantisation variants** were benchmarked — model quantisation stays
  a backend-installation concern, not a harness dimension.
- **`exoplanets.csv` and `wikidata-q42.json` were skipped** (unsupported
  source suffix in the CLI media-type map). The skips are recorded in the
  report, not hidden.
- **HTML coverage is a structure proxy.** `native-html` emits headings,
  paragraphs, lists, tables, code, and quotes; raw inline markup dominates
  the analyzer's character count, so 0.11–0.27 coverage is expected for
  markup-heavy pages, not data loss.
- `itu-t-p863.pdf` shows `selected: -`: its page signals routed some pages
  to OCR intents while no OCR backend was installed in this environment, so
  auto-mode planning correctly declined (`selected` is null) — the direct
  per-backend measurement still ran.

## Artifacts

| file | contents |
| --- | --- |
| [`benchmark.md`](benchmark.md) | full Markdown report (harness output) |
| [`benchmark.json`](benchmark.json) | full JSON report, schema-guarded by tests |
| [`benchmark-ocr-pass-a.json`](benchmark-ocr-pass-a.json) | GPU pass A (ovis + qianfan, transformers 5.17.0), 12 rows — schema-guarded |
| [`benchmark-ocr-pass-b.json`](benchmark-ocr-pass-b.json) | GPU pass B (tele + unlimited, transformers 4.57.1), 15 rows — schema-guarded |
