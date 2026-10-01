# Vendored model code — ported GPU-tier numerics, upstream style rules relaxed

Processed by every gate, not excluded from any: ruff (`ruff.toml`, upstream
style families relaxed), codespell (`pyproject.toml`), pyreorder
(`.config/pyreorder.toml`) and ty (`ty.toml`, heavy imports carry per-line
ignore markers) as of pc-4u7.36. Every file here is third-party modeling
code, byte-verified against the descriptor's pinned revision at vendoring
time, plus the minimal patches listed below. Patch any
problem in this README's list — never silently edit, never reformat.

## Why

Root `AGENTS.md` rule 10: `uv sync -U --all-extras --all-groups --all-packages`
must always succeed, so all four OCR extras share one transformers window
(`transformers>=5.17,<6`). Two model repos shipped `trust_remote_code` modeling
code written against transformers 4.x that no longer loads on 5.x. Vendoring a
patched copy (loaded explicitly, `trust_remote_code` dropped) keeps the unified
window without pinning an extra out — the sanctioned fix.

## Sources

| file | repository @ revision | licence |
| --- | --- | --- |
| `naviocr/modeling_naviocr.py` | `StarDoc-AI/TeleOCR` (now `XingChen-AGI/TeleOCR`) @ `a61433186527cb53958bf354e34ae673c19cec4b` | Apache-2.0 (model-card `license`; file header states it is generated from HF `transformers` qwen2_5_vl, also Apache-2.0) |
| `unlimited/*.py` (5 files) | `baidu/Unlimited-OCR` @ `07dea832e22aefee32ad281d4b80551282e1c168` | MIT (model-card `license: mit`) |

Original sha256 values equal the `ModelAssetDescriptor.file_pins` entries for
the same paths (`backends/ocr/_models.py`) — all six matched at vendoring
(2026-09-28): `modeling_naviocr.py` `7adac1cc…`, `modeling_unlimitedocr.py`
`268bdcbe…`, `modeling_deepseekv2.py` `74e36e6b…`,
`configuration_deepseek_v2.py` `b8470dd6…`, `deepencoder.py` `0ae2fb6d…`,
`conversation.py` `ec7b6ce8…`.

## Patches (all transformers-5.x ports, pc-4u7.36)

Recorded here because ported code is first-party: linted, formatted, and
superseded only by editing both this list and the code together.

1. `naviocr/modeling_naviocr.py` — rope: the classic `"default"` rope left
   `ROPE_INIT_FUNCTIONS`; a `compute_default_rope_parameters` hook replaces it
   and reads theta from v5's `rope_parameters` dict (flat `rope_theta` is gone).
2. `naviocr` — `_tied_weights_keys` v4 list → v5 `{target: source}` mapping
   (`lm_head.weight` → `model.language_model.embed_tokens.weight`).
3. `naviocr` — `mm_token_type_ids` accepted in `forward` (the v5 processor
   emits it; the v4-era model ignores it, but generate's kwargs validation
   requires the parameter) and `cache_position` rebuilt in
   `prepare_inputs_for_generation` when v5 no longer seeds it.
4. `naviocr` — `create_causal_mask` kwargs renamed/trimmed for v5
   (`input_embeds` → `inputs_embeds`; `cache_position` removed).
5. `unlimited/modeling_deepseekv2.py` — `is_torch_fx_available` shim
   (`try/except ImportError`, returns `False`: removed in v5).
6. `unlimited/modeling_deepseekv2.py` — `DeepseekV2ForCausalLM` now inherits
   `GenerationMixin` (v5 `PreTrainedModel` no longer provides `generate`).
7. `unlimited/modeling_unlimitedocr.py` — `UnlimitedOCRConfig` gets an explicit
   `__init__` delegating to `DeepseekV2Config` (the v5 config mixin skips the
   inherited explicit `__init__` for subclasses that define none).
8. `unlimited` — prefill mask extended to the fused sequence length and
   `position_ids` trimmed to the decode input in
   `prepare_inputs_for_generation` (v5 seeds both differently; the fusion adds
   one token over the prompt ids and v5 passes full-history `position_ids`).
9. `unlimited` — ring/sliding attention disabled (`_ring_window` no longer
   set): the ring path double-counts v5 cache bookkeeping (kv 280/281/561
   mismatch during decode). Standard attention + cache is used instead — a
   performance note for very long prompts, not a correctness loss.
10. Style-only sweep: codespell fixes, `assert` → `raise AssertionError`,
    `eval` → `ast.literal_eval`, `zip(..., strict=False)`, underscore-prefixed
    unused loop vars, plus one real `logger` NameError fixed in `deepencoder`.

Loaded by `backends/ocr/_tele_impl.py` and `backends/ocr/_unlimited_impl.py`
as explicit classes (`from_pretrained(...)` on our imports) — the model
directories' `auto_map`/`trust_remote_code` path is never taken.
