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
    `native-pdf` extraction and OCR PDF input need PyMuPDF
    (**AGPL-3.0-or-commercial**, `pdf` extra), and `pandoc` needs the Pandoc
    binary (**GPL-2.0-or-later**). These are never core or dev dependencies. The
    OCR extras deliberately exclude PyMuPDF so the choice stays explicit.
    Installing one is your own licence decision; see
    [ADR-0003](../adr/0003-optional-agpl-pymupdf.md).

## Native (in-package)

| Backend | Extra | Formats | Upstream / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ------- | ------------------ | --------- | ---------- | ------------ |
| `native-text` | — | `.txt` (`text/plain`) | in-package — MIT | Zero dependencies, fully offline, deterministic | Paragraphs only; no headings, tables, or layout | Available |
| `native-markdown` | — | `.md` (`text/markdown`) | [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — MIT | CommonMark token structure: headings, lists, tables, code, quotes | CommonMark only; no non-CommonMark dialects | Available |
| `native-html` | — | `.html` (`text/html`) | in-package (stdlib `html.parser`) — MIT | Zero dependencies; headings, lists, tables, code, quotes | Simplistic parser; scripts and styles skipped; not a full HTML5 engine | Available |
| `native-pdf` | `pdf-lite` (analysis) + `pdf` (extraction) | `.pdf` (`application/pdf`) | [`pypdf`](https://pypi.org/project/pypdf/) — BSD-3-Clause; [`pymupdf`](https://pypi.org/project/PyMuPDF/) — **AGPL-3.0-or-commercial** | Fast native text and layout extraction; analysis works without the AGPL extra | Extraction requires the AGPL `pdf` extra; scanned PDFs need OCR | Available |

The native backends are registered through
`[project.entry-points."parsecraft.backends"]` and appear in
`parsecraft backends`.

## External converters

| Backend | Extra | Formats | Upstream / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ------- | ------------------ | --------- | ---------- | ------------ |
| `pandoc` | `pandoc` (`pypandoc` 1.17, MIT) | docx, odt, epub, rtf, LaTeX, reStructuredText, HTML, and many more | [pandoc.org](https://pandoc.org/) — **GPL-2.0-or-later** | Broadest format coverage | Requires the external Pandoc binary; copyleft gate | Planned |
| `liteparse` | `liteparse` (`liteparse` 2.14.7, Apache-2.0) | pdf, docx, pptx, xlsx, html, images | [`run-llama/liteparse`](https://github.com/run-llama/liteparse) — Apache-2.0 | Broad and permissive; no copyleft gate | Larger extra dependency surface | Planned |
| `docling` | `docling` (`docling` 2.130.0, MIT) | pdf, docx, pptx, xlsx, html, images | [`docling`](https://pypi.org/project/docling/) — MIT | Layout, tables, and reading order | Heavy dependency graph; ran >1 h on a 113-page PDF without finishing | Planned |

## OCR / document-VLM (GPU)

Model pins and licences are recorded once in
`src/parsecraft/backends/ocr/_models.py` (verified against the Hugging Face API
on 2026-09-27). The adapters are implemented but **not yet benchmarked** — GPU
weights are pending. Each backend has its own extra
(`pip install "parsecraft[ocr-ovis]"`), pulling `transformers`, `torch`,
`pillow`, and `accelerate`. The OCR extras exclude PyMuPDF, so **PDF input
additionally needs `parsecraft[pdf]`** (AGPL — see the warning above).

| Backend | Extra | Input | Model / licence | Strengths | Weaknesses | Availability |
| ------- | ----- | ----- | --------------- | --------- | ---------- | ------------ |
| `ocr-ovis` | `ocr-ovis` | page images | [`ATH-MaaS/OvisOCR2`](https://huggingface.co/ATH-MaaS/OvisOCR2) — Apache-2.0 | ~0.9 B; formulas and tables; vLLM wrapper (optional `vllm` extra) | GPU required (~1 GB VRAM); not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-tele` | `ocr-tele` | images, scans | [`StarDoc-AI/TeleOCR`](https://huggingface.co/StarDoc-AI/TeleOCR) — Apache-2.0 ([code](https://github.com/caipeng328/TeleOCR)) | Geometry-aware; camera captures | GPU required (~1.2 GB VRAM); not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-unlimited` | `ocr-unlimited` | multi-page | [`baidu/Unlimited-OCR`](https://huggingface.co/baidu/Unlimited-OCR) — MIT | Long-horizon multi-page documents | 3 B; quantization required under an 8 GB budget; not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |
| `ocr-qianfan` | `ocr-qianfan` | images | [`baidu/Qianfan-OCR`](https://huggingface.co/baidu/Qianfan-OCR) — Apache-2.0 (`Layout-as-Thought`) | Strong element, box, and reading-order control | 4 B; quantization required under an 8 GB budget; not yet benchmarked | Available (adapter implemented; not yet benchmarked — GPU/weights pending) |

Model licences apply to the weights; the ParseCraft adapter code is MIT.

## Choosing a backend

| Document trait | Route | Why |
| -------------- | ----- | --- |
| Digital text PDF | `native-pdf` | Fast native text extraction, no GPU |
| Scanned pages or camera photos | `ocr-tele`, `ocr-ovis` | No native text; geometry-aware, formula-capable |
| Multi-page dense tables | `ocr-unlimited` | Long-horizon multi-page handling |
| Formulas and equations | `ocr-ovis` | Formula-aware extraction |
| Broad office formats (docx, pptx, xlsx) | `pandoc`, `liteparse` | Convert to a readable format first |
| Layout-heavy, reading order matters | `docling` | Layout, tables, and reading order |

Native extraction runs before OCR. OCR is selective and expensive: it is used
per page range, under a time budget, only where analysis shows native text is
missing or unusable.

## Auto mode

`auto` mode selects a route from per-page signals. `analyze()` first produces
deterministic signals — native text present, text characters, replacement-character
ratio, image count, blank pages — without converting content. A bounded candidate
set is then handed to an optional System One / Jev judgment step that picks among
eligible backends.

Hard constraints stay code-owned. VRAM budget, batch size, maximum passes,
installed backends, and timeouts are never negotiated by the model: Jev selects
among eligible candidates only and cannot override a VRAM or dependency
violation. When Jev is unavailable, deterministic planning still produces a
route. Acceptance targets and the per-pass budget are fixed in
[ADR-0001](../adr/0001-phase-0-decisions.md).

Auto mode is **Phase 4** and not implemented yet. It is tracked by bead `pc-5ub`
(corpus-driven auto-mode routing harness).
