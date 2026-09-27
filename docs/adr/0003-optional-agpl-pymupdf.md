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
3. **`pdf-lite` (pypdf, BSD-3-Clause) remains** the permissive dependency for
   cheap, always-available inspection — page count, encryption, text-density
   and other difficulty signals used by `analyze()` — so routing works without
   the AGPL extra.
4. Installing the `pdf` extra is the user's own licence decision; AGPL-3.0
   terms apply to that extra, not to the MIT-licensed core.

## Consequences

- The `pdf` extra is copyleft and therefore never bundled into the default
  distribution or the `all` convenience profile without the same explicit
  acknowledgement.
- `native-pdf` extraction requires `parsecraft[pdf]`; `native-pdf` analysis
  works with `parsecraft[pdf-lite]` alone.
- Any future copyleft dependency follows this same pattern: optional extra +
  addendum + no core/dev install.
