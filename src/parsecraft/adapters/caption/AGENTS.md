# AGENTS.md — src/parsecraft/adapters/caption/

Opt-in caption attribution over an existing `DocumentResult` (pc-sod).

## Purpose

Attribute existing caption paragraphs to their tables/figures with one
batched System One request per document — relations only. No chunk is
created, deleted, reclassified, rewritten, or copied; projection output is
byte-identical with or without attribution (relations are never rendered).

## Ownership

- `__init__.py` — the whole seam (single module, no impl split):
  `attribute_captions(document, *, model=None) -> CaptionReport` (Jev path),
  `link_same_span_captions(document) -> int` (deterministic structural path),
  `CaptionFailure` (unconfigured / invalid-spec / unsupported-provider /
  unavailable / transport-error), `CaptionReport` (frozen counts + failure).

## Local Contracts

- **Caller owns the caching model.** The function runs after
  `execute`/cache at the caller's discretion (attribute-then-cache for
  stability); no cached reproducibility is claimed. Off means never calling
  it — there is no flag, knob, executor touch, or CLI surface.
- **Relations only, paragraph → target.** Each linked pair appends one
  `ChunkRelation(CAPTION_OF, target_id)` on the caption-role chunk
  (`PARAGRAPH` or `CAPTION` immediately preceding a `TABLE`/`FIGURE`
  sibling). No metadata writes, no text copies — the consumer keeps its own
  prose and forwards relations separately.
- **Same-span links are structural, never model evidence.**
  `link_same_span_captions` links a `CAPTION` sharing its exact non-null
  `source_span` with an adjacent target. It is a separate function with
  separate tests; callers compose the two explicitly.
- **Uncalibrated by construction.** No numeric floor anywhere; exact ties
  abstain. Probabilities are a selection signal only — nothing is reported
  or documented as validated or calibrated, and `ChunkRelation` carries no
  confidence by shape, which enforces this.
- **Strict answers or abstention.** A pair links only when the answer is
  exactly the two option labels as keys, every score a finite in-range
  float summing to unity within `_UNIT_SUM_TOLERANCE` (0.01 — the API
  promises only "approximately 1"), the maximum unique, and `choice`
  naming that maximum. Missing/extra keys, NaN/inf/out-of-range/off-unity
  scores, ties, and winner-mismatches abstain the pair.
- **Typed seam, no runtime checks.** Shape is the SDK's own guarantee:
  strict parsing rejects invalid envelopes before validation sees them, so
  the seam is public concrete `Protocol`s (`ChoiceAnswer`,
  `SystemOneResponse`, `SystemOneClient`, `SystemOneSdk`) with zero
  `isinstance` — type rigor rule 1 allows only public protocols, and the
  SDK's typed surface is the seam. `JudgeSpecError` is a real top-level
  import (light module, no cli drag); the SDK stays call-time.
- **No-throw boundary.** Response-level access failures (raising
  `response.choices`, transport/parse errors) fail the run as
  `TRANSPORT_ERROR`; per-answer access failures (raising `choice` or
  `probabilities`) abstain that pair. Resolution failures map to
  `INVALID_SPEC` (malformed spec only) or `UNAVAILABLE` (missing
  credential, bad config, broken install) — nothing escapes
  `attribute_captions` except programming bugs.
- **Validate all, then write.** Every answer is validated before any
  relation is appended; a corrupt envelope changes nothing. Failures return
  a `CaptionFailure` in the report — never a fabricated document signal,
  never a raised transport/config error, never secrets or exception text.
- **Endpoint discipline.** Spec order is explicit argument, generic env,
  file (reusing the judge chain); the provider must be Jev-serving
  (`CAPTION_PROVIDERS = ("typesafe-ai", "zen")`). The local endpoint
  (`ollama`/Nimble/Tev) is excluded — different model family with
  cold-start semantics this batch path does not cover — so requesting it
  fails as `UNSUPPORTED_PROVIDER` instead of degrading silently. Model
  tokens stay verbatim per provider doctrine; the base URL comes only
  from the standard chain. SDK default timeout applies (cloud endpoints
  only, no cold-load override).
- **Import-clean.** `typesafe_sdk` enters only through the call-time
  `import_module` seam (`_sdk`/`_open_client`); no `httpx2` or
  `TYPE_CHECKING` imports in shipped code. The module-level `_open_client`
  seam exists so tests can inject controlled transport into the REAL
  client — it is not public API.
- **Privacy.** Bounded snippets per pair (caps `_MAX_TARGET_CHARS` /
  `_MAX_CANDIDATE_CHARS`, truncation flagged in the request) go to the
  configured endpoint; cloud endpoints traverse the network. Disclosed in
  the module docstring; never log or print secrets.

## Verification

`mise test` — `tests/adapters/test_caption.py`: real `TypeSafeClient` +
real `Choice` over `httpx2.MockTransport` (genuine SDK
serialization/parsing, controlled bytes — stubbed endpoint, never model
evidence); validator-level edge cases JSON cannot express (NaN/inf) against
the pure validators; baseline/no-op, partial, envelope-failure, truncation,
idempotency, same-span, and unchanged-content pins. The single real-model
check is `test_live_jev_caption_attribution` (`judge` marker, needs
`TYPESAFE_API_KEY`, out of the default gate). 100% coverage gate applies.
