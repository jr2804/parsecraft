---
name: parsecraft
description: Use when working with the parsecraft document-intelligence CLI — converting documents (PDF, DOCX, PPTX, HTML, Markdown, text, XLSX, EPUB, images) into typed structured chunks, Markdown, or machine-readable JSON; choosing a backend or trading speed against quality; auto-mode routing with judges and classifiers; installing parsecraft with uv tool install / uvx and its extras; GPU or OCR setup; or reading parsecraft JSON output, exit codes, and environment variables.
---

# ParseCraft CLI

## Overview

`parsecraft` converts a document into one canonical typed structure
(`DocumentResult` of pages of structured chunks). Markdown is a deterministic
projection of that structure; `--json` emits the structure itself.

Two rules for automated use:

1. Consume `--json` output — never parse the Markdown projection back into state.
2. Branch on the exit code — errors are typed and go to stderr, results to stdout.

## Install and run

Preferred — a persistent CLI:

```bash
uv tool install parsecraft                    # base install is self-sufficient
uv tool install parsecraft --extra docling    # add any declared parsecraft extra
uv tool install parsecraft --with PACKAGE     # add a peer package
uv tool run --from 'parsecraft[ocr]' parsecraft ...
```

One-off runs work too, but every invocation re-resolves the environment and gets
slower as `--with` dependencies pile up — use them for experiments, not loops:

```bash
uvx parsecraft convert report.pdf --json
uvx --from 'parsecraft[ocr]' parsecraft convert scan.png --json
```

As a project dependency: `uv add 'parsecraft[docling]'` then `uv run parsecraft …`.

Requires Python ≥ 3.13 and `uv`. Verify with `parsecraft --version` and
`parsecraft backends`.

The base install covers the whole routing pipeline (rule-table routing, OCR-need
classifier, judge client, `liteparse`, `openpyxl`, `pypandoc`). Extras add
heavyweight or licence-isolated engines:

| Extra | Adds | Licence note |
| --- | --- | --- |
| `web` | `trafilatura` HTML readability backend | Apache-2.0 |
| `docling` | layout/tables/reading-order parsing | MIT |
| `pymupdf` | PyMuPDF extraction for PDF/DOCX/PPTX | **AGPL-3.0-or-commercial** |
| `mineru` | MinerU VLM PDF pipeline | **conditional terms** (ADR-0007) |
| `pdf-full` | `pymupdf` + `mineru` in one name | inherits both warnings |
| `ocr` | GPU OCR/VLM backends (torch, transformers) | per-model upstream |
| `vllm` | vLLM accelerator (Linux/WSL2 only) | — |

## Commands

| Command | Purpose |
| --- | --- |
| `convert` | Convert a document through the auto-mode pipeline |
| `inspect` | Analyze a source and preview the routing decision |
| `backends` | List registered backends (`--json` for descriptors) |
| `judges` | List built-in judge providers and accepted model tokens |
| `benchmark` | Benchmark eligible backends over local documents (offline) |
| `cache` | Inspect or clear the content-addressed conversion cache |
| `models` | Inspect and manage the pinned model-asset cache |
| `config` | Inspect and validate ParseCraft configuration |

## `convert` — the main command

```bash
parsecraft convert report.pdf --json                    # IR JSON to stdout
parsecraft convert report.pdf -o report.md              # Markdown to a file
parsecraft convert scan.png -b ocr-ovis --json          # pin a backend
parsecraft convert report.pdf --preference quality      # rank for VRAM headroom
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Emit `DocumentResult` JSON instead of Markdown (env: `PARSECRAFT_JSON`) |
| `-o, --output FILE` | stdout | Write the result to FILE (parent dir must exist) |
| `-b, --backend NAME` | — | Lead backend for every eligible page |
| `--auto / --no-auto` | `--auto` | Auto-mode switch; `--no-auto` requires `--backend` |
| `--judge provider/model` (or `provider:model`; model token verbatim) | — | Learned re-ranking of eligible backends; may use the network; mutually exclusive with `--backend` |
| `--classifier provider/model` (or `provider:model`) | — | Fold OCR-need facts from a classifier into the analysis |
| `--preference speed\|balanced\|quality` | `balanced` | Ranking axis inside the eligible family |
| `--max-passes N` | `1` | Up to `N-1` ordered fallback passes per page group |
| `--no-ocr` | off | Forbid OCR backends |
| `--cache` | off | Content-addressed cache (source bytes + registry fingerprint + constraints + judge identity); failed runs are never cached |
| `--backend-option KEY=VALUE` | — | Repeatable backend-specific option |
| `--verbose` | off | Let third-party libraries print progress/advisories |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (also `inspect` with no eligible backend) |
| `1` | Analysis/routing failure: missing extra, unset credential, unavailable `--judge`/`--classifier`, no eligible backend, OCR page with no OCR family |
| `2` | Usage error: malformed spec, `--backend` together with `--judge`, `--no-auto` without `--backend`, ineligible `--backend`, judge crossing the native/OCR boundary, unsupported suffix |

Example failure (stderr, exit `1`):

```text
error: judge unavailable: judge provider 'zen' unavailable: OPENCODE_API_KEY is not set — export the credential before resolving a zen judge
```

## JSON output

`parsecraft convert SOURCE --json` prints `DocumentResult` as JSON (2-space
indent, sorted keys):

```json
{
  "metadata": {"source_uri": "...", "source_hash": "<sha256>", "format": "text/markdown",
               "page_count": 1, "title": null, "produced_at": "...",
               "package_version": "...", "schema_version": "..."},
  "pages": [{"page_number": 1, "width": null, "height": null,
             "blocks": [{"id": "md-0000-heading", "kind": "heading", "content": "ParseCraft",
                         "reading_order": 0, "bbox": null, "source_span": {"start": 0, "end": 13},
                         "metadata": {"heading_level": "1"}, "children": [], "relations": [],
                         "confidence": null, "quality": [], "rows": null}],
             "regions": [], "assets": [], "diagnostics": []}],
  "relations": [], "trace": [...], "quality": []
}
```

- `kind` is one of: `heading`, `paragraph`, `list`, `table`, `formula`, `code`,
  `figure`, `caption`, `header`, `footer`, `quote`, `unknown`.
- Tables carry the real cell grid in `rows` (tuple of tuples, `table` only);
  spreadsheet sheet names arrive in `metadata["sheet"]`.
- `trace` records each pass: `backend`, `backend_version`, `pass_kind`,
  `status`, `elapsed_s`, `settings`, `page_range`, and a typed `failure`
  (`code`: timeout, budget_exceeded, dependency_missing, gpu_insufficient,
  model_unavailable, cancelled, backend_error, invalid_input, …).
- Document invariants: pages unique and ascending, `metadata.page_count` equals
  `len(pages)`, chunk ids globally unique.

`parsecraft inspect SOURCE --json` previews the routing decision without
converting — use it to check backend eligibility before spending a conversion:

```json
{
  "media_type": "text/markdown",
  "analysis": {"page_count": 1, "source_hash": "ae96...", "diagnostics": [],
               "signals": [{"page_number": 1, "has_native_text": true, "text_chars": 4782,
                            "image_count": 0, "blank": false,
                            "replacement_char_ratio": null, "classifier_needs_ocr": null}]},
  "plan": {"primary": "native-markdown",
           "pages": [{"page_number": 1, "intent": "native", "candidates": ["native-markdown"],
                      "chosen": "native-markdown", "reason": "page 1: native text sufficient …",
                      "degradation_code": null, "degradation_score": null}]},
  "routing_error": null
}
```

Full field-by-field walkthrough: [references/json-output.md](references/json-output.md).

## Auto mode and decision models

Auto mode is on by default and needs no model: a deterministic rule table
decides — `parsecraft judges` states it plainly: "no --judge: the deterministic
rule table decides; no provider is resolved and no network is used".

- `--judge provider/model` (or `provider:model`; the model token is verbatim, tags and registry paths included) swaps in a learned re-ranking of the
  candidates the hard constraints already admitted. Built-in providers (from
  `parsecraft judges`):

  | Provider | Tokens | Credential |
  | --- | --- | --- |
  | `typesafe-ai` | `jev-latest`, `jev-preview` | `TYPESAFE_API_KEY` |
  | `zen` | `jev-1.13`, `jev-1.13-free` | `OPENCODE_API_KEY` |
  | `ollama` | any local model (verified: `nimble`, `tev`) | none — local daemon on `http://localhost:11434` |

- The judge client ships in the base install; only credentials (or a running
  Ollama) are needed. `parsecraft judges` prints credential state only — no
  extra column, because the `auto` meta-extra was removed by ADR-0009.
- `--classifier` folds OCR-need evidence into the analysis instead of re-ranking;
  it combines with `--judge`.
- `--preference` reaches both the planner's built-in judge and a resolved
  System One judge: `speed`/`balanced` take the smallest declared VRAM first,
  `quality` the largest — declared VRAM is a resource proxy, not a quality score.
- A judge can never override hard constraints: installed extras, VRAM budget,
  format coverage, offline (`PARSECRAFT_OFFLINE=1`).

## Choosing a backend

Run `parsecraft backends` for the live catalog (name · CPU/GPU + VRAM ·
formats); `--json` for machine-readable descriptors. Quick orientation:

| Document | Route | Why |
| --- | --- | --- |
| Any plain text (`.txt`, `.py`, `.rst`, `.log`, …) | `native-text` (base) | Zero dependencies, deterministic |
| Markdown / HTML source | `native-markdown` / `native-html` (base) | Complete, instant |
| Digital text PDF, throughput first | `pdf-inspector` (base) | Rust extraction, no ML/OCR runtime |
| Digital text PDF | `pymupdf` (extra) | Fast native text; extraction needs the AGPL extra, analysis does not |
| Layout-heavy PDF, tables/reading order | `docling` (extra) | Layout-aware; heavy — a 113-page PDF ran > 1 h |
| Layout-heavy PDF, VLM path | `mineru` (extra) | Text PDFs run weight-free at flash effort |
| Scanned pages / camera photos | `ocr-tele`, `ocr-ovis` (extra `ocr`, GPU) | No native text; geometry-aware, formula-capable |
| Dense multi-page tables (scanned) | `ocr-unlimited` (GPU) | Long-horizon multi-page handling |
| Spreadsheets | `openpyxl` (base) | Typed per-sheet `TABLE` chunks, no GPU |
| Broad office (docx/pptx/odt/rtf/epub) | `pandoc` (base + pandoc binary) | One wrapper; GPL binary on the host |
| HTML article, chrome removal | `trafilatura` (extra `web`) | Readability-first; prefer `native-html` when completeness matters |

Native extraction always runs before OCR; OCR is per page range, under a time
budget, only where analysis shows native text missing or unusable. A PDF that
turns out to be scanned falls through to OCR only when an OCR backend is
eligible — otherwise the run fails with exit `1`.

Speed-vs-quality knobs in order of cost:

1. `--preference speed` — smallest declared VRAM first inside the eligible family.
2. Pick a lighter backend with `-b` (or accept auto-mode's choice shown by
   `inspect`).
3. `--max-passes 1` (default) — no fallback passes.
4. Leave `--cache` off until repeat conversions of the same input matter.

## Formats

`parsecraft backends` prints formats as extension + MIME per backend. Notable
mappings:

- Text-like suffixes (`.py`, `.rst`, `.toml`, `.log`, `.tex`, …) all resolve to
  `text/plain` → `native-text`.
- OCR-accepting backends take `.pdf`, `.jpg`, `.jpeg`, `.png`
  (`ocr-tesseract` and `liteparse` also `.tif`/`.tiff`).
- `.xlsx` → `openpyxl` (`.xls`/`.xlsm` are not declared).
- `.docx` → `docling` (extra `docling`) for headings, tables, and reading order;
  `pandoc` (base + host binary) or `pymupdf` (extra `pymupdf`, AGPL) also read
  it. No office format converts on a bare base install.
- `.html` → `native-html` (complete) or `trafilatura` (readability, extra `web`).
- Unsupported suffix → usage error, exit `2`.

Per-backend strengths, weaknesses, licence gates, platform availability, and
languages: [references/backends.md](references/backends.md).

## GPU setup

- Plain `uv tool install` / `uvx` installs carry **no torch** — text, office,
  and CPU backends work without any GPU stack.
- The `ocr` extra pulls torch. On Windows/Linux the PyPI torch wheels are CPU
  builds, so a CUDA host must resolve torch from `download.pytorch.org/whl/cu128`.
  That `[tool.uv.sources]` index block lives in parsecraft's `pyproject.toml`
  and only applies when it is *your* project's metadata (a checkout, or a
  downstream project that copies the block) — the CUDA driver must also match
  the wheel; macOS resolves PyPI wheels (CPU + MPS).
- Detection is automatic: parsecraft probes `nvidia-smi` for the VRAM budget and
  reads the installed torch build for CUDA usability. A GPU-only backend is
  dropped when the GPU is unusable, and the CLI warns once. Check the result in
  `parsecraft backends` (GPU column, e.g. `ocr-ovis  gpu  vram <= 1G`).
- OCR backends require the GPU explicitly — they fail loudly rather than
  silently degrading to CPU.
- Platform limits: `vllm` extra is Linux/WSL2 only; `mineru` is GIL-builds-only
  (no free-threaded interpreter); `pdf-inspector` ships prebuilt wheels for
  Linux x86_64/aarch64, macOS Intel/ARM, Windows x64.
- No GPU at all: `ocr-tesseract` (base install) runs on CPU wherever the OS
  `tesseract` binary exists (install via apt/brew/choco/winget; parsecraft never
  downloads it), or point `PARSECRAFT_TESSERACT` at a binary outside PATH.

## Environment variables

| Variable | Effect |
| --- | --- |
| `PARSECRAFT_JSON` | Emit JSON without passing `--json` |
| `PARSECRAFT_OFFLINE=1` | Offline constraint: backends carrying a model asset are excluded |
| `PARSECRAFT_TESSERACT` | Declare the tesseract binary location (never probed past) |
| `TYPESAFE_API_KEY` | Credential for `typesafe-ai/*` judges |
| `OPENCODE_API_KEY` | Credential for `zen/*` judges |

## Verification

```bash
parsecraft --version                    # package version
parsecraft backends                     # live backend catalog
parsecraft inspect FILE --json          # preview the routing decision
parsecraft convert FILE --json          # IR JSON; check $? before consuming stdout
```

If a conversion exits non-zero, read stderr (it names the typed failure, e.g.
`pipeline-all-passes-failed`) and do not treat stdout as a result.
