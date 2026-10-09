# ADR-0007: MinerU backend as a conditional-licence opt-in extra

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** user (ruling relayed via knox-1), pc-1
- **Related:** ADR-0003 (optional AGPL extra — opt-in principle),
  ADR-0006 (marker deferral; the inverse no-extra shape), bead `pc-ha9`
  recon `.agents/plans/mineru-backend/00-recon.md` (gitignored),
  root AGENTS.md rule 10

## Context

knox consolidates all parsing onto parsecraft and carries a MinerU backend
(`mineru_parser.py`, pinned to the 2.x API). The pc-ha9 recon verified the
upstream empirically:

- **Rule 10 passes on both Pythons** (3.13 and 3.14): 55 packages added, 0
  removed, only `tomlkit` moving (0.15.1 → 0.14.0). Unlike marker
  (ADR-0006), there is **no dependency-pin blocker**.
- **The licence surface is the decision:** the default VLM checkpoint
  (`opendatalab/MinerU2.5-2509-1.2B`, 2.16 GiB) is **AGPL-3.0**; the
  pipeline kit (`opendatalab/PDF-Extract-Kit-1.0`, 14.09 GiB, 187 files)
  declares **no licence at all**; the code licence is Apache-2.0 **plus**
  commercial thresholds (>100M MAU or >USD 20M revenue), an online-service
  attribution duty, and termination-without-notice. A conditional-licence
  category with no precedent ruling here.
- 4.x is an API rewrite (doclib FastAPI/uvicorn server, per-format
  `analyze_<fmt>`, Content List V1/V2) with **two page-index conventions in
  one release** (0-based `page_idx` vs 1-based `page:{page_no}` locators);
  the page-range input base is delegated downstream and unverified. Formats
  are present but **not conversion-verified**; no upstream VRAM minimum
  exists.

## Decision

1. **Ship MinerU as an optional extra — the extra IS the opt-in.** The user
   ruling: the licence is not a blocker to shipping the capability; it is a
   thing the installer must be told about. `parsecraft[mineru]` (or the
   implementer's nearest library-named equivalent) opts the installer into
   the conditional licence terms above. This extends ADR-0003's opt-in
   principle: consent is expressed by installing the extra and reading the
   statement offered at the point of installation.
2. **The licence statement is a documentation requirement, not decoration.**
   The docs page offering the extra (and the README's extras summary) must
   state plainly: AGPL-3.0 default checkpoint; the 14 GiB pipeline kit with
   no declared licence; the commercial thresholds; the attribution duty;
   termination-without-notice. No silent install: the statement ships in the
   same change as the extra.
3. **Marker and MinerU are documented as the two DISTINCT optional-dependency
   shapes** (knox-1's warning, adopted): marker = *backend + entry point, no
   extra* — the consumer owns the dependency and parsecraft's lock never
   resolves it (ADR-0006 d1 holds); MinerU = *the extra is the opt-in and
   parsecraft owns the pin*. The two sections must not read identically or
   the licence statement stops meaning anything.
4. **Engineering constraints from the recon bind the implementation:**
   - API version pinned by the implementer from recon evidence (2.x API
     surface vs 4.x rewrite; knox targets 2.x — a 2.x pin avoids the dual
     page-index conventions until 4.x stabilises, but the choice and its
     evidence are recorded in the bead).
   - Page indexing normalized 0/1-based in exactly one place (house rule).
   - Formats enter `supported_formats` only after conversion verification
     (house rule from gh-2).
   - `gpu_requirement`/VRAM declared from **measurement**, not upstream
     claim (none exists).
   - The AGPL checkpoint makes this a `model_asset` backend: download
     notice, offline carve-out, and cache discipline apply (ADR-0003
     mechanics).

## Alternatives considered

- **Defer entirely (the recon's initial verdict):** rejected by the user
  ruling — knox keeps a pinned copy either way, and an undocumented
  capability split across projects is worse than a documented opt-in here.
- **Marker-style no-extra shape for MinerU too:** rejected — it would move
  the pin reality to every consumer while the whole point of the ruling is
  that parsecraft offers the capability *with* its terms stated; also the
  recon's 55-package web stack is exactly what a consumer should not have
  to resolve blind.

## Consequences

- A conditional-licence opt-in precedent exists for future extras
  (restricted weights, thresholds, attribution duties): extra + statement,
  never silent.
- knox's `mineru_parser.py` deletion unblocks when this lands, joining the
  marker/web removal batch.
- The unlicensed pipeline kit stays a standing risk item: if upstream never
  declares a licence, the docs statement says so and the opt-in stays honest
  rather than assuming permission.

## Amendment (2026-10-09): licence facts corrected for the pinned 4.0.11

The Context section above describes the surface the pc-ha9 recon measured
against the 2.x line. Implementation (bead `pc-gv8`) pinned
`mineru>=4.0.11,<5` — the last 2.x (2.7.6) requires Python <3.14 and cannot
  install on the canonical dev env, making rule 10 impossible — and the
4.0.11 licence surface differs:

- The AGPL-3.0 checkpoint (`MinerU2.5-2509-1.2B`) is **not referenced
  anywhere in 4.0.11** (zero grep hits in the wheel).
- The 4.0.11 VLM checkpoint `MinerU2.5-Pro-2605-1.2B` and the torch
  pipeline kit `MinerU-4_models_torch` declare **Apache-2.0**.
- The Windows/CPU **default** engine is llama-cpp →
  `jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF`, which declares **no licence** —
  note this is not an exotic path: it is what a default Windows install
  downloads.
- The ONNX pipeline kit `MinerU-4_models_onnx` also declares **no licence**.
- The code licence is unchanged: `LicenseRef-MinerU-Open-Source-License`
  (Apache-2.0 plus commercial thresholds, attribution duty,
  termination-without-notice).

**Ruling (pc-1, under the user's 2026-10-09 decision): the GO stands.** The
correction is strictly-better-or-equal on every axis versus the picture the
user approved (no AGPL anywhere; undeclared-weights risk unchanged;
conditional code licence unchanged). The documentation statement ships the
corrected facts: Apache-2.0 checkpoint and torch kit; **undeclared licence**
on the default Windows/CPU GGUF engine and the ONNX kit (unknown terms —
stated, not assumed); the conditional code licence with its thresholds,
attribution duty, and termination clause. The standing-risk consequence
above applies verbatim to the undeclared GGUF/ONNX artifacts.
