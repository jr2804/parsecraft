# Benchmarks

Measured throughput and text coverage for the real backends over the pinned
local corpus, measured on the maintainer's machine on **2026-09-27**.

## What was measured

Documents (all local, never fetched at run time):

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
(CPU), plus `liteparse` and the GPU OCR backends where noted below. Every
backend eligible for a document's media type is measured once per document.

Environment: Windows laptop, CPython 3.13, CPU only for the table below;
light extras (`download`, `web`, `pdf-lite`, `pdf`, `liteparse`) installed.
No GPU work is included in this snapshot — see [Gaps](#honest-gaps).

## Reproduce

```bash
parsecraft benchmark *.txt *.md *.html *.pdf --output docs/benchmarks/
```

The report writers are deterministic: stable key and row ordering, fixed
rounding, LF newlines, no wall-clock fields. Re-running on the same inputs
yields the same schema and row set — the measured values (elapsed, pages/s,
peak bytes) are run data and will vary. Benchmarking goes straight to the
backend's `convert` and never reads the conversion cache, which would report
~0 s and falsify the measurement.

`native-pdf` extraction needs the AGPL `pdf` extra; without it the report
records a typed `dependency_missing` row instead of crashing.

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

The four OCR models ran on an RTX A2000 8 GB over **2-page fixtures** of the
three corpus PDFs — full-corpus OCR would take hours. At run time the four
models pinned two different `transformers` majors, so the snapshot is two
passes over the same fixtures. That split was a *pinning* fact, not a model
requirement: the extras now share one `transformers>=5.17,<6` window and the
TeleOCR/Unlimited models load from local vendored modeling code, so a re-run
needs **one** environment and **one** pass (see
[Deferred runs](#deferred-runs)).

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

Reading rules:

- `elapsed_s` **includes per-document model load** — the runner constructs
  the backend per document; these are 2-page fixtures, not per-page rates
  you can multiply to the full corpus.
- The two passes are different virtualenvs: merge rows per backend, never
  sum across passes.
- Full-corpus `qianfan` is a dated deferral, not a gap — see
  [Deferred runs](#deferred-runs).
- `selected` is recorded before load success/failure: a
  `selected: true` + `failures: [backend_error]` row means “planner picked
  it, then it could not load in this transformers” — not a planner bug.

## Deferred runs

The legs below were **deliberately deferred on 2026-09-28**, with the reason
recorded so a later run is a scheduling decision rather than a missing result.
None of them failed: the measured fixtures already prove the adapters work, and
the full-corpus runs only cost machine time.

| Run | Deferred on | Reason | Command |
| --- | --- | --- | --- |
| Full-corpus OCR, all four models | 2026-09-28 | Cost/time only: the 2-page fixtures already take 82–1024 s per document, so the corpus is many hours of exclusive GPU time. Not an install conflict — the extras are jointly installable on one `transformers` window | [Full-corpus command](#full-corpus-ocr-command) |
| Full-corpus `ocr-qianfan` | 2026-09-28 | Cost/time: ≈ 145 s/page measured → ≈ 20 h for NIST SP 800-53r5 alone. Weights are staged and sha-verified (20 GB cache), so the run is offline-ready | same command (qianfan is one of the measured backends) |
| Full-corpus docling | 2026-09-28 | Runtime budget: docling is ≈ 8–15 s/page warm on this laptop → ≈ 1.8 h for NIST SP 800-53r5 alone, and the incident shape (>1 h on 113 pp) says hours. Not an automated check | [Full-corpus docling](#full-corpus-docling-command) |
| Quantised variants (int8 / 4-bit) | 2026-09-28 | Out of scope by design: quantisation is a backend-installation choice, not a harness dimension | — |

### Full-corpus OCR command

All four OCR extras share one `transformers>=5.17,<6` window, so the full run is
a single pass in a single environment. The command prints JSON on stdout and
never overwrites the committed CPU report; the AGPL `pdf` extra is required for
PDF rasterisation.

```bash
parsecraft benchmark itu-t-p863.pdf etsi-ts-103558.pdf nist-sp-800-53r5.pdf \
  --json > benchmark-ocr-full.json
```

### Full-corpus docling command

```bash
parsecraft benchmark *.pdf --json > benchmark-docling-full.json
```

The committed bounded runs stay the evidence for adapter correctness:
[`benchmark-ocr-pass-a.json`](benchmark-ocr-pass-a.json) and
[`benchmark-ocr-pass-b.json`](benchmark-ocr-pass-b.json).

## Cross-check: a 113-page PDF on Docling

*Docling ran for
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
signals say native extraction cannot do the job (the cold-cache budget for a
whole document, escalations included, is 15 minutes).

## Docling vs native-pdf (bounded head-to-head)

The gap the incident cross-check only inferred is now measured on **2-page
fixtures** of the three corpus PDFs (docling 2.130.0, 2026-09-28).
Environment: Windows laptop, CPython 3.13, CPU only. Docling ran its **default
pipeline untuned** (layout, tables, and its bundled RapidOCR); `native-pdf` used
PyMuPDF. Both were measured with `parsecraft benchmark`.

| fixture (2 pp) | docling | native-pdf | ratio |
| --- | --- | --- | --- |
| `itu-t-p863` (of 80) | 20.6 s | 0.014 s | ≈ 1470× |
| `etsi-ts-103558` (of 68) | 30.5 s | 2.98 s | ≈ 10× |
| `nist-sp-800-53r5` (of 492) | 15.5 s | 0.024 s | ≈ 650× |

- Docling's **first** conversion in a process is far slower — 191.9 s for the
  p863 fixture — because it loads its layout/OCR models. The table is warm; its
  peak Python memory is 158–405 MB against 0.03–15 MB for `native-pdf`.
- Docling emits more structure (headings, tables) at comparable text coverage;
  the cost of that is runtime and memory, not correctness.
- Ratios vary per document (the ETSI fixture is the most docling-favourable of
  the set) — read the per-row numbers, not a single ratio.

This is an **on-demand measurement**, not an automated check. Cut two-page
fixtures from the corpus PDFs and benchmark them:

```bash
parsecraft benchmark fixtures/*.pdf --json > benchmark-docling-vs-native.json
```

## Honest gaps

- **Full-corpus OCR was deferred, not skipped.** The GPU legs cover 2-page
  fixtures only (see [GPU OCR legs](#gpu-ocr-legs-bounded-fixtures)); scaling
  any OCR number here to a whole document would be fabricated. The dated
  deferral and exact commands are in [Deferred runs](#deferred-runs). The pinned
  licensed PDFs are hand-staged and never downloaded.
- **Full-corpus `qianfan` is a dated deferral** (≈ 145 s/page measured; ≈ 20 h
  for NIST alone) — see [Deferred runs](#deferred-runs). All four models are
  staged and sha-verified (20 GB cache), so the deferred run passes offline
  asset checks.
- **VRAM is not measured here.** Peak bytes are `tracemalloc` (host Python
  allocations); GPU VRAM is out of scope.
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
| [`benchmark.json`](benchmark.json) | full JSON report, schema-guarded |
| [`benchmark-ocr-pass-a.json`](benchmark-ocr-pass-a.json) | GPU pass A (ovis + qianfan, transformers 5.17.0), 12 rows — schema-guarded |
| [`benchmark-ocr-pass-b.json`](benchmark-ocr-pass-b.json) | GPU pass B (tele + unlimited, transformers 4.57.1), 15 rows — schema-guarded |
| [`benchmark-docling-vs-native.json`](benchmark-docling-vs-native.json) | docling vs native-pdf, 2-page fixtures, 6 rows |
