# ParseCraft backends reference

Self-contained catalog: what each backend extracts, what it needs installed,
and how to choose. Live truth on any host: `parsecraft backends` (add `--json`
for machine-readable descriptors).

## Contents

- [Extras and licences](#extras-and-licences)
- [Catalog](#catalog)
- [GPU VRAM budgets](#gpu-vram-budgets)
- [Platform and engine availability](#platform-and-engine-availability)
- [Choosing a backend](#choosing-a-backend)
- [How auto mode uses backends](#how-auto-mode-uses-backends)

## Extras and licences

The base install is self-sufficient for routing, the OCR-need classifier, the
judge client, `liteparse`, `openpyxl`, and the pandoc wrapper. Extras
(ADR-0009 feature-oriented extras):

| Extra | Contents | Note |
| --- | --- | --- |
| `web` | `httpx`, `trafilatura>=2.3.1` | HTML readability backend |
| `docling` | `docling>=2.130` | MIT |
| `pymupdf` | `pymupdf>=1.28.2` | **AGPL-3.0-or-commercial** (ADR-0003) |
| `mineru` | `mineru>=4.0.11,<5` | **conditional licence** (ADR-0007): Apache-2.0 code, commercial thresholds, attribution duty |
| `pdf-full` | `pymupdf` + `mineru` | inherits both licence warnings |
| `ocr` | `torch>=2.5`, `transformers>=5.17,<6`, `accelerate`, … | GPU OCR/VLM backends |
| `vllm` | `vllm>=0.11` | Linux/WSL2 only (platform marker) |

There is no `auto` extra — it was removed by ADR-0009, and no extra name
appears in `parsecraft judges` output. `marker` has no extra at all:
`marker-pdf` 2.0.0 is a
bring-your-own dependency (ADR-0006).

Engine binaries (never downloaded by parsecraft): `pandoc` (GPL-2.0-or-later
binary, required by the `pandoc` backend), `tesseract` (required by
`ocr-tesseract`), LibreOffice (office formats on the `liteparse` path).

## Catalog

Formats are extension-first with the declared MIME in parentheses; `supported_formats`
in the code is MIME, suffixes are joined on by the host layer.

| Backend | Install | Formats | Strengths | Weaknesses |
| --- | --- | --- | --- | --- |
| `native-text` | base | `.txt` (`text/plain`) plus every suffix mapped to text/plain (`.py`, `.rst`, `.toml`, `.log`, `.tex`, …) | Zero dependencies, fully offline, deterministic | Paragraphs only; no headings, tables, layout |
| `native-markdown` | base | `.md`, `.markdown` (`text/markdown`) | CommonMark token structure: headings, lists, tables, code, quotes | CommonMark only |
| `native-html` | base | `.html`, `.htm` (`text/html`) | Zero dependencies; complete extraction | Simplistic parser; scripts/styles skipped |
| `pymupdf` | analysis: core `pypdf`; extraction: `parsecraft[pymupdf]` | `.pdf`, `.docx`, `.pptx` | Fast native text + layout; monospace runs become fenced `code` chunks with recovered indentation; DOCX/PPTX need no second dependency; analysis works without the AGPL extra | Extraction requires the AGPL extra; scanned PDFs still need OCR; `.xlsx` deliberately not declared (openpyxl carries spreadsheets) |
| `openpyxl` | base | `.xlsx` | One typed `TABLE` chunk per worksheet with the real grid in `rows`, sheet name in `metadata["sheet"]`; no GPU, no weights | Workbook = one logical page; `.xls`/`.xlsm` not declared; cells read `data_only` (formulas yield cached values) |
| `pandoc` | base + **pandoc binary** (engine-gated) | `.docx`, `.pptx`, `.xlsx` (read-only), `.odt`, `.rtf`, `.epub` | Broad office/e-book coverage through one wrapper | Needs the external GPL binary; `.ods`/`.odp` unsupported by pandoc 3.11 |
| `liteparse` | base | `.pdf`, `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff` | Broad and permissive; no copyleft gate | Office/ODF formats need system LibreOffice; no `.html`; larger dependency surface |
| `docling` | `parsecraft[docling]` | `.pdf`, `.html`, `.md`, `.txt`, `.docx` | Layout, tables, reading order; the Word path is conversion-verified (2026-10-17, real 9-page guide) | Heavy dependency graph; **a 113-page PDF ran > 1 h**; other office/image formats registered upstream but undeclared pending verification |
| `trafilatura` | `parsecraft[web]` | `.html` | Readability-class main-content extraction: starts at the article H1, keeps tables as Markdown, asset-free (no weights) | Under-extracts very long single-page specs (kept 5,592 of 503,420 chars on a 1.2 MB spec); single-row tables without `<thead>` and bare `<pre><code>` lines don't survive as typed blocks |
| `pdf-inspector` | base | `.pdf` | Fastest verified text-PDF path: Rust/PyO3 straight to Markdown, no ML models, no OCR runtime; classifies text-based vs scanned before extraction | PDF only; no OCR (scanned pages need an OCR backend); prebuilt `cp38-abi3` wheels for Linux x86_64/aarch64, macOS Intel/ARM, Windows x64 |
| `marker` | bring-your-own `marker-pdf` 2.0.0 (no extra) | `.pdf` | High-fidelity PDF→Markdown: figures, tables, math, reading order; page-aware pagination | PDF only (other formats route via weasyprint/GTK, unverified on Windows); weights fetched at converter creation (network I/O); weights under OpenRAIL-M |
| `mineru` | `parsecraft[mineru]` (**GIL builds only**) | `.pdf` | VLM layout, tables, equations, reading order; **text PDFs run weight-free** at flash effort (no checkpoint download, no VRAM) | Heavy 55-package web stack; conditional licence; not yet benchmarked against siblings |
| `ocr-ovis` | `parsecraft[ocr]`, GPU | `.pdf`, `.jpg`, `.jpeg`, `.png` | ~0.9 B; formulas and tables; optional vLLM wrapper | GPU required (~1 GB VRAM); not yet benchmarked |
| `ocr-tele` | `parsecraft[ocr]`, GPU | `.pdf`, `.jpg`, `.jpeg`, `.png` | Geometry-aware; camera captures | GPU required (~1.2 GB VRAM); not yet benchmarked |
| `ocr-unlimited` | `parsecraft[ocr]`, GPU | `.pdf`, `.jpg`, `.jpeg`, `.png` | Long-horizon multi-page documents | 3 B; quantization required under an 8 GB budget |
| `ocr-qianfan` | `parsecraft[ocr]`, GPU | `.pdf`, `.jpg`, `.jpeg`, `.png` | Strong element, box, and reading-order control | 4 B; quantization required under an 8 GB budget |
| `ocr-tesseract` | base + **tesseract OS binary** | `.pdf`, `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff` | No extra, no GPU, no weights; runs anywhere the engine is installed | Weaker layout/formula handling than the VLM backends |

## GPU VRAM budgets

Declared budgets from `parsecraft backends` (what eligibility budgets against;
actual detection comes from `nvidia-smi` plus the torch build):

| Backend | Requirement |
| --- | --- |
| `ocr-ovis` | GPU, `vram <= 1G` |
| `ocr-tele` | GPU, `vram <= 1.2G` |
| `ocr-unlimited` | GPU, `vram <= 3G` |
| `ocr-qianfan` | GPU, `vram <= 4G` |
| `mineru` | CPU or GPU |
| everything else listed | CPU |

## Platform and engine availability

- Runtimes: CPython 3.13 (GIL only) and 3.14 (including free-threaded builds);
  `mineru` additionally needs a GIL build at install time (onnxruntime has no
  free-threaded macOS/Windows wheel).
- `vllm` extra: Linux/WSL2 only — the dependency carries a `sys_platform != 'win32'`
  marker, so it is absent on Windows by construction.
- Torch on Windows/Linux: PyPI wheels are CPU builds; CUDA builds come from
  `download.pytorch.org/whl/cu128` via the `[tool.uv.sources]` index block in
  parsecraft's `pyproject.toml` (honoured for a checkout or a downstream project
  that copies the block). Plain `uv tool install`/`uvx` installs carry no torch.
- OCR backends refuse silent CPU fallback (typed `require_gpu` failure).
- `ocr-tesseract` discovery includes a Windows install-root scan; declare a
  non-PATH binary with `PARSECRAFT_TESSERACT`.
- Offline (`PARSECRAFT_OFFLINE=1`): backends carrying a `model_asset` are
  excluded from eligibility.

## Choosing a backend

| Document trait | Route | Why |
| --- | --- | --- |
| Digital text PDF | `pymupdf` | Fast native text extraction, no GPU |
| Digital text PDF, throughput first | `pdf-inspector` | Rust extraction with no OCR runtime; falls back to `pymupdf` when the extra is absent |
| Scanned pages or camera photos | `ocr-tele`, `ocr-ovis` | No native text; geometry-aware, formula-capable |
| Multi-page dense tables | `ocr-unlimited` | Long-horizon multi-page handling |
| Formulas and equations | `ocr-ovis` | Formula-aware extraction |
| Broad office formats (docx, pptx) | `pandoc` | Convert to a readable format first |
| Spreadsheet (`.xlsx`) | `openpyxl` | Typed per-sheet tables; no copyleft, no GPU |
| Layout-heavy, reading order matters | `docling` | Layout, tables, and reading order |
| Layout-heavy PDF, VLM reading order | `mineru` | VLM layout/tables/equations; text PDFs weight-free |
| HTML article page, chrome removal matters | `trafilatura` | Readability extraction; `native-html` when completeness matters more |

## How auto mode uses backends

- Eligibility filters first: installed extras, format coverage, VRAM budget,
  offline, licence/OCR allowances. A judge or `--preference` only re-ranks what
  survived — it can never admit an ineligible backend.
- Native extraction runs before OCR; OCR is per page range, under a time budget,
  only where analysis shows native text missing or unusable.
- `--preference speed`/`balanced` ranks smallest declared VRAM first, `quality`
  largest — a resource proxy, not a measured quality score.
- `--max-passes N` adds up to `N-1` ordered fallback passes per page group;
  exhaustion surfaces as a typed error (`pipeline-all-passes-failed`), exit `1`.
- `parsecraft inspect FILE --json` shows the planned per-page decision and
  reason before you spend a conversion.
