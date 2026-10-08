# Plan — OCR transformer warnings (beads `pc-scr`, `pc-c8t`)

Fix plan for the five transformers warnings seen in a real OCR run. Two beads,
filed 2026-10-02; both are `bug`, P2, `discovered-from:pc-1ow`.

Evidence (verbatim, from the user's console on 2026-10-01) and call sites are in
the bead descriptions — this document is the how, not the what. Re-read the
beads before starting (`bd show pc-scr`, `bd show pc-c8t`).

## Starting conditions

- Work happens in the **heavy** OCR environment, but NEVER by syncing the
  shared `.venv` — a heavy sync flips `# ty: ignore` markers to "unused" and
  leaves the tree red for everyone else (twice burned, see memory #1097).
  Use an isolated env instead: `uv run --isolated --extra ocr-qianfan ...`
  (same pattern as the judge tier). If a shared sync is unavoidable for
  debugging, `mise dev` restores the light canonical state afterwards — the
  FINAL gate must always run on the canonical env.
- The warnings only fire on the *transformers* runtime path (`ocr-qianfan`,
  `ocr-ovis`, `ocr-tele`, `ocr-unlimited`), never on `runtime="vllm"`.
- Default CLI output already suppresses library chatter (`cli/verbosity.py`).
  That is why these were easy to miss in a normal run — and why `--verbose` is
  the way to reproduce them.

## Reproduction (before touching anything)

```powershell
uv sync --extra ocr-qianfan
uv run parsecraft convert "tmp/T-REC-P.863.2-202405-I!!PDF-E.pdf" --backend ocr-qianfan --verbose
```

Record the warnings and the page text (a baseline artefact to diff later). Keep
the model cache warm between runs — cold load is ~minutes.

## pc-scr — `fix_mistral_regex=True` at tokenizer/processor load

Steps (as landed in 52167ac — the fallback originally planned here was
DROPPED during implementation and ratified in review: `TRANSFORMERS_RANGE`
(`>=5.17,<6`) guarantees the kwarg, verified at both window ends and through
`AutoProcessor` forwarding, so a `TypeError` fallback would be unreachable
defensive code):

1. Verify the flag's acceptance **per loader** in the installed transformers:
   - `AutoTokenizer.from_pretrained(..., fix_mistral_regex=True)` (unlimited path),
   - `AutoProcessor.from_pretrained(..., fix_mistral_regex=True)` (tele path),
   - `pipeline(task="image-text-to-text", ...)` (qianfan/ovis path). SETTLED:
     `pipeline()` cannot forward the flag (`_resolve_processor` passes only
     hub/model kwargs) — the processor is loaded explicitly and the INSTANCE
     is handed to `pipeline()`.
2. ONE helper in `_common.py` (`tokenizer_load_kwargs()`) returning the shared
   kwarg dict — NO retry/fallback (see ratification note above); the guarantee
   is documented at `TOKENIZER_FIX_KWARGS` and asserted in tests.
3. Wire it into the three sites (`load_transformers_pipeline` via
   `processor_loader=AutoProcessor.from_pretrained`, `_tele_impl`,
   `_unlimited_impl`) without duplicating the kwarg.
4. Offline stub tests: assert the kwarg reaches every loader (exact tuples);
   no fallback-path test exists by design.
5. Live: re-run the reproduction; the regex warning is gone (done once against
   the released pinned model; impact on token counts honestly zero across a
   33-sample battery — recorded in the bead).

Risks / open questions:

- If `pipeline` cannot forward the flag, the fix for that path is a separate
  tokenizer load — check that it does not break the processor's image handling.
- The flag is global, not per-model. If a model repo genuinely wants its own
  (broken) regex, we still override: our tokens come from the same templated
  conversation the model expects, and the warning explicitly says the pattern is
  wrong. Record the decision in the bead.

## pc-c8t — chat `messages` + explicit generation settings

**OUTCOME (2026-10-02, transformers 5.18.0): (a) and (b) were dropped as code
changes, (c) landed as a one-line budget pin.** The bead's premise did not
survive contact with the installed source — see the bead notes for the full
evidence, and `hf-issue-draft.md` in this directory for the upstream report.

What the investigation found (all verified, not inferred):

- **(b) `generation_config` + `return_dict_in_generate` is upstream and
  unreachable from our code.** `generation/utils.py:2099-2106` warns on any
  non-model kwarg alongside a config; `ImageTextToTextPipeline` itself sets
  `generate_kwargs["return_dict_in_generate"] = False`
  (`pipelines/image_text_to_text.py:394`) while passing a config. It fires even
  when the caller passes no generation arguments at all. Nothing to fix here.
- **(c) `max_new_tokens`/`max_length` are the pipeline's own defaults, not
  ours.** A traced `model.generate` shows the config carrying
  `max_new_tokens=256` (the pipeline's documented default) and `max_length=20`,
  while every `GenerationConfig` entry point reports `max_length=None` for this
  model. Our explicit budget already wins. The advisory cannot be silenced
  without abandoning the pipeline path — so the resolution is *explicitness*,
  not silence: both transcribers now pass
  `_common.DEFAULT_PAGE_MAX_NEW_TOKENS` when the request names no
  `max_context_tokens`, so the page budget is ours and not an upstream default.
- **(a) the chat-messages rewrite was DROPPED.** `messages=` is not this
  pipeline's API ("You must provide text for this pipeline"), and the real chat
  path passes the conversation as `text=` and *rejects* a separate `images=`
  argument (images must live inside `content`). That means the change also moves
  chat templating into the pipeline and re-derives the output shape
  (assistant-message assembly / `input_text.messages`), i.e. a rewrite of a
  working path to silence one advisory — disproportionate, and unverifiable
  end-to-end on this host, where the CPU-only torch build makes our own
  `GPU_REQUIRED` fail-fast refuse these backends through the product path
  (direct-transformers probes are not product-path evidence).

**Landed instead:** the `DEFAULT_PAGE_MAX_NEW_TOKENS` pin in `_common.py` (one
number for both the transformers and vLLM transcribers, documented as sized for
a dense page, with the pipeline's 256 default called out as an implementation
detail), plus the test that pins it and the `backends/AGENTS.md` bullet.

**Original plan (kept for the record; sub-problems in the order they were
tackled):**

Sub-problems, smallest first:

Sub-problems, smallest first:

**(c) max_new_tokens vs max_length** — decide and single-source. Options:

- pass a `generation_config` built from the model's own with our token budget
  (then stop passing loose generation kwargs), or
- pass explicit arguments only, including an explicit `max_length` override.

Investigate the truncation question the warning raises: is 256 enough for a
dense page (`request.max_context_tokens`)? If a page needs more, the budget is a
conversion-quality bug, not cosmetics.

**(b) `generation_config` + `return_dict_in_generate`** — reproduce and attribute
before changing anything. We pass only `max_new_tokens`; find who adds
`return_dict_in_generate` (pipeline internals vs the model's custom code vs a
config asset). If it is upstream, record the evidence and report upstream; do not
paper over it locally.

**(a) chat `messages`** — the largest change; do it last, on top of a decision
from (b)/(c):

1. Verify on 5.17/5.18 that `pipe(messages=[...], ...)` works for
   image-text-to-text, with the image content part present
   (`{"type": "image"}` before the text part — the reason `chat_prompt()` exists;
   a raw prompt yields `Image features and image tokens do not match, tokens: 0`).
2. Check whether the pipeline still echoes the prompt into `generated_text`
   (`extract_generated` + `removeprefix(prompt)` may become unnecessary or wrong).
3. Check `enable_thinking=False` for Ovis: today it is a `chat_prompt()` kwarg with
   a `TypeError` retry; with `messages` it likely moves to
   `chat_template_kwargs`. Verify on both templates (with and without the switch).
4. Delete `chat_prompt()` **for the pipeline path** if `messages` covers it; keep
   the vLLM path as it is (string prompt — different runtime).
5. Update the `ImageTextPipeline` protocol (`_common.py:141`) and the offline
   stubs to record `messages`, then assert on that shape in tests.

## Shared verification

- Offline: `mise all` exit 0 (100% coverage; stubs only — no model downloads).
- Live (heavy env), for each touched backend: `--verbose`, no target warning, and
  page text diffed against the recorded baseline; `parsecraft inspect` unaffected.
- `mise dev` afterwards, then `mise all` again — confirms the light canonical env
  still passes (this is the state the repo ships and CI gates).

## Ordering

`pc-scr` is independent and small — do it first (it also exercises the heavy-env
setup the second bead needs). `pc-c8t` depends on nothing else, but its three
sub-problems should land in the order above, ideally as separate commits.
