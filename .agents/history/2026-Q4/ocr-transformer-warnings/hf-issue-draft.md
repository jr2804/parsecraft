# Upstream issue draft — `ImageTextToTextPipeline` warns about its own generation arguments

**Status: DRAFT — not filed.** Filing needs the user's explicit text approval.

**Verification status (2026-10-02):** reproduced on **transformers 5.18.0**, which is
the **latest release on PyPI** at the time of writing, with a **public 0.5B model**
and **zero generation arguments from the caller**. Both advisories are live; this
is not a stale report. Source references below were re-read in the installed
5.18.0 tree, not from memory.

Target repo: `huggingface/transformers`. Intended title:

> `ImageTextToTextPipeline` warns about generation arguments it injects itself
> (`generation_config` + `return_dict_in_generate`, and `max_new_tokens` vs
> `max_length`)

---

## System Info

- `transformers` version: **5.18.0** (latest on PyPI; `pip install -U transformers` resolves to it)
- Platform: Windows 11, Python 3.14.2, `torch` 2.13.0+cpu (CPU-only run; not device-specific)
- Python version: 3.14.2
- Who can help: @zucchini-nlp (pipelines / image-text-to-text)

## Reproduction

Model: `llava-hf/llava-onevision-qwen2-0.5b-ov-hf` (any image-text-to-text model
shows the same; this one needs `torchvision` for its video processor).

```python
from transformers import AutoModelForImageTextToText, AutoProcessor, pipeline

model_id = "llava-hf/llava-onevision-qwen2-0.5b-ov-hf"
processor = AutoProcessor.from_pretrained(model_id)
model = AutoModelForImageTextToText.from_pretrained(model_id, dtype="auto", device_map="cpu")
pipe = pipeline("image-text-to-text", model=model, processor=processor)

# The documented chat path: the conversation is `text=`, images live inside `content`.
messages = [
    {
        "role": "user",
        "content": [
            {"type": "image", "url": "path/to/image.png"},
            {"type": "text", "text": "Describe this image briefly."},
        ],
    }
]

out = pipe(text=messages)          # NOTE: no generation arguments at all
```

The call succeeds, and prints on the default `transformers` logger (WARNING):

```text
Passing `generation_config` together with generation-related arguments=({'return_dict_in_generate'}) is
  deprecated and will be removed in future versions. Please pass either a `generation_config` object
  OR all generation parameters explicitly, but not both.
Both `max_new_tokens` (=256) and `max_length`(=20) seem to have been set. `max_new_tokens` will take
  precedence. Please refer to the documentation for more information.
  (https://huggingface.co/docs/transformers/main/en/main_classes/text_generation)
```

(Each message is emitted as one line; wrapped here for the repository's
Markdown line limit.)

Passing an explicit budget widens the first warning and changes the second,
still without the caller setting both values:

```python
pipe(text=messages, max_new_tokens=8)
# Passing `generation_config` together with generation-related arguments=
#   ({'return_dict_in_generate', 'max_new_tokens'}) is deprecated ...
# Both `max_new_tokens` (=8) and `max_length`(=20) seem to have been set. ...
```

Neither the model repository nor the caller sets either length:

```python
from transformers import AutoConfig, GenerationConfig
config = AutoConfig.from_pretrained(model_id)
GenerationConfig().max_length, GenerationConfig().max_new_tokens                     # (None, None)
GenerationConfig.from_model_config(config).max_length                                # None
GenerationConfig.from_pretrained(model_id).max_length                                # None
```

## Expected

A call that passes no generation arguments should not warn about generation
arguments, and should not be told that both `max_new_tokens` and `max_length`
were set when the pipeline chose both of them.

## Actual

Both warnings are emitted by the pipeline's own call path. Every argument named
in them is injected by the pipeline:

1. **`generation_config` together with `return_dict_in_generate`** —
   `pipelines/image_text_to_text.py:391-394` attaches the pipeline's generation
   config *and* a loose `return_dict_in_generate` to the same `generate()` call:

   ```python
   # User-defined `generation_config` passed to the pipeline call take precedence
   if "generation_config" not in generate_kwargs:
       generate_kwargs["generation_config"] = self.generation_config
   generate_kwargs["return_dict_in_generate"] = False
   ```

   `generation/utils.py:2099-2106` then warns about exactly that combination,
   because it cannot tell pipeline-injected kwargs from caller kwargs.

2. **`max_new_tokens` (256) and `max_length` (20)** — the pipeline declares its
   own default (`pipelines/image_text_to_text.py:122-124`):

   ```python
   _default_generation_config = GenerationConfig(
       max_new_tokens=256,
   )
   ```

   `pipelines/base.py:895-909` merges that with the model config and stores the
   result as `self.generation_config`. `max_length` keeps generation's global
   default of 20 — the code names it as such at `pipelines/base.py:907`
   (`self.generation_config.max_length != 20  # global default`) while leaving it
   set — so the stored config carries *both* values, and
   `generation/utils.py:2004-2012` warns on the pair for every call.

## Relation to #47752 / #47953

PR #47953 ("preserve model.generation_config precedence over pipeline defaults")
fixed *which* value wins when a model and a pipeline default disagree; the merge
shape it introduced is visible at `pipelines/base.py:890-894`
(`defaults_only=True`). It does not cover either case above:

- the stored config still contains two competing length settings that the
  pipeline itself created (no model, no caller involved), and
- the pipeline still passes a `generation_config` alongside a loose generation
  kwarg.

Both remain reproducible on 5.18.0 with zero caller arguments, i.e. after that
fix landed.

## Suggested fixes

1. Pass `return_dict_in_generate` **inside** the generation config the pipeline
   builds (or stop attaching a config and pass generation parameters
   explicitly), so the pipeline never triggers its own deprecation path.
2. Make the pipeline's default internally consistent: when the pipeline's
   `max_new_tokens` default is in effect, leave `max_length` unset (or clear it
   in the same place that already compares against the `20` global default at
   `pipelines/base.py:899-909`), so exactly one length governs and no caller
   sees a "both were set" advisory about values they never set.
3. Use `logger.warning_once` for the length-pair advisory as well (the
   deprecation already is): it currently repeats per `generate()` call, so a
   per-image or per-page loop floods the log.

## Notes

- The advisories are emitted at WARNING level by the `transformers` logger, so
  they are the only library output left in a run that has already silenced
  progress bars; a multi-page or multi-image loop repeats them once per call.
- A third advisory appears on the same path — "The input data was not formatted
  as a chat with dicts containing 'role' and 'content' keys" — only when the
  caller passes a pre-templated **string**; the chat path above does not trigger
  it, so it is not part of this report.

---

*Note: This issue was drafted with AI assistance and reviewed by the actual user.*
