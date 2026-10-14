# ADR-0009: Feature-oriented optional-dependency extras

Date: 2026-10-13
Status: Accepted (amends ADR-0001 extras table, ADR-0003 point 3, ADR-0008; supersedes the `auto` meta-extra of 2026-10-02)

## Context

The extras list had grown to sixteen entries, split along *sub-functionality*
lines: one extra per OCR model (`ocr-ovis`/`-tele`/`-unlimited`/`-qianfan`),
`pdf` vs `pdf-lite` for two PDF libraries, `download` for one light package,
`trafilatura` named after its library, and a self-referential `auto` meta-extra.
All extras resolve jointly (rule 10), so granular extras bought no conflict
isolation — only install friction and a catalogue nobody can hold in their head.

The OCR models are downloaded assets (managed by the asset manager with pinned
revisions and sha256), not packages: their package needs are identical, which
makes one-extra-per-model pure granularity.

## Decision

Extras are **feature groups**. After the redesign there are nine:

| Extra | Feature | Contents |
| ----- | ------- | -------- |
| `ocr` | OCR / document-VLM on GPU | transformers (one window), torch, torchvision, accelerate, model-code helpers |
| `auto` | auto-routing flags | typesafe-sdk (judges), pdf-inspector (classifier) — direct dependencies, no meta-references |
| `web` | web/HTML ingestion | httpx (HTTP adapter), trafilatura (readability backend) |
| `pdf` | native PDF extraction | PyMuPDF (**AGPL**, ADR-0003) |
| `liteparse`, `docling`, `pandoc`, `mineru` | one parse engine each | engine wheels/binary (pandoc's binary is GPL-2.0-or-later, ADR-0005; MinerU licences per ADR-0007) |
| `vllm` | OCR accelerator (Linux/WSL2) | vllm; kept separate so `ocr` alone never drags a CUDA stack onto Linux installs |

Promoted into **core** (comprehensive base, per the same review): `pypdf`
(permissive analysis; supersedes the `pdf-lite` extra) and `huggingface-hub`
(model/asset downloads).

Untouched: backend *names* (`ocr-ovis` etc. stay), the `marker`
bring-your-own pattern (ADR-0006, no extra declared), and the licence
isolations — AGPL/GPL-adjacent stacks stay in their own extras and are never
smuggled into convenience groups.

## Consequences

- `pyproject.toml` carries at most one short comment per extra pointing at its
  ADR; rationale lives here, not in the dependency list.
- Consumers using `pdf-lite`, `download`, `systemone`, or any `ocr-*` extra
  must switch to the unified groups (`download` content is core now — just
  remove the extra). The AGPL extra was renamed `pdf` → `pymupdf` on 2026-10-14
  (package-named convention; see ADR-0003 amendment); consumers of
  `parsecraft[pdf]` must switch to `parsecraft[pymupdf]`, or use the new
  `parsecraft[pdf-full]` shortcut for the full PDF stack.
- Every declared extra is import-detectable; the `META_EXTRAS` concept is gone.
- The `auto`, `liteparse` and `pandoc` extras are gone (pc-s7g, same policy): their
  content is base now — `pdf-inspector` and `typesafe-sdk` (the routing seam), the
  `liteparse` library, and `pypandoc` (whose GPL binary was never a pip dependency and
  is gated by the pandoc engine check instead). Licence-isolated stacks
  (`pymupdf`, `mineru`, OCR) stay optional.
