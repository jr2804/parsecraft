# Benchmarks

Measured throughput and text coverage for the real backends over the pinned
local corpus. Latest run: **2026-10-31** at commit `a546d73`
(`2026.10.13.post53.dev0+a546d73`), CPU only, PyMuPDF not installed.
The 2026-09-27 snapshot (11 rows, with PyMuPDF measured) is retained under
[2026-09-27 snapshot](#2026-09-27-snapshot) with its provenance; no
historical number is presented as a new-run result.

## What was measured

Documents (all local, never fetched at run time):

| document | format | pages |
| --- | --- | --- |
| `…-pg1342.txt` (Gutenberg, Pride & Prejudice, 754 KiB) | text/plain | 1 |
| `…-rfc5322.txt` (119 KiB) | text/plain | 1 |
| `…-commonmark-spec.md` (201 KiB) | text/markdown | 1 |
| `exoplanets.csv` (7.5 KiB) | text/csv | 1 |
| `whatwg-html.html` (151 KiB) | text/html | 1 |
| `wikipedia-calculus.html` (792 KiB) | text/html | 1 |
| `itu-t-p863.pdf` (licensed, 2.3 MiB) | application/pdf | 80 |
| `etsi-ts-103558.pdf` (licensed, 2.7 MiB) | application/pdf | 68 |
| `…-nist-sp-800-53r5.pdf` (5.8 MiB) | application/pdf | 492 |

`wikidata-q42.json` (316.1 KiB) is staged but unsupported — recorded as
an honest skip, never silently dropped.

Backends (2026-10-31 run): `native-text`, `native-markdown`, `native-html`,
`native-csv` (CPU), plus `liteparse`, `pdf-inspector`, and `trafilatura`
where eligible — these were the backends registered in the measured
environment. Measured component versions: `trafilatura` 2.3.1,
`liteparse` 2.17.0, `pdf-inspector` 1.25.2, `openpyxl` 3.1.5. Not installed
and therefore not measured: `pymupdf`, `docling`, `marker-pdf`, `mineru`,
and all OCR extras. Every registered backend eligible for a document's
media type is measured once per document. PyMuPDF was not installed in this
environment, so its three PDF rows record typed `dependency_missing`
instead of crashing; the 2026-09-27 snapshot below measured it while
installed.

Environment (2026-10-31 run): Windows laptop, CPython 3.14.2, CPU only;
the light set installed (pypdf and huggingface-hub are core; `liteparse`,
`trafilatura`, `pdf-inspector`, `openpyxl` as needed).
No GPU work is included in this snapshot — see [Gaps](#honest-gaps).

## Reproduce

```bash
parsecraft benchmark path/to/tests/downloads/* --no-ocr -o <scratch-dir>/
```

Before measuring, refresh editable metadata against the measured commit
(`uv sync --no-progress --reinstall-package parsecraft`) and assert
`importlib.metadata.version("parsecraft")` names it — a stale install
label falsifies the report's provenance (this bit the 2026-10-31 trial:
the first invocation silently ran a different venv). Stage trial outputs in
scratch and review them before copying anything into `docs/benchmarks/`.

The report writers are deterministic: stable key and row ordering, fixed
rounding, LF newlines, no wall-clock fields. Re-running on the same inputs
yields the same schema and row set — the measured values (elapsed, pages/s,
peak bytes) are run data and will vary. Benchmarking goes straight to the
backend's `convert` and never reads the conversion cache, which would report
~0 s and falsify the measurement.

`pymupdf` extraction needs the `pymupdf` extra (AGPL); without it the
report records a typed `dependency_missing` row instead of crashing.

## Results (2026-10-31, `a546d73`)

The full harness output is committed alongside this page:
[`benchmark.md`](benchmark.md) and [`benchmark.json`](benchmark.json)
(package `2026.10.13.post53.dev0+a546d73`, 17 rows over 9 documents,
8 backends: 14 successful conversions + 3 typed `dependency_missing`
rows, no other failures).

Headlines (CPU, 2026-10-31; values as committed):

| document | backend | elapsed | pages/s | coverage |
| --- | --- | --- | --- | --- |
| pg1342.txt (754 KiB) | native-text | 0.005 s | 194.3 | 0.988 |
| rfc5322.txt | native-text | 0.013 s | 77.6 | 0.976 |
| commonmark-spec.md (201 KiB) | native-markdown | 0.683 s | 1.5* | 0.717 |
| exoplanets.csv (7.5 KiB) | native-csv | 0.002 s | 466.6 | 1.212† |
| whatwg-html.html | native-html | 0.166 s | 6.0* | 0.271 |
| wikipedia-calculus.html | native-html | 0.474 s | 2.1* | 0.107 |
| etsi-ts-103558.pdf (68 pp) | liteparse | 2.701 s | 25.2 | 0.925 |
| itu-t-p863.pdf (80 pp) | liteparse | 3.014 s | 26.5 | 0.978 |
| nist-sp-800-53r5.pdf (492 pp) | liteparse | 65.343 s | 7.5 | 0.877 |

Also measured but not planner-selected: `pdf-inspector` on all three PDFs
(0.369 s / 184.3 p/s / 0.884; 4.300 s / 18.6 / 0.982; 6.863 s / 71.7 /
1.010†) and `trafilatura` on both HTML pages (coverage 0.021 / 0.073).
PyMuPDF contributes three `dependency_missing` rows — listed, not measured.

\* Single-page formats: "pages/s" is one page over elapsed time — read the
elapsed figure instead.
† Coverage above 1.0 needs the ratio reading — see
[How to read coverage](#how-to-read-text_coverage).

### How to read `text_coverage`

`text_coverage` is the ratio of emitted chunk characters to the analyzer's
source character count — an unbounded emitted/source ratio, not an accuracy
score. Two rows exceed 1.0 in this run: `exoplanets.csv` at 1.212 and the
NIST row via `pdf-inspector` at 1.010. Segmentation and projection markup
are possible influences; fidelity is unjudged here — the ratio is disclosed,
not explained away. Magnitude ordering across backends is comparable (one
definition).

### Measurement notes

- Timing and memory come from the benchmark runner: it starts `tracemalloc`
  before timing, so peak bytes cover traced Python allocations only — not
  total RSS and not native/GPU allocations — and elapsed times include the
  tracing overhead. Compare backends within one run, not against
  wall-clock expectations.
- Three rows carry typed `dependency_missing` failures (PyMuPDF not
  installed); every other row loaded, so no `selected: true` + failure
  combination appears.

Skips in this run: `wikidata-q42.json` only (unsupported source suffix).
No historical PyMuPDF number is presented as a new-run result anywhere on
this page.

## 2026-09-27 snapshot

The 2026-09-27 harness output is preserved verbatim under
[`benchmark-2026-09-27.md`](benchmark-2026-09-27.md) and
[`benchmark-2026-09-27.json`](benchmark-2026-09-27.json)
(package `2026.9.11.post9.dev0+917dbdb`, 11 rows; `exoplanets.csv` was then
an honest skip — unsupported suffix — and is measured in the 2026-10-31 run
above).

Headlines (CPU pass, 2026-09-27; values as committed in
[`benchmark-2026-09-27.json`](benchmark-2026-09-27.json)):

| document | backend | elapsed | pages/s | coverage |
| --- | --- | --- | --- | --- |
| itu-t-p863.pdf (80 pp) | pymupdf | **0.612 s** | **130.8** | 0.999 |
| nist-sp-800-53r5.pdf (492 pp) | pymupdf | **4.002 s** | **122.9** | 0.995 |
| etsi-ts-103558.pdf (68 pp, image-heavy) | pymupdf | 6.468 s | 10.5 | 0.963 |
| itu-t-p863.pdf (80 pp) | liteparse | 5.672 s | 14.1 | 1.017 |
| nist-sp-800-53r5.pdf (492 pp) | liteparse | 115.647 s | 4.3 | 0.904 |
| etsi-ts-103558.pdf (68 pp) | liteparse | 6.011 s | 11.3 | 1.012 |
| pg1342.txt (754 KiB) | native-text | 0.002 s | 409.9 | 0.988 |
| rfc5322.txt | native-text | 0.009 s | 116.6 | 0.976 |
| commonmark-spec.md (201 KiB) | native-markdown | 0.739 s | 1.4* | 0.717 |
| whatwg-html.html | native-html | 0.873 s | 1.1* | 0.271 |
| wikipedia-calculus.html | native-html | 2.460 s | 0.4* | 0.106 |

\* Single-page formats: "pages/s" is one page over elapsed time — read the
elapsed figure instead. Coverage may exceed 1.0 (different segmentation is
one possible influence; fidelity is unjudged).

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
| Full-corpus OCR, all four models (re-affirmed) | 2026-10-31 | Cost/time only — unchanged from 2026-09-28; no GPU env in the 2026-10-31 run | same OCR command above |
| Full-corpus docling (re-affirmed) | 2026-10-31 | Runtime budget — unchanged from 2026-09-28; not an automated check | same docling command above |
| PyMuPDF extraction on the 2026-10-31 corpus | 2026-10-31 | Scope/env: PyMuPDF deliberately absent from the measured env to record the honest `dependency_missing` path; historical numbers stay in the 2026-09-27 snapshot | `uv pip install "parsecraft[pymupdf]"` then the [run recipe](#reproduce) |
| GPU VRAM measurement | 2026-10-31 | Out of scope: peak bytes are `tracemalloc` host-Python allocations; no GPU env in this run | — |
| Full-corpus docling | 2026-09-28 | Runtime budget: docling is ≈ 8–15 s/page warm on this laptop → ≈ 1.8 h for NIST SP 800-53r5 alone, and the incident shape (>1 h on 113 pp) says hours. Not an automated check | [Full-corpus docling](#full-corpus-docling-command) |
| Quantised variants (int8 / 4-bit) | 2026-09-28 | Out of scope by design: quantisation is a backend-installation choice, not a harness dimension | — |

### Full-corpus OCR command

All four OCR extras share one `transformers>=5.17,<6` window, so the full run is
a single pass in a single environment. The command prints JSON on stdout and
never overwrites the committed CPU report; PDF rasterisation uses base
pypdfium2, so no extra is needed.

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
cancelled* (≈ >31.9 s/page, never finished). All figures in this section
are the 2026-09-27 snapshot (PyMuPDF measured while installed), not the
2026-10-31 run.

Measured here on the staged ITU-T P.863 artifact (80 pages, same document
family — the staged file is not byte-identical to the incident's 113-page
bundle): **pymupdf extracts 80 pages in 0.612 s (≈ 7.7 ms/page)**, and
the 492-page NIST SP 800-53r5 in 4.00 s (122.9 pages/s). That is more than
three orders of magnitude below the incident's per-page cost — with honest
caveats: this is text-layer extraction into typed chunks, not Docling's
full layout/table pipeline. The routing design leans exactly on this gap: run the cheap
native pass first, escalate to OCR/layout backends only for pages whose
signals say native extraction cannot do the job (the cold-cache budget for a
whole document, escalations included, is 15 minutes).

## Docling vs pymupdf (bounded head-to-head, 2026-09-28)

The gap the incident cross-check only inferred is now measured on **2-page
fixtures** of the three corpus PDFs (docling 2.130.0, 2026-09-28).
Environment: Windows laptop, CPython 3.13, CPU only. Docling ran its **default
pipeline untuned** (layout, tables, and its bundled RapidOCR); `pymupdf` used
PyMuPDF. Both were measured with `parsecraft benchmark`.

| fixture (2 pp) | docling | pymupdf | ratio |
| --- | --- | --- | --- |
| `itu-t-p863` (of 80) | 20.6 s | 0.014 s | ≈ 1470× |
| `etsi-ts-103558` (of 68) | 30.5 s | 2.98 s | ≈ 10× |
| `nist-sp-800-53r5` (of 492) | 15.5 s | 0.024 s | ≈ 650× |

- Docling's **first** conversion in a process is far slower — 191.9 s for the
  p863 fixture — because it loads its layout/OCR models. The table is warm; its
  peak Python memory is 158–405 MB against 0.03–15 MB for `pymupdf`.
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
- **VRAM is not measured here, and timings carry tracing overhead.** Peak
  bytes are `tracemalloc` (host Python allocations); GPU VRAM and native
  allocations are out of scope. Elapsed times include the runner's tracing
  overhead — compare backends within one run, not against wall-clock
expectations.
- **No quantisation variants** were benchmarked — model quantisation stays
  a backend-installation concern, not a harness dimension.
- **`wikidata-q42.json` is still skipped** (unsupported source suffix,
  recorded in the report, not hidden). `exoplanets.csv` was a skip in the
  2026-09-27 snapshot and has been measured since 2026-10-31 (`native-csv`,
  one TABLE chunk, coverage 1.212 — an unbounded ratio, see
  [How to read coverage](#how-to-read-text_coverage)).
- **HTML coverage is a structure proxy.** `native-html` emits headings,
  paragraphs, lists, tables, code, and quotes; raw inline markup dominates
  the analyzer's character count, so 0.11–0.27 coverage is typical for
  markup-heavy pages; whether anything is lost is unjudged.
- `itu-t-p863.pdf` showed `selected: -` in the 2026-09-27 report: its page signals routed some pages
  to OCR intents while no OCR backend was installed in this environment, so
  auto-mode planning correctly declined (`selected` is null) — the direct
  per-backend measurement still ran.

## Artifacts

| file | contents |
| --- | --- |
| [`benchmark.md`](benchmark.md) | 2026-10-31 harness Markdown (`a546d73`, 17 rows) |
| [`benchmark.json`](benchmark.json) | 2026-10-31 harness JSON, schema-guarded |
| [`benchmark-2026-09-27.md`](benchmark-2026-09-27.md) | 2026-09-27 harness Markdown, preserved verbatim |
| [`benchmark-2026-09-27.json`](benchmark-2026-09-27.json) | 2026-09-27 harness JSON, preserved verbatim |
| [`benchmark-ocr-pass-a.json`](benchmark-ocr-pass-a.json) | GPU pass A (ovis + qianfan, transformers 5.17.0), 12 rows — schema-guarded |
| [`benchmark-ocr-pass-b.json`](benchmark-ocr-pass-b.json) | GPU pass B (tele + unlimited, transformers 4.57.1), 15 rows — schema-guarded |
| [`benchmark-docling-vs-native.json`](benchmark-docling-vs-native.json) | docling vs pymupdf, 2-page fixtures, 6 rows |
