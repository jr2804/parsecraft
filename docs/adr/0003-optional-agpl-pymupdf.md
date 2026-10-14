# ADR-0003: PyMuPDF as an optional AGPL PDF backend

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** user, pc-1
- **Related:** ADR-0001 (dependency licence rule), ADR-0001 §4 (profiles)

## Context

ADR-0001 2 allows only permissive licences (MIT/BSD/Apache-2.0/PSF/ISC) in
core; anything copyleft requires an explicit addendum before it enters any
dependency profile. The native PDF backend needs real text, layout, and
image extraction. `pypdf` is permissive but materially weaker as a parser;
**PyMuPDF** is a strong, fast extraction engine but is licensed **AGPL-3.0**.

## Decision

1. **PyMuPDF is allowed only behind the optional `pdf` extra**
   (`pdf = ["pymupdf>=1.24"]`). It is never a core dependency.
2. Core stays MIT and import-clean: the `pdf` extra is not installed by
   `mise dev` or CI, and the backend loads PyMuPDF only at instantiation
   (module boundary + `importlib`).
3. ~~`pdf-lite` (pypdf, BSD-3-Clause) remains~~ **Amended (ADR-0009):** pypdf is now a **core** dependency — the permissive path for
   cheap, always-available inspection — page count, encryption, text-density
   and other difficulty signals used by `analyze()` — so routing works without
   the AGPL extra.
4. Installing the `pdf` extra is the user's own licence decision; AGPL-3.0
   terms apply to that extra, not to the MIT-licensed core.

   **Amended (2026-10-14, pc-1):** the AGPL extra is renamed `pdf` → `pymupdf` under the
   package-named convention (matching `docling`/`mineru`/`web`-or-`trafilatura`).
   Consumers of `parsecraft[pdf]` must switch to `parsecraft[pymupdf]`. A new
   meta-extra `pdf-full = [pymupdf, mineru]` ships as the convenience bundle
   (pymupdf is AGPL; mineru is Apache-2.0 weights + source-available code
   per ADR-0007). The original `pdf` extra is removed in the same commit;
   `setup-pandoc` was the unrelated previous rename.

## Consequences

- The `pymupdf` extra is copyleft and therefore never bundled into the default
  distribution or the `all` convenience profile without the same explicit
  acknowledgement.
- `pymupdf` extraction requires `parsecraft[pymupdf]`; `pymupdf` analysis
  works on the core install alone.
- Any future copyleft dependency follows this same pattern: optional extra +
  addendum + no core/dev install.
