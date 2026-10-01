# ADR-0006: Marker backend deferred — dependency pins and restricted model weights

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** pc-1
- **Related:** gh-1, ADR-0003 (optional AGPL extra), ADR-0005 (GPL system binary),
  recon `.agents/plans/marker-backend/00-recon.md` (gitignored, bead `pc-bkk`),
  root AGENTS.md rule 10

## Context

gh-1 proposes [marker](https://github.com/datalab-to/marker) (PyPI
`marker-pdf` 2.0.0) as a parsecraft backend, citing an accuracy edge over
docling. The recon verified the released wheel empirically and found the
accuracy claim real (upstream olmocr-bench: marker balanced 76.0 vs docling
50.3; CPU `fast` mode 0.28 s/page warm with no weights download) — but three
independent blockers:

1. **Rule 10 fails on Python 3.14** (the canonical dev venv). marker pins
   `pillow<11` (no cp314 wheel); `surya-ocr==0.22.1` pins
   `opencv-python-headless==4.11.0.86` while `vllm>=0.30.0` needs `>=4.13.0`.
   On Python 3.13 the joint dry-run "resolves" only by downgrading vllm to
   0.11 (pinning `torch==2.8.0`, cp313-only) — all extras must resolve at
   their best versions, not merely resolve.
2. **The weights are not GPL — they are restricted:** Surya weights ship under
   a modified AI Pubs **OpenRAIL-M** licence (free for research, personal use,
   and startups under a funding/revenue cap). The issue body's "GPL models"
   is outdated. This is a licence category parsecraft has not handled before:
   not copyleft (ADR-0003/0005), not permissive — field-of-use restrictions
   that can conflict with commercial deployment.
3. **Construction-time network:** `PdfConverter.__init__` downloads a 14 MB
   font into site-packages on every cold construction — network at
   instantiation time, which the offline contract and the lazy-impl boundary
   treat as a side effect to refuse.

Also verified: on Windows (the canonical platform) marker is **PDF-only** —
DOCX/XLSX/PPTX/HTML/EPUB route through weasyprint, which needs GTK/Pango
system libraries and fails at import; marker's OCR/VLM needs an *external*
runtime (Docker+vLLM or a llama.cpp server), not pip-installable.

## Decisions

1. **Defer the `marker` extra.** No `parsecraft[marker]` is added until
   upstream pins allow joint resolution at best versions on every supported
   Python (requires-python `>=3.13`): specifically `pillow` ≥ 11 with cp314
   wheels, and `surya-ocr` compatible with `vllm>=0.30`'s opencv floor.
   Re-entry: re-run the recon (bead `pc-bkk`'s method) against the then-
   current release; this ADR is superseded, not amended.
2. **Restricted-weights discipline (new licence category).** When any backend
   ships weights under a field-of-use-restricted licence (OpenRAIL-M class),
   it is treated at least as strictly as the copyleft cases: its own opt-in
   extra, never transitive, catalog licence warning, `model_asset` declared
   so `offline=True` excludes it, and the extra's catalogue entry must state
   the funding/revenue restriction in plain text — a user must be able to see
   the commercial-use cap before installing.
3. **Implementation contract, if re-entry happens:** `supported_formats` is
   `["application/pdf"]` only until each other format is conversion-verified
   on the canonical platform (pdf-inspector precedent, gh-2); marker enters
   the catalog as a **converter candidate, not an analyzer** (no cheap
   detection API; `choose_analyzer`'s native-first ranking is untouched);
   page numbers normalize 1-based IR ↔ marker's 0-based `page_range`/`page`
   in the adapter, one place; and **no construction-time network** — the font
   fetch must be pre-seeded or deferred, or the backend raises typed
   `DependencyUnavailableError`/offline-eligible failure instead of fetching.

## Consequences

- The catalog gains no marker entry; `docs/reference/backends.md` stays as-is.
- gh-1 stays open as a deferred enhancement; the re-entry trigger is
  upstream, outside our control.
- The restricted-weights rule (decision 2) applies to any future candidate,
  not only marker, and is binding for catalog reviews from now on.

## Alternatives considered

- **Python-gated extra** (`marker-pdf ; python_version < '3.14'`). Rejected:
  it hides the break on the maintainer's own 3.14 environment, and the
  3.13 resolution still downgrades vllm/torch for every other extra — rule 10
  means best-version joint resolution, not just satisfiability.
- **Vendor the font / pre-seed weights to avoid the construction fetch.**
  Rejected for now: it patches one symptom while rule 10 still fails; revisit
  at re-entry.
