---
title: Backend catalog
---

A backend turns one source format into IR pages. Each tool stays isolated behind
a `DocumentBackend`, and ParseCraft translates its result into the typed IR — the
protocol and registry are described in [Backends](../architecture/backends.md).

This page catalogs every registered backend. The **Backend (extra)** column names
the optional install that carries it — a backend with no extra either ships in
the base install or is a bring-your-own dependency. Each third-party tool keeps
its own licence.

!!! warning "Copyleft extras are opt-in"
    `pymupdf` extraction needs PyMuPDF
    (**AGPL-3.0-or-commercial**, `pdf` extra), and `pandoc` needs the Pandoc
    binary (**GPL-2.0-or-later**). These are never core or dev dependencies.
    The extra is named `pdf` but now also carries the DOCX/PPTX office
    importers — one library, one licence decision.
    OCR PDF input does **not** need PyMuPDF — the base install rasterizes
    through the permissive `pypdfium2`, so no copyleft sits on the
    default OCR path.
    Installing one is your own licence decision.

!!! warning "MinerU is a conditional-licence opt-in"
    `parsecraft[mineru]` carries a **conditional** licence surface, not a
    copyleft one — the distinction matters, so it gets its own statement.
    Installing the extra is your consent to these terms:
    - **Code:** `LicenseRef-MinerU-Open-Source-License` — Apache-2.0 **plus**
      commercial thresholds (>100M MAU or >USD 20M revenue), an online-service
      attribution duty, and termination-without-notice.
    - **VLM checkpoint and torch pipeline kit:** Apache-2.0
      (`MinerU2.5-Pro-2605-1.2B`, `MinerU-4_models_torch`).
    - **Default Windows/CPU GGUF engine and the ONNX kit:** **no licence
      declared** (`jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF`,
      `MinerU-4_models_onnx`). Unknown terms are stated, not assumed — the
      GGUF engine is what a default Windows install downloads, so this is not
      an exotic path.

## All backends

Formats are written extension-first with the declared MIME type in parentheses,
because that is the vocabulary the code speaks — `supported_formats` is MIME,
and the extension is the suffix the host layer joins onto it.

| Backend (extra) | Formats | Upstream / licence | Strengths | Weaknesses |
| --------------- | ------- | ------------------ | --------- | ---------- |
| `native-text` — base install | `.txt` (`text/plain`) — plus every other suffix the MIME table maps to `text/plain` (`.py`, `.rst`, `.toml`, `.log`, `.tex`, …) | in-package — MIT | Zero dependencies, fully offline, deterministic | Paragraphs only; no headings, tables, or layout |
| `native-markdown` — base install | `.md`, `.markdown` (`text/markdown`) | [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — MIT | CommonMark token structure: headings, lists, tables, code, quotes | CommonMark only; no non-CommonMark dialects |
| `native-html` — base install | `.html`, `.htm` (`text/html`) | in-package (stdlib `html.parser`) — MIT | Zero dependencies; headings, lists, tables, code, quotes | Simplistic parser; scripts and styles skipped; not a full HTML5 engine |
| `pymupdf` — `pypdf` (core, analysis) + `parsecraft[pdf]` (extraction) | `.pdf` (`application/pdf`), `.docx` (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`), `.pptx` (`application/vnd.openxmlformats-officedocument.presentationml.presentation`) | [`pypdf`](https://pypi.org/project/pypdf/) — BSD-3-Clause; [`pymupdf`](https://pypi.org/project/PyMuPDF/) — **AGPL-3.0-or-commercial** | Fast native text and layout extraction for PDF and office alike: monospace runs become fenced `code` chunks with recovered relative indentation and joined wrapped lines, and DOCX/PPTX need no second dependency. Analysis of every declared format works without the AGPL extra | Extraction of every declared format requires the AGPL `pdf` extra (the name predates the office support); scanned PDFs still need OCR; `.xlsx` is deliberately **not** declared — pymupdf keeps only numeric cells and drops strings; the `openpyxl` backend carries spreadsheets |
| `openpyxl` — base install | `.xlsx` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) | [`openpyxl`](https://openpyxl.readthedocs.io/) — MIT (`et_xmlfile`, MIT) | The spreadsheet backend, and a **base install**: one typed `TABLE` chunk per worksheet carrying the real cell grid as `rows`, the sheet name in `metadata["sheet"]`, trailing blanks trimmed; no extra, no copyleft, no weights, no GPU, no second dependency | A workbook is one logical page, so `supports_page_ranges`/`supports_multi_page` are off; `.xls`/`.xlsm` are not declared; cells are read as stored (`data_only`), so a formula yields its cached value |
| `pandoc` — base install, engine-gated | `.docx` (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`), `.pptx` (`application/vnd.openxmlformats-officedocument.presentationml.presentation`), `.xlsx` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, read-only), `.odt` (`application/vnd.oasis.opendocument.text`), `.rtf` (`application/rtf`), `.epub` (`application/epub+zip`) | [pandoc.org](https://pandoc.org/) — **GPL-2.0-or-later** binary, MIT wrapper | Broad office and e-book coverage through one wrapper | Needs the external **Pandoc binary**; `.ods`/`.odp` are unsupported by pandoc 3.11; copyleft gate |
| `liteparse` — base install | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`), `.tif`, `.tiff` (`image/tiff`) | [`run-llama/liteparse`](https://github.com/run-llama/liteparse) — Apache-2.0 | Broad and permissive; no copyleft gate | Office/ODF formats require a system LibreOffice; `.html` is not supported; larger extra dependency surface |
| `docling` — `parsecraft[docling]` (`docling>=2.130`) | `.pdf` (`application/pdf`), `.html` (`text/html`), `.md` (`text/markdown`), `.txt` (`text/plain`) | [`docling`](https://pypi.org/project/docling/) — MIT | Layout, tables, and reading order | Heavy dependency graph; office/image formats are registered upstream but undeclared pending conversion verification; a 113-page PDF ran >1 h |
| `trafilatura` — `parsecraft[trafilatura]` (`trafilatura>=2.3.1`) | `.html` (`text/html`) | [`adbar/trafilatura`](https://github.com/adbar/trafilatura) — Apache-2.0 | Readability-class main-content extraction: starts at the article H1, keeps tables as Markdown tables, drops link/image/formula syntax; **asset-free** — no weights, so no download notice and no offline carve-out | Under-extracts very long single-page specs (RFC-class counter-case below); a single-row table without `<thead>`, and a single-line `<pre><code>`, do not survive as typed blocks |
| `pdf-inspector` — base install | `.pdf` (`application/pdf`) | [`firecrawl/pdf-inspector`](https://github.com/firecrawl/pdf-inspector) — MIT | Fastest verified text-PDF path: Rust/PyO3 extraction straight to Markdown, no ML models and no OCR runtime loaded; classifies text-based vs scanned PDFs before extraction | PDF only (no docx/pptx/xlsx path exists upstream); no OCR, so scanned pages need an OCR backend; ships as a prebuilt Rust extension wheel only for `cp38-abi3` — Linux x86_64/aarch64, macOS Intel/ARM, Windows x64 (other platforms build from source and need a Rust toolchain); not yet benchmarked |
| `marker` — none; `marker-pdf` 2.0.0 is a **bring-your-own dependency** | `.pdf` (`application/pdf`) | [`datalab-to/marker`](https://github.com/datalab-to/marker) — Apache-2.0; model weights under OpenRAIL-M (restricted use) | High-fidelity PDF→Markdown: figures, tables, math, reading order; page-aware pagination | PDF only — DOCX/XLSX/PPTX/HTML/EPUB route through weasyprint, which needs GTK/Pango system libraries and is unverified on Windows; model weights are fetched at converter creation (network I/O); no extra is declared, so the consumer's own dependency graph installs `marker-pdf` |
| `mineru` — `parsecraft[mineru]` (`mineru>=4.0.11,<5`); **GIL builds only**, see [Interpreter and platform availability](#interpreter-and-platform-availability) | `.pdf` (`application/pdf`) | [`opendatalab/MinerU`](https://github.com/opendatalab/MinerU) — Apache-2.0 code; weights conditional (see the warning above) | VLM layout, tables, equations, and reading order; **text PDFs run weight-free** at flash effort (no checkpoint download, no VRAM) | Heavy 55-package web stack; page-range input base is delegated downstream and post-filtered; not yet benchmarked against siblings; conditional licence |
| `ocr-ovis` — `parsecraft[ocr]` | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`) | [`ATH-MaaS/OvisOCR2`](https://huggingface.co/ATH-MaaS/OvisOCR2) — Apache-2.0 | ~0.9 B; formulas and tables; vLLM wrapper (optional `vllm` extra) | GPU required (~1 GB VRAM); not yet benchmarked |
| `ocr-tele` — `parsecraft[ocr]` | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`) | [`StarDoc-AI/TeleOCR`](https://huggingface.co/StarDoc-AI/TeleOCR) — Apache-2.0 ([code](https://github.com/caipeng328/TeleOCR)) | Geometry-aware; camera captures | GPU required (~1.2 GB VRAM); not yet benchmarked |
| `ocr-unlimited` — `parsecraft[ocr]` | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`) | [`baidu/Unlimited-OCR`](https://huggingface.co/baidu/Unlimited-OCR) — MIT | Long-horizon multi-page documents | 3 B; quantization required under an 8 GB budget; not yet benchmarked |
| `ocr-qianfan` — `parsecraft[ocr]` | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`) | [`baidu/Qianfan-OCR`](https://huggingface.co/baidu/Qianfan-OCR) — Apache-2.0 (`Layout-as-Thought`) | Strong element, box, and reading-order control | 4 B; quantization required under an 8 GB budget; not yet benchmarked |
| `ocr-tesseract` — base install | `.pdf` (`application/pdf`), `.jpg`, `.jpeg` (`image/jpeg`), `.png` (`image/png`), `.tif`, `.tiff` (`image/tiff`) | [`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast) — Apache-2.0 | No extra, no GPU, no model weights; runs anywhere the OS engine is installed | Needs the `tesseract` OS binary (parsecraft never installs it); weaker layout and formula handling than the VLM backends |

## Native (in-package)

The native backends are registered through
`[project.entry-points."parsecraft.backends"]` and appear in
`parsecraft backends`.

### What the `native-` prefix means — and a proposal to retire it

`native-` began as "no third-party runtime dependency". That is no longer what
the word communicates, and it stopped being true the moment `native-pdf`
spent its life carrying PyMuPDF: the family now holds exactly
`native-text`, `native-markdown`, and `native-html`, all three of which are
**first-party** — the parsing rules are parsecraft's own code (in-package, or
stdlib `html.parser`), with no upstream parser delegated to. A reader sees
the word and reasonably asks which native platform, or which native code.

A more precise name for the category is **builtin**:

| Candidate | Reads as | Problem |
| --------- | -------- | ------- |
| `native-` (current) | platform- or language-native | ambiguous; users ask "native to what?" |
| `builtin-` | ships inside parsecraft | none found |
| `first-party-` | our own code, our licence | accurate but long for a CLI token |
| `internal-` | private implementation detail | implies not-for-use, which is wrong |

`builtin-text`, `builtin-markdown`, `builtin-html` state the property that the
remaining three actually share, and match the "base install" promise the docs
already make elsewhere. `ocr-tesseract` is **base install** but not builtin
(it drives an OS binary), which is the distinction the word should carry.

The rename is deliberately **not** done in this document's current tree: it is
a name change with its own cost (entry points, tests, every doc, and operator
muscle memory), and it deserves its own change rather than riding along with a
docs update. The proposal is recorded here so the decision is either taken or
declined on its merits.

### What each backend actually extracts

Typed structure in the IR is narrower than "the document had a table in it".
A capability is only marked **typed** when the converter emits a structured
chunk with machine-readable content — `TABLE` chunks carrying `rows`
(tuples of cell texts, separator row excluded), `FIGURE` chunks carrying image
metadata, and so on. Content that survives only as prose or as Markdown text is
marked **text**, and an empty cell means the backend drops it during
conversion. This distinction matters for the router: only typed `rows` can be
counted, filtered or projected without re-parsing text.

| Backend | Tables | Figures / images | Equations |
| ------- | ------ | ---------------- | --------- |
| `native-text` | — | — | — |
| `native-markdown` | typed | — | — |
| `native-html` | typed | — | — |
| `pymupdf` | — | — | — |
| `openpyxl` | typed (per sheet) | — | — |
| `pandoc` | text | text | text |
| `liteparse` | unverified | unverified | unverified |
| `docling` | typed | unverified | unverified |
| `trafilatura` | typed | — (image syntax dropped) | — (formula syntax dropped) |
| `pdf-inspector` | unverified | — | — |
| `marker` | text | text | text |
| `mineru` | typed (kind only) | unverified | unverified |
| `ocr-ovis` … `ocr-qianfan` | — | — | — |
| `ocr-tesseract` | — | — | — |

**No backend extracts equations as typed chunks.** There is no `EQUATION`
chunk kind in the IR, so equation markup reaching any backend survives only as
text inside a paragraph. If typed equations matter to a downstream consumer,
that is a missing IR feature, not a backend choice — see
`src/parsecraft/ir/models.py` for the chunk vocabulary.

`native-markdown`/`native-html`/`trafilatura` tables are typed through the
shared Markdown projection, which is also where their one shared limit lives:
a table row whose cells are present but whose separator row is missing does
not become a `TABLE` chunk (see the trafilatura quirks below).

`mineru` emits `TABLE` chunks but the structured `rows` are not pinned by a
test yet, so the cell says "kind only". `docling` is the strongest typed-table
case: rows are built from the vendor's own cell grid and a test pins cell
agreement with the Markdown content.

`pandoc` and `marker` pass their structure through as Markdown, so tables,
figures and equations all survive **as text** in that projection — visible in
the output, but not typed in the IR.

**Unverified is not a claim.** Those cells are the honest state of the test
suite, not a measurement of the upstream library: `liteparse`,
`pdf-inspector`, `marker` and `docling` all handle more structure than their
cells show, but no test pins a typed chunk for it. Closing an unverified cell
means adding a test that asserts the typed chunk, not asserting a datasheet.

### Two distinct optional-dependency shapes

`marker` and `mineru` are the two **different** ways an optional dependency can
be offered, and the difference is the point:

- **marker — backend + entry point, no extra.** The consumer owns the
  dependency; parsecraft's lock never resolves it.
- **mineru — the extra IS the opt-in, and parsecraft owns the pin.**
  `parsecraft[mineru]` resolves the whole 55-package stack and states the
  licence terms at the point of installation.

The two must not read identically: a no-extra shape hides the dependency from
the installer, while an extra shape makes the terms a condition of installing.

## External converters

MinerU fetches its own weights, so its cache placement matters: `MINERU_HOME` is
a revision directory of the managed model cache
(`<cache>/models/<slug>/<revision>`), which makes those weights visible to
`parsecraft models list` and reclaimable by `models clean`/`remove` instead of
accumulating unseen in `~/.mineru`.

!!! note "Readability-class under-extraction, and two shape quirks"
    `trafilatura` trades completeness for cleanliness, and the counter-case is
    measured: on a 1.2 MB single-page specification it kept **5,592 of 503,420
    characters**, having classified the page as a summary/landing page and
    dropped the body (`favor_recall=True` does not recover it). There is no v1
    mitigation — reach for `native-html` when completeness matters more than
    chrome removal.

```text
Two shape quirks, both verified against 2.3.1, are stated here rather than
papered over with heuristics: a single-row `<table>` with no `<thead>` is
emitted without a `|---|` separator, so it is not a CommonMark table and
yields no `table` chunk; and a single-line `<pre><code>` comes back as an
inline code span, yielding a `paragraph` chunk rather than `code`.
Multi-line code yields a fenced block and does project to `code`.

A third quirk, same voice: trafilatura sometimes passes raw HTML straight
through (an `<img …>` survives next to a code fence), and the shared Markdown
projection has no block for that — it lands as `unknown`. Measured: exactly
one such block across the four verified fixtures (the MDN page).
```

## OCR / document-VLM (GPU)

Model pins and licences are recorded once in
`src/parsecraft/backends/ocr/_models.py`. The adapters are implemented but **not
yet benchmarked with weights** — GPU runs are pending.

All four models share **one** extra — `parsecraft[ocr]` (`transformers` window
`>=5.17,<6`, `torch>=2.5`, `torchvision`, `accelerate` and the small model-code
helpers, declared once in `pyproject.toml`, the authoritative source). The model
*weights* are downloaded assets, not packages, so one feature extra covers every
model. The per-model card pins (`transformers==4.57.1` and similar) are advisory
origin only: the project range supersedes them, and the TeleOCR/Unlimited models
ship as local vendored modeling code so they load on the unified major.
modeling code so they load on the unified major.

The OCR extras are **not** mutually exclusive: every extra installs alongside
the others.

GPU `torch` needs no manual install step: a checkout sync resolves the CUDA
builds on Windows/Linux automatically. See [GPU
torch](../getting-started/installation.md#gpu-torch) for the platform matrix and
the driver caveat.

The optional `vllm` extra is **Linux/WSL2-only**: its marker
(`vllm>=0.11 ; sys_platform != 'win32'`) keeps Windows installs resolvable, but
the vLLM runtime does not run there, so those backends use the default
`transformers` runtime on Windows.

### The `auto` convenience extra

`parsecraft[auto]` is the **routing-flags feature group**: `typesafe-sdk`
(the judge providers) and `pdf-inspector` (the `--classifier` provider), so one
install covers the whole auto-routing flags story. It deliberately does **not**
include `pdf` or any OCR backend — routing flags and heavy extraction are
separate install decisions (ADR-0009).
Judge credentials still come from the environment (`TYPESAFE_API_KEY`,
`OPENCODE_API_KEY`; the local Ollama endpoint needs none) — see
`parsecraft judges`.

A host whose installed `transformers` falls outside the shared window gets a
typed `UnsupportedDependencyVersionError` before any model load — the detail
names the package, the actual version, and the required range; it is never a
crash inside weight loading, and it is distinct from a missing extra.

All OCR backends accept `application/pdf`, `image/jpeg`, and `image/png`, and
**PDF input needs no extra**: the base install carries the permissive
[`pypdfium2`](https://pypi.org/project/pypdfium2/) raster engine (BSD-3-Clause +
Apache-2.0) with Pillow as its image bridge, so OCR on a scanned PDF stays off
the AGPL path. PyMuPDF remains available behind
`parsecraft[pdf]` for `pymupdf` extraction.

Every backend declares `supported_formats` as **MIME types** — one
vocabulary shared with `RoutingConstraints.formats`, which is built from the
source's media type. The OCR backends declare `application/pdf`,
`image/png`, and `image/jpeg` (the payloads their page-access layer accepts).

Model licences apply to the weights; the ParseCraft adapter code is MIT.

### `ocr-tesseract` — the base-install OCR path

It is in the default install because its dependency is an **OS package**, not a
wheel: install `tesseract` with your platform's package manager and the backend
appears. ParseCraft **never downloads the engine** — no upstream publishes a
portable, hash-published release to verify against (the official project ships a
Windows *installer*), so a pull path would be unauditable. A missing engine is a
typed `dependency_missing` failure naming the package for your platform
(`apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu`,
`brew install tesseract`, or choco/winget on Windows).

Tesseract reads page images, so a PDF is rasterized at 300 dpi first.
Language data
(`eng`, `deu`) comes from your system tessdata when it is present and complete,
and otherwise from a pinned, checksum-verified download into the managed cache.

**Formula handling is the known limitation.** The `equ` model ships with the
pushed set so it is available, but v1 never selects it automatically: page-type
dependent engine selection would be a new routing primitive for a bounded gain,
and `equ` recognition is weak enough that promising it would overstate what this
backend does. For formula-critical documents use the heavyweight `ocr-*` extras,
which are geometry-aware.

## Interpreter and platform availability

Some extras cannot resolve on every interpreter and platform combination, and
the failure reaches you rather than a build: PEP 508 has no free-threaded
marker, so such a dependency cannot be gated out of a bulk `--all-extras`
install.

| Extra | Availability |
| ----- | ------------ |
| `mineru` | **GIL builds only.** `mineru` → `onnxruntime` (unconditional); onnxruntime 1.31.0 publishes `cp314t` wheels for **manylinux only** — no free-threaded macOS/Windows wheel and no sdist, so `uv sync --all-extras` fails there. |
| any extra pulling `onnxruntime` | **Free-threaded Linux only** — same mechanism. |
| `vllm` | Linux/WSL2 only — gated by a `sys_platform != 'win32'` marker. |

A matrix-blocked engine that must stay installable uses a **bring-your-own
dependency group** instead of a declared extra — no
`[project.optional-dependencies]` entry, so `--all-extras` never sees it. See
[Two distinct optional-dependency shapes](#two-distinct-optional-dependency-shapes).

## Languages

Backends declare BCP-47 tags in `capabilities.languages`; an empty tuple means
no claim. A `RoutingConstraints.language` request only narrows a declaration —
it never excludes a language-agnostic backend (see
[Language](../architecture/routing.md#language-optional)).

| Backend | Declared languages |
| ------- | ------------------ |
| `native-text`, `native-markdown`, `native-html`, `pymupdf` | agnostic (no claim) |
| `liteparse` | agnostic (no claim) |
| `ocr-tele` | `zh`, `en` |
| `ocr-ovis`, `ocr-unlimited`, `ocr-qianfan` | agnostic (multilingual) |
| `ocr-tesseract` | `eng`, `deu` |
| `pandoc`, `docling`, `pdf-inspector`, `trafilatura` | agnostic (no claim) |
| `mineru` | agnostic (no claim) |

## Hosts: suffixes

A host that fronts ParseCraft asks "which suffixes can this backend ingest?"
instead of re-deriving the extension→MIME join. That projection is public:

```python
from parsecraft.backends import BackendDescriptor, default_registry

default_registry.suffixes()                        # every ingestible suffix, installed backends only
default_registry.suffixes(installed_only=False)    # ignore which extras are installed
default_registry.suffixes_for(descriptor)          # one backend's suffixes
default_registry.suffixes_for(descriptor, installed_only=False)
```

The result is a **projection**, never a second table: it is
`pipeline.MEDIA_TYPES` joined with each descriptor's declared
`supported_formats`, so a suffix exists only where the MIME table has it and a
backend claims that MIME. `parsecraft backends --json` carries the same
`suffixes` array per descriptor.

The projection is the *declared* answer. GPU/VRAM/`--no-ocr` eligibility remains
`routing.rules.is_hard_eligible`'s job — it needs a probed host — so a host that
wants full eligibility joins this projection with that funnel rather than
re-implementing it.

## External benchmarks

parsecraft ships its own benchmark runner (`parsecraft benchmark`) over its own
fixture corpus, which measures **this** codebase but cannot answer "are we
competitive?". These are the public benchmarks a reader can check independently
— and, because each of them publishes a dataset and a scoring script, the ones
most likely to feed a decision model later.

| Benchmark | Publisher | What it measures | Notes for a parsecraft reader |
| --------- | --------- | ---------------- | ----------------------------- |
| [olmOCR-Bench](https://github.com/allenai/olmocr/tree/main/olmocr/bench) | AllenAI | ~7,000 machine-checkable test cases over 1,400 documents across 7 categories (ArXiv, old scans, math, tables, headers/footers, multi-column, long tiny text) | Unit-test-style assertions rather than edit distance, so a reordered-but-correct parse is not penalised; the authors explicitly reject soft metrics. Results are reproduced in-house except those marked `*`, which are reported by the model's own authors. |
| [OmniDocBench](https://github.com/opendatalab/OmniDocBench) | OpenDataLab (CVPR 2025) | Fine-grained document parsing across 9 page sources and 19 layout categories | The benchmark MinerU's own numbers are usually reported against, so it is the natural comparison for the `mineru` row. |
| [Docling benchmarks](https://docling.ai/benchmarks) | Docling project | Layout, tables and reading order over an 89-PDF / 4,008-page dataset with 1,842 tables and 4,676 pictures | Worth reading for its **reproducibility policy** even if the numbers age: dataset, release, configuration, hardware, metric, throughput, baselines, limitations, and a script that regenerates the table. That is the shape parsecraft's own benchmark output should eventually match. |
| [parser-bench](https://github.com/SeanLikesData/parser-bench) | Third-party | Extraction **quality** across 11 real-world documents: content accuracy, reading order, table fidelity, column handling, watermark separation, numeric precision | Compares PyMuPDF, pdfplumber, pypdf and liteparse — the same "cheap deterministic parser" family parsecraft's `pymupdf` backend sits in. |
| **opendataloader-bench** · **ParseBench** | OpenDataLab · third-party | Two further public scores for model-free PDF→Markdown pipelines | Names retained without links: the only reference located for these two was liteparse's own release announcement, which is not an independent source. Treat any number quoted against them as vendor-reported until the dataset is located at a first-party URL. |

**How to read any of these before trusting them.** A benchmark is only as
strong as the boundary between what it measured and what it claims:

1. **Who ran the numbers.** A vendor reporting its own score (`*` above) is
   weaker evidence than an in-house reproduction.
2. **Which version.** Marker 1.10.1's olmOCR-bench score is not marker's
   current score; the library moves.
3. **Metric family.** Machine-checkable assertions survive a correct reordering;
   edit distance penalises it. They answer different questions.
4. **Hardware and effort.** CPU vs GPU, and whether OCR was enabled, changes
   throughput by an order of magnitude — parsecraft's own measurements record
   `fast`/`balanced` effort for the same reason.

Nothing in this table is a parsecraft result. It is where a reader goes to
check one.

## Choosing a backend

| Document trait | Route | Why |
| -------------- | ----- | --- |
| Digital text PDF | `pymupdf` | Fast native text extraction, no GPU |
| Digital text PDF, throughput first | `pdf-inspector` | Rust extraction with no OCR runtime; falls back to `pymupdf` when the extra is absent |
| Scanned pages or camera photos | `ocr-tele`, `ocr-ovis` | No native text; geometry-aware, formula-capable |
| Multi-page dense tables | `ocr-unlimited` | Long-horizon multi-page handling |
| Formulas and equations | `ocr-ovis` | Formula-aware extraction |
| Broad office formats (docx, pptx) | `pandoc` | Convert to a readable format first |
| Spreadsheet (`.xlsx`) | `openpyxl` | Typed per-sheet tables — the grid a consumer can count and filter; no copyleft, no GPU |
| Layout-heavy, reading order matters | `docling` | Layout, tables, and reading order |
| Layout-heavy PDF, VLM reading order | `mineru` | VLM layout/tables/equations; text PDFs run weight-free at flash effort |
| HTML article page, chrome removal matters | `trafilatura` | Readability-class main-content extraction; use `native-html` when completeness matters more |

Native extraction runs before OCR. OCR is selective and expensive: it is used
per page range, under a time budget, only where analysis shows native text is
missing or unusable.

## Auto mode

A descriptor is a candidate, never a decision: `auto` mode picks the backend per
page, and what each backend declares here — formats, GPU need, extra group,
model asset, languages — is exactly what the eligibility funnel reads. See
[Routing and auto mode](../architecture/routing.md) for the funnel and the judge
seam, and [Auto routing](../architecture/auto-routing.md) for the host inputs.
