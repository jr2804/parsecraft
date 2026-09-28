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
| `liteparse` | `parsecraft[liteparse]` (`liteparse` 2.14.7, Apache-2.0) | `application/pdf`, `image/jpeg`, `image/png`, `image/tiff` | [`run-llama/liteparse`](https://github.com/run-llama/liteparse) — Apache-2.0 | Broad and permissive; no copyleft gate | Office/ODF formats require a system LibreOffice; `.html` is not supported; larger extra dependency surface | Available |
| `docling` | `docling` (`docling` 2.130.0, MIT) | pdf, docx, pptx, xlsx, html, images | [`docling`](https://pypi.org/project/docling/) — MIT | Layout, tables, and reading order | Heavy dependency graph; ran >1 h on a 113-page PDF without finishing | Planned |

## OCR / document-VLM (GPU)

Model pins and licences are recorded once in
`src/parsecraft/backends/ocr/_models.py` (verified against the Hugging Face API
on 2026-09-27). The adapters are implemented but **not yet benchmarked** — GPU
weights are pending. Each backend has its own extra
(`pip install "parsecraft[ocr-ovis]"`), pulling `transformers`, `torch`,
`pillow`, and `accelerate`. The OCR extras exclude PyMuPDF, so **PDF input
additionally needs `parsecraft[pdf]`** (AGPL — see the warning above). All OCR
backends accept `application/pdf`, `image/jpeg`, and `image/png`.

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

Model licences apply to the weights; the ParseCraft adapter code is MIT.

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
| `pandoc`, `docling` | agnostic (planned) |

## Choosing a backend

| Document trait | Route | Why |
| -------------- | ----- | --- |
| Digital text PDF | `native-pdf` | Fast native text extraction, no GPU |
| Scanned pages or camera photos | `ocr-tele`, `ocr-ovis` | No native text; geometry-aware, formula-capable |
| Multi-page dense tables | `ocr-unlimited` | Long-horizon multi-page handling |
| Formulas and equations | `ocr-ovis` | Formula-aware extraction |
| Broad office formats (docx, pptx, xlsx) | `pandoc` | Convert to a readable format first |
| Layout-heavy, reading order matters | `docling` | Layout, tables, and reading order |

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
