# ADR-0010: OCR-capable is an admission fact, not a naming one

Date: 2026-10-31
Status: Accepted (amends ADR-0008 tier rule, ADR-0009 extras table; retires the
`_UNPROVABLE_IMPORTS` exemption filed as pc-5m9)

## Context

Two independent facts were being read off one predicate.

`is_ocr(descriptor)` answers *is this backend an OCR harness?* — a naming
question: an `ocr-` backend name, or an `ocr-` dependency group. Four places
consume that answer as if it meant *does this backend read scans?*: the
extraction tier (harnesses are tier 2), the `allow_ocr` gate, the judge's family
label, and the guard that keeps an OCR harness out of the lead position on a
native page.

The two questions have different answers. `docling` OCRs images and text-free
pages inside its own conversion and also extracts native text well; `mineru`'s
higher-effort path reads scans; `marker` is a document-VLM stack. None is a
harness, all three read scans. Because they were excluded from the `OCR_*`
intent family, a page that had no text layer could not reach them through auto
routing at all — it landed on a native extractor and came back empty. Measured:
a one-page bitmap-only PDF (150 dpi render of an ITU-T page) routed to
`liteparse`, returned one element of fence, and carried a
`degraded-blank-page` quality signal; `docling` read the identical page
correctly in the same wall time on CPU.

Separately, `EXTRA_IMPORTS` claimed `mineru: ("mineru",)` and
`pdf-full: ("pymupdf", "mineru")` long after the native-MinerU rework removed
the `mineru` package. `pyproject.toml` declares `mineru = ["accelerate",
"torch", "transformers", "torchvision"]` — the **same four requirements as
`surya`** — so on a host with the extra installed, `find_spec("mineru")` is
`None` and the backend is ineligible forever; on a host that installed only
`surya`, any probe for one of those four modules answers `True` and the
ineligible-extra problem becomes a false positive instead. A false positive is
not free: it admits a backend whose first page triggers a gigabyte-scale
checkpoint fetch that can only fail. The tests carried the defect as a known
exemption (`_UNPROVABLE_IMPORTS`, pc-5m9) rather than fixing it.

## Decision

**1. Admission is declared.** `BackendCapabilities` gains
`ocr_capable: bool = False`, a statement that an OCR-intent page may be routed
here, not a ranking preference. `docling`, `mineru` and `marker` set it.

A new predicate consumes it:

```python
def serves_ocr_intent(descriptor: BackendDescriptor) -> bool:
    return is_ocr(descriptor) or descriptor.capabilities.ocr_capable
```

`in_intent_family` uses `serves_ocr_intent` for `OCR_GENERAL`, `OCR_TABLES` and
`OCR_VISION`. `is_ocr` keeps its harness meaning everywhere else. Membership is
necessary, never sufficient: NATIVE pages still order native candidates ahead of
these backends, and `_validate_order` enforces it.

`ocr_capable` changes no tier and does not answer the `allow_ocr` gate. docling
stays tier 1 and stays eligible with `--no-ocr`, because it is not an OCR
harness — the flag forbids harnesses, and this ADR deliberately keeps that
reading.

**2. Unprovable extras are declared, not guessed.** `EXTRA_IMPORTS` keeps only
requirement sets an import can prove. A new map sits beside it:

```python
EXTRA_DECLARATIONS: dict[str, str] = {"mineru": "PARSECRAFT_MINERU"}
```

`extra_present()` consults `EXTRA_DECLARATIONS` first and returns the operator's
declaration; otherwise it falls back to the import map. This mirrors
`ENGINE_DECLARATIONS` (ADR-0008 decision 2: a declared engine is honoured
verbatim and never probed), keyed by extra rather than by backend name, so
nothing is special-cased in code.

Composite extras are absent from both maps on purpose: `pdf-full` is a bundle,
a bundle gates nothing, and its contents each report themselves.

The declaration is only legitimate while the extra is genuinely unprovable, so
the contract test enforces it: every declared extra must be *composite* (each of
its requirements is also required by some sibling extra). The moment `mineru`
grows a distinctive requirement, or the maps start to disagree,
`test_declared_extras_are_unprovable_by_construction` fails and the declaration
has to go.

## Consequences

- A scanned page can reach a backend that reads scans, without reclassifying
  that backend: tier, `allow_ocr` and the judge's family label are unchanged.
- `--no-ocr` still means "no OCR harness"; documentation says so explicitly
  now, in `docs/reference/backends.md` and the `--no-ocr` rows of
  `docs/reference/cli.md`.
- `parsecraft[mineru]` and `parsecraft[pdf-full]` become usable again on hosts
  that set `PARSECRAFT_MINERU=1`. Nothing is detected automatically, and a host
  that installs the extra without declaring it stays ineligible — the silent
  alternative (probing a shared requirement) was rejected.
- The pc-5m9 exemption is gone from the test suite; the map cannot drift
  silently in either direction.
- Deliberately not changed: docling's tier and its treatment under
  `allow_ocr`. Folding a scan-reading parser into the harness definition would
  be a different decision with different evidence, and this ADR is not the place
  to make it.

## Verification

- `tests/routing/test_routing.py::test_ocr_capable_parser_serves_ocr_intents_without_becoming_an_ocr_backend`
  pins all four answers for a hybrid backend (not a harness, serves OCR intents,
  tier 1, still admitted for NATIVE) and that a plain native backend stays out of
  `OCR_GENERAL`.
- `tests/environment/test_environment.py` pins the declaration contract, that
  declared and import-provable groups are disjoint, and that
  `PARSECRAFT_MINERU=1` alone makes `probe_environment().installed_extras`
  contain `mineru`.
- End to end through the consumer: the bitmap-only PDF reported
  `backends: liteparse` with `degraded-blank-page` before the change and
  `backends: docling` with correct text after it.

## Amendments

2026-10-31 (pc-1, on merge): two boundaries were left open above and are now
settled, both as "leave as is".

1. docling stays ungated by `allow_ocr`. The flag governs *harness* passes —
   the expensive separate VLM runs it was built to avoid (the 527 s `ocr-ovis`
   blank-page measurement). docling's internal OCR is a property of the parser
   the operator already selected, not an extra pass they opted into, and a
   `--no-ocr` run on a native page already lets docling OCR internally today,
   so gating it on OCR intents alone would be inconsistent. A stricter knob, if
   anyone wants one, is a new flag rather than a redefinition of this one.
2. mineru keeps `ocr_capable: True`, with the effort-dependence noted. The flag
   is a capability ("can read a text-free page"), never a precondition and
   never a promise about the default effort path — the same shape as
   `gpu_requirement`, which is a requirement fact rather than a usage fact.
3. pc-2's review of the merged tree found that "admission is not promotion"
   held only for the built-in judge. Two doors stayed open: `_validate_order`
   guarded `NATIVE` only, so an injected provider judge could lead an OCR-intent
   page with a non-harness while a harness was eligible, and
   `RoutingConstraints.preferred_backend` could do the same.
   `planner._validate_ocr_lead` closes the judge door for every non-`NATIVE`
   intent: if any eligible candidate is a harness, the lead must be a harness.
   Naming a backend explicitly (`--backend`, which arrives as
   `preferred_backend`) stays the caller's privilege — the same shape as the
   NATIVE lead-tier cap rising to a named backend — so a deliberate
   `-b docling` on a scan still works. A judge that prefers a parser over an
   available harness now raises `JudgeViolationError` instead of winning
   quietly.

Ranking, verified for the built-in `DeterministicJudge`: `_sort_key` ranks OCR
harnesses at `family_rank` 0 (`OCR_GENERAL`, no preferred model) or 0-or-1
(`OCR_TABLES` → `ocr-unlimited`, `OCR_VISION` → `ocr-ovis`) and a non-harness
parser at 1 or 2, so an `ocr_capable` parser is admitted to an OCR page but can
never lead one over an available harness. **Admission is not promotion.**
`tests/routing/test_routing.py::test_ocr_capable_parser_is_admitted_but_never_leads_an_ocr_page`
pins the ranking, and
`test_an_injected_judge_may_not_lead_an_ocr_page_with_a_non_harness` pins the
guard that extends the same rule to an injected judge.
