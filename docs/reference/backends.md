---
title: Backend catalog
---

A backend turns one source format into IR pages. Each tool stays isolated behind
a `DocumentBackend`, and ParseCraft translates its result into the typed IR — the
protocol and registry are described in [Backends](../architecture/backends.md).

This page catalogs the in-package and external backends. **Extra** is the
optional install that carries the backend; **Availability** separates what ships
today from what is planned. Each third-party tool keeps its own licence.

!!! warning "Copyleft extras are opt-in"
    `native-pdf` extraction needs PyMuPDF
    (**AGPL-3.0-or-commercial**, `pdf` extra), and `pandoc` needs the Pandoc
    binary (**GPL-2.0-or-later**). These are never core or dev dependencies.
    OCR PDF input does **not** need PyMuPDF — the base install rasterizes
    through the permissive `pypdfium2` (ADR-0008), so no copyleft sits on the
    default OCR path.
    Installing one is your own licence decision; see
    [ADR-0003](../adr/0003-optional-agpl-pymupdf.md).

!!! warning "MinerU is a conditional-licence opt-in"
    `parsecraft[mineru]` carries a **conditional** licence surface, not a
    copyleft one — the distinction matters, so it gets its own statement
    ([ADR-0007](../adr/0007-mineru-conditional-licence-optin.md)). Installing
    the extra is your consent to these terms:
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

## Native (in-package)

| Backend | Extra | Formats | Upstream / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ------- | ------------------ | --------- | ---------- | ------------ |
| `native-text` | — | `.txt` (`text/plain`) | in-package — MIT | Zero dependencies, fully offline, deterministic | Paragraphs only; no headings, tables, or layout | Available |
| `native-markdown` | — | `.md` (`text/markdown`) | [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — MIT | CommonMark token structure: headings, lists, tables, code, quotes | CommonMark only; no non-CommonMark dialects | Available |
| `native-html` | — | `.html` (`text/html`) | in-package (stdlib `html.parser`) — MIT | Zero dependencies; headings, lists, tables, code, quotes | Simplistic parser; scripts and styles skipped; not a full HTML5 engine | Available |
| `native-pdf` | `pdf-lite` (analysis) + `pdf` (extraction) | `.pdf` (`application/pdf`) | [`pypdf`](https://pypi.org/project/pypdf/) — BSD-3-Clause; [`pymupdf`](https://pypi.org/project/PyMuPDF/) — **AGPL-3.0-or-commercial** | Fast native text and layout extraction; monospace runs become fenced `code` chunks with recovered relative indentation and joined wrapped lines; analysis works without the AGPL extra | Extraction requires the AGPL `pdf` extra; scanned PDFs need OCR | Available |

The native backends are registered through
`[project.entry-points."parsecraft.backends"]` and appear in
`parsecraft backends`.

### Two distinct optional-dependency shapes

`marker` and `mineru` are the two **different** ways an optional dependency can
be offered, and the difference is the point (ADR-0006 vs ADR-0007):

- **marker — backend + entry point, no extra.** The consumer owns the
  dependency; parsecraft's lock never resolves it. Deferred (ADR-0006).
- **mineru — the extra IS the opt-in, and parsecraft owns the pin.**
  `parsecraft[mineru]` resolves the whole 55-package stack and states the
  licence terms at the point of installation.

The two must not read identically: a no-extra shape hides the dependency from
the installer, while an extra shape makes the terms a condition of installing.

## External converters

| Backend | Extra | Formats | Upstream / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ------- | ------------------ | --------- | ---------- | ------------ |
| `pandoc` | `parsecraft[pandoc]` (`pypandoc` 1.17, MIT) | docx, pptx, xlsx (read-only), odt, rtf, epub — the verified MIME set | [pandoc.org](https://pandoc.org/) — **GPL-2.0-or-later** binary, MIT wrapper | Broad office and e-book coverage through one wrapper | Needs the external **Pandoc binary**; `.ods`/`.odp` are unsupported by pandoc 3.11; copyleft gate | Available |
| `liteparse` | `parsecraft[liteparse]` (`liteparse` 2.14.7, Apache-2.0) | `application/pdf`, `image/jpeg`, `image/png`, `image/tiff` | [`run-llama/liteparse`](https://github.com/run-llama/liteparse) — Apache-2.0 | Broad and permissive; no copyleft gate | Office/ODF formats require a system LibreOffice; `.html` is not supported; larger extra dependency surface | Available |
| `docling` | `parsecraft[docling]` (`docling` 2.130.0, MIT) | `application/pdf`, `text/html`, `text/markdown`, `text/plain` | [`docling`](https://pypi.org/project/docling/) — MIT | Layout, tables, and reading order | Heavy dependency graph; office/image formats are registered upstream but undeclared pending conversion verification; the motivating incident ran >1 h on a 113-page PDF | Available |
| `pdf-inspector` | `parsecraft[pdf-inspector]` (`pdf-inspector` 1.25.2, MIT) | `application/pdf` | [`firecrawl/pdf-inspector`](https://github.com/firecrawl/pdf-inspector) — MIT | Fastest verified text-PDF path: Rust/PyO3 extraction straight to Markdown, no ML models and no OCR runtime loaded; classifies text-based vs scanned PDFs before extraction | PDF only (no docx/pptx/xlsx path exists upstream); no OCR, so scanned pages need an OCR backend; ships as a prebuilt Rust extension wheel only for `cp38-abi3` — Linux x86_64/aarch64, macOS Intel/ARM, Windows x64 (other platforms build from source and need a Rust toolchain); not yet benchmarked in this repo | Available |
| `marker` | none — `marker-pdf` 2.0.0 is a **bring-your-own dependency** (ADR-0006) | `application/pdf` | [`datalab-to/marker`](https://github.com/datalab-to/marker) — Apache-2.0; model weights under OpenRAIL-M (restricted use) | High-fidelity PDF→Markdown: figures, tables, math, reading order; page-aware pagination | PDF only — DOCX/XLSX/PPTX/HTML/EPUB route through weasyprint, which needs GTK/Pango system libraries and is unverified on Windows; model weights are fetched at converter creation (network I/O, see ADR-0006); no extra is declared, so the consumer's own dependency graph installs `marker-pdf` | Bring-your-own dependency (ADR-0006) |
| `mineru` | `parsecraft[mineru]` (`mineru` 4.0.11, Apache-2.0 + conditional terms) | `application/pdf` | [`opendatalab/MinerU`](https://github.com/opendatalab/MinerU) — Apache-2.0 code; weights conditional (see the warning above) | VLM layout, tables, equations, and reading order; **text PDFs run weight-free** at flash effort (no checkpoint download, no VRAM) | Heavy 55-package web stack; page-range input base is delegated downstream and post-filtered; not yet benchmarked against siblings; conditional licence | **GIL builds only** — see [Interpreter / platform availability](#interpreter--platform-availability) |

MinerU fetches its own weights, so its cache placement matters: `MINERU_HOME` is
a revision directory of the managed model cache
(`<cache>/models/<slug>/<revision>`), which makes those weights visible to
`parsecraft models list` and reclaimable by `models clean`/`remove` instead of
accumulating unseen in `~/.mineru`.

## OCR / document-VLM (GPU)

Model pins and licences are recorded once in
`src/parsecraft/backends/ocr/_models.py`. The adapters are implemented but **not
yet benchmarked with weights** — GPU runs are pending.

Each backend has its own extra (`pip install "parsecraft[ocr-ovis]"`), and all
four share **one** `transformers` window — `transformers>=5.17,<6`, declared
once in `pyproject.toml` (the authoritative source) — plus `torch>=2.5`,
`torchvision`, `pillow`, and `accelerate`. The per-model card pins
(`transformers==4.57.1` and similar) are advisory origin only: the project range
supersedes them, and the TeleOCR/Unlimited models ship as local vendored
modeling code so they load on the unified major.

The OCR extras are **not** mutually exclusive. Every extra installs jointly, and
`uv sync -U --all-extras --all-groups --all-packages` is expected to succeed
(root `AGENTS.md` rule 10).

GPU `torch` needs no manual install step: `torch`/`torchvision` are pinned to
the **PyTorch cu128 index** in `pyproject.toml` (`[tool.uv.sources]`, gated
to non-macOS), so a checkout sync gets the CUDA builds on Windows/Linux
automatically — see the [GPU torch](../getting-started/installation.md#gpu-torch)
section for the platform matrix. On a GPU host you may still want the CUDA
runtime's driver, and older GPUs may need a different CUDA wheel; macOS resolves
PyPI's CPU+MPS build because the index publishes no macOS wheels.

The optional `vllm` extra is **Linux/WSL2-only**: its marker
(`vllm>=0.11 ; sys_platform != 'win32'`) keeps Windows installs resolvable, but
the vLLM runtime does not run there, so those backends use the default
`transformers` runtime on Windows.

### The `auto` convenience extra

`parsecraft[auto]` is a **meta-extra**: it pulls in `parsecraft[systemone]`
(the judge providers) and `parsecraft[pdf-inspector]` (the `--classifier`
provider), so one install covers the whole auto-routing flags story. It adds no
dependency of its own, and it deliberately does **not** include `pdf`/`pdf-lite`
or any OCR backend extra — routing flags and PDF extraction are separate
choices, and a convenience extra must not smuggle in a copyleft or GPU stack.
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
the AGPL path (ADR-0008 decisions 10 and 14). PyMuPDF remains available behind
`parsecraft[pdf]` for `native-pdf` extraction.

Every backend declares `supported_formats` as **MIME types** — one
vocabulary shared with `RoutingConstraints.formats`, which is built from the
source's media type. The OCR backends declare `application/pdf`,
`image/png`, and `image/jpeg` (the payloads their page-access layer accepts).

| Backend | Extra | Input | Model / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ----- | --------------- | --------- | ---------- | ------------ |
| `ocr-ovis` | `ocr-ovis` | page images | [`ATH-MaaS/OvisOCR2`](https://huggingface.co/ATH-MaaS/OvisOCR2) — Apache-2.0 | ~0.9 B; formulas and tables; vLLM wrapper (optional `vllm` extra) | GPU required (~1 GB VRAM); not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-tele` | `ocr-tele` | images, scans | [`StarDoc-AI/TeleOCR`](https://huggingface.co/StarDoc-AI/TeleOCR) — Apache-2.0 ([code](https://github.com/caipeng328/TeleOCR)) | Geometry-aware; camera captures | GPU required (~1.2 GB VRAM); not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-unlimited` | `ocr-unlimited` | multi-page | [`baidu/Unlimited-OCR`](https://huggingface.co/baidu/Unlimited-OCR) — MIT | Long-horizon multi-page documents | 3 B; quantization required under an 8 GB budget; not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-qianfan` | `ocr-qianfan` | images | [`baidu/Qianfan-OCR`](https://huggingface.co/baidu/Qianfan-OCR) — Apache-2.0 (`Layout-as-Thought`) | Strong element, box, and reading-order control | 4 B; quantization required under an 8 GB budget; not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-tesseract` | none — in the base install | PDF pages, page images (PNG/JPEG/TIFF) | [`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast) — Apache-2.0 | No extra, no GPU, no model weights; runs anywhere the OS engine is installed | Needs the `tesseract` OS binary (parsecraft never installs it); weaker layout and formula handling than the VLM backends | Available wherever the engine is present — see below |

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

Tesseract reads page images, so a PDF is rasterized at 300 dpi first — see
[ADR-0008 decision 10](../adr/0008-lite-ocr-in-base-install.md). Language data
(`eng`, `deu`) comes from your system tessdata when it is present and complete,
and otherwise from a pinned, checksum-verified download into the managed cache.

**Formula handling is the known limitation.** The `equ` model ships with the
pushed set so it is available, but v1 never selects it automatically: page-type
dependent engine selection would be a new routing primitive for a bounded gain,
and `equ` recognition is weak enough that promising it would overstate what this
backend does. For formula-critical documents use the heavyweight `ocr-*` extras,
which are geometry-aware.

## Interpreter / platform availability

Root `AGENTS.md` rule 10 requires `uv sync -U --all-extras --all-groups
--all-packages` to succeed. The CI `extras` job verifies that on **CPython 3.13
(GIL) on Linux and Windows only** — so an extra that cannot resolve on another
interpreter/platform cell is a *latent* rule-10 violation: CI stays green and
the failure reaches the user. PEP 508 has no free-threaded marker, so such a
dependency cannot be gated out of `--all-extras`.

| Extra | Availability |
| ----- | ------------ |
| `mineru` | **GIL builds only.** `mineru` → `onnxruntime` (unconditional); onnxruntime 1.31.0 publishes `cp314t` wheels for **manylinux only** — no free-threaded macOS/Windows wheel and no sdist, so `uv sync --all-extras` fails there. |
| any extra pulling `onnxruntime` | **Free-threaded Linux only** — same mechanism. |
| `vllm` | Linux/WSL2 only — gated by a `sys_platform != 'win32'` marker, the existing in-repo pattern for a platform-restricted extra. |

A matrix-blocked engine that must stay installable uses a **bring-your-own
dependency group** (ADR-0006) instead of a declared extra — no
`[project.optional-dependencies]` entry, so `--all-extras` never sees it. See
[Two distinct optional-dependency shapes](#two-distinct-optional-dependency-shapes).

## Languages

Backends declare BCP-47 tags in `capabilities.languages`; an empty tuple means
no claim. A `RoutingConstraints.language` request only narrows a declaration —
it never excludes a language-agnostic backend (see
[Language](../architecture/routing.md#language-optional)).

| Backend | Declared languages |
| ------- | ------------------ |
| `native-text`, `native-markdown`, `native-html`, `native-pdf` | agnostic (no claim) |
| `liteparse` | agnostic (no claim) |
| `ocr-tele` | `zh`, `en` |
| `ocr-ovis`, `ocr-unlimited`, `ocr-qianfan` | agnostic (multilingual) |
| `ocr-tesseract` | `eng`, `deu` |
| `pandoc`, `docling`, `pdf-inspector` | agnostic (no claim) |
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

## Choosing a backend

| Document trait | Route | Why |
| -------------- | ----- | --- |
| Digital text PDF | `native-pdf` | Fast native text extraction, no GPU |
| Digital text PDF, throughput first | `pdf-inspector` | Rust extraction with no OCR runtime; falls back to `native-pdf` when the extra is absent |
| Scanned pages or camera photos | `ocr-tele`, `ocr-ovis` | No native text; geometry-aware, formula-capable |
| Multi-page dense tables | `ocr-unlimited` | Long-horizon multi-page handling |
| Formulas and equations | `ocr-ovis` | Formula-aware extraction |
| Broad office formats (docx, pptx, xlsx) | `pandoc` | Convert to a readable format first |
| Layout-heavy, reading order matters | `docling` | Layout, tables, and reading order |
| Layout-heavy PDF, VLM reading order | `mineru` | VLM layout/tables/equations; text PDFs run weight-free at flash effort |

Native extraction runs before OCR. OCR is selective and expensive: it is used
per page range, under a time budget, only where analysis shows native text is
missing or unusable.

## Auto mode

`auto` mode is decided by `parsecraft.routing`. `analyze()` produces per-page
signals, one rule table classifies each page into an `Intent`, eligibility rules
filter the catalog, and `plan_route()` returns a `RoutingPlan` with ordered
fallbacks. Planning is deterministic, pure, and offline. `parsecraft.pipeline.execute()`
runs that plan: it groups contiguous pages, converts each group with the chosen
backend, retries fallback candidates on a typed failure, and aggregates a
`DocumentResult`.

Hard constraints stay code-owned: installed extras, VRAM budget, format
coverage, `max_passes`, `allow_ocr`, and `offline` are enforced before any judge
sees a candidate, and a `NATIVE` page requires at least one eligible non-OCR
backend. An optional `RoutingJudge` may re-rank eligible candidates only;
`DeterministicJudge` is the default, and a judge cannot override a constraint. A
Jev / System One-backed judge is a planned optional extra.

See [Routing and auto mode](../architecture/routing.md) and
[ADR-0004](../adr/0004-routing-and-auto-mode.md). Tracking bead: `pc-5ub`.
