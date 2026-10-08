# 00 — Plan: multi-endpoint Jev judge (typesafe / zen / ollama) + tiered live tests

- **Origin:** user directive 2026-10-01 — semi-live judge testing against local
  ollama (docs.ollama.com/api/systemone) + live tiers for TYPESAFE_API_KEY,
  OPENCODE_API_KEY, OPENROUTER_API_KEY; three machine profiles (real, better,
  worse).
- **Author:** pc-1. **Implementer:** pc-3. **Contract:** ADR-0004 d3–d5 + A7;
  existing pc-989 review verdicts (F1 memo etc.) remain binding.

## 0. Empirical ground truth (verified by pc-1, 2026-10-01)

| Platform | Endpoint | Auth | Model ids | Verified |
| --- | --- | --- | --- | --- |
| Typesafe cloud | typesafe-sdk (api.typesafe.ai) | `TYPESAFE_API_KEY` | `jev` (+ `-latest`) | shipped, live tier gated on key |
| OpenCode Zen | `POST https://opencode.ai/zen/v1/systemone` | `OPENCODE_API_KEY` | `jev-1.13`, `jev-1.13-free` | docs/de/zen.md model table |
| Ollama local | `POST http://localhost:11434/v1/systemone` | none (local) | `nimble` (ollama's System One model; requires ollama ≥ 0.35) | smoke 2026-10-01: choice B p=0.9999, ollama 0.35.0 running |
| OpenRouter | none — `/api/v1/systemone` → 404; catalog has chat-only `typesafe/jev-router` (logprobs available) | `OPENROUTER_API_KEY` | — | NOT in this bead; see §4 |

Wire contract identical across all three: `{model, state, questions} →
{model, answers: {qid: {type, choice, probabilities, confidence}}, usage}`
(ollama OpenAPI + zen's table + typesafe docs agree; `answers` is the wire
name, `.choices` is SDK-side only). Constraints: requests ≤ 64 KiB; ≥ 2, ≤ 26
choice criteria; ties → first option in request order; no streaming/tools.

Local facts: `nimble` pulled and answering on this host; `laya-gguf` is NOT
scoring-capable (400: "use a local Nimble or Tev GGUF model") — the ollaya
chat-judge's model is irrelevant here. The typesafe-sdk has NO base_url
support (grep: zero hits) → zen/ollama cannot reuse it.

## 1. Provider architecture (spec grammar `provider/model` preserved)

- `systemone/jev` — unchanged (typesafe-sdk, cloud, key-gated).
- NEW shared `SystemOneWireClient`: small httpx client for the common wire
  contract (one POST; explicit timeout constant documented like the F4
  review item; typed errors wrapping transport/HTTP/shape failures as
  `JevJudgeError`; key/credential errors stay `JudgeProviderUnavailableError`
  at resolution). httpx must be added to the `systemone` extra requirements
  (already in the joint graph via `web` — rule 10 safe).
- NEW `zen/<model>` provider (default model `jev-1.13-free` for tests;
  `jev-1.13` fully valid): base `https://opencode.ai/zen/v1/systemone`,
  `OPENCODE_API_KEY` → `JudgeProviderUnavailableError` at resolution when
  absent. Auth header per Zen docs (verify: Bearer).
- NEW `ollama/<model>` provider (default `nimble`): base
  `http://localhost:11434/v1/systemone`, NO key — local-only by contract
  (same local-only discipline as the classifier A4: resolution must not
  require network; rank() obviously does).
- `systemone/jev` — unchanged (typesafe-sdk, cloud, key-gated).
- **ALL endpoints go through typesafe-sdk** (user decision 2026-10-01: no
  hand-rolled HTTP; one wrapper covers everything — `TypeSafeClient` ctor
  takes `base_url`, `api_key`, `model`; `TYPESAFE_BASE_URL` env exists but
  the provider passes explicit ctor args):
  - zen: `base_url="https://opencode.ai/zen"` (SDK appends `/v1/systemone`)
    - `api_key=OPENCODE_API_KEY` → `JudgeProviderUnavailableError` at
    resolution when absent. Auth header per Zen docs (verify: Bearer).
  - ollama: `base_url="http://localhost:11434"` + `api_key="local"` (the SDK
    validates a non-empty key; ollama ignores the header — sentinel
    documented in the provider). NO env key required — local-only by
    contract (same local-only discipline as the classifier A4).
- No new dependency, no httpx additions to the extra; the F4-documented SDK
  timeout/retry bounds apply uniformly to all three endpoints (a consistency
  win over per-client transports).

## 2. Judge state gains the machine section

`JevJudge` state becomes `{intent, candidates: {name: capabilities},
machine: {vram_budget_gb}}` — bounded, no document content. Rationale: among
ELIGIBLE candidates (eligibility stays code-owned, ADR d3), hardware fit is a
legitimate ranking signal; this is what makes the three-profile test
meaningful at judge level. Memo key unchanged (machine profile is constant
within one plan).

## 3. Tiered tests (all in the existing `-m judge` tier, each skip-gated)

1. Offline (no tier): httpx-transport stubs mirroring the SDK-stub
   discipline; provider table, spec parsing, missing-key paths (zen errors,
   ollama needs none), wire-shape error mapping, memo interplay unchanged.
2. Semi-live `ollama/nimble` — skip unless `GET /api/version` answers AND
   `/v1/systemone` accepts the model (probe, don't assume). Runs rank() under
   THREE machine profiles (user ask b):
   - real: `probe_environment()` values;
   - better dummy: `vram_budget_gb=32.0` (GPU host);
   - worse dummy: `0.0` (CPU-only).
   Assertions: contract-valid orderings (permutation, ghosts dropped, memo
   dedup ≤ 1 request per (intent, names) shape) under all three; deterministic
   code-owned eligibility deltas (GPU candidates absent from the eligible set
   at 0.0 — planner behavior, not judge); ONE behavioral anchor asserted (the
   pc-1 smoke: on the CPU-only profile, a GPU-only OCR candidate must not
   rank first — anchored at confidence ≥ 0.9 in the smoke, opt-in tier so a
   model update flipping it fails loudly, not silently). All three orderings
   printed for human inspection.
3. Live keys — each skips on its own env var: `systemone/jev`
   (TYPESAFE_API_KEY, existing), `zen/jev-1.13-free` (OPENCODE_API_KEY;
   the free model — no billing side effect). OPENROUTER_API_KEY: NOT wired
   (§4).

## 4. OpenRouter — deferred, evidence recorded

`/api/v1/systemone` 404s; only `typesafe/jev-router` (chat + logprobs)
exists. A chat-completions forced-choice scorer is a DIFFERENT adapter
(prompt synthesis, logprob aggregation, weaker typing, flakier). Not in this
bead. Follow-up option if wanted: `openrouter/jev-router` chat-based adapter
as its own bead after this one lands.

## 5. Docs

- cli.md/auto-routing.md: the judge provider list gains zen + ollama (ollama
  = local, no key, semi-live tier); the "may use the network" phrasing gains
  "except ollama, which is local by definition".
- providers/AGENTS.md: three entries, shared wire client, per-platform auth.
- The existing systemone provider tests' stub fidelity notes stay valid.

## 6. Acceptance

`mise all` green (offline tests + 100% coverage — live/semi tiers skip
without daemon/keys); semi-live tier demonstrated once by the implementer
against the local daemon (paste the three orderings into the completion
report); zen live tier skipped here (no key) — first real run happens on the
user's machine with OPENCODE_API_KEY set.
