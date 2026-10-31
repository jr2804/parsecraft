---
title: Attribute captions to tables and figures
---

`attribute_captions` is an opt-in post-processing step that asks a Jev
decision model which paragraphs caption which tables or figures, and records
the answer as `caption_of` relations on the paragraph chunks. It never
creates, deletes, reclassifies, rewrites, or copies chunks, and the rendered
Markdown is identical with or without it (relations are not rendered). There
is no CLI flag and no executor integration: off means never calling it.

## Invoke it after conversion

Call it on the `document` of a pipeline result, then cache or forward the
document as usual. Attribute-then-cache keeps the stored document stable;
the function itself claims no cached reproducibility:

```python
from parsecraft.adapters.caption import attribute_captions, link_same_span_captions

report = attribute_captions(result.document, model="typesafe-ai/jev-latest")
linked = link_same_span_captions(result.document)
```

`link_same_span_captions` is the deterministic companion: it links a
`CAPTION` chunk that shares its exact source span with an adjacent
`TABLE`/`FIGURE`, with no model and no network. The two functions are
separate; callers compose them explicitly.

## Select the model

`model` is a provider/model spec reusing the judge chain (`--judge` syntax):
an explicit argument, else the generic environment variable, else the
configured default. Only Jev-serving providers are supported
(`typesafe-ai`, `zen`); the local endpoint is refused rather than
substituted. Model tokens stay verbatim per provider doctrine and the base
URL comes from the standard chain — see the Judge providers section of the
[CLI reference](../reference/cli.md) for the option forms.

One batched System One request covers the whole document: every undecided
paragraph-before-table/figure pair is judged in a single call. Pairs already
linked are skipped, so a repeat call asks fewer questions and never
duplicates relations. Each pair links only on a strictly well-formed answer
(exact option labels, finite in-range scores summing near unity, a unique
maximum naming the returned choice); anything else abstains.

## Relations point paragraph to target

Each linked pair appends one `caption_of` relation **on the paragraph chunk**,
naming the table or figure chunk id. Chunks keep their kinds, text, and
metadata untouched, so consumers that forward relations but keep their own
prose see no content change — only new references from caption paragraphs to
their targets.

## Privacy

Each pair sends bounded text snippets (target and candidate paragraph,
truncated to documented caps with truncation flagged) to the configured
endpoint. Cloud endpoints traverse the network; the request carries no
credentials beyond the endpoint's own authentication. Never log or print
secrets around these calls.

## Failures and caching

The call returns a `CaptionReport` with attributed/abstained counts plus an
optional `CaptionFailure`: `unconfigured` (no spec anywhere), `invalid_spec`
(malformed spec), `unsupported_provider` (not a Jev provider),
`unavailable` (missing credential, unreadable config, broken install), or
`transport_error` (call or response unusable). On any failure the document
remains unchanged — failures never fabricate document signals.

Caller owns the caching model: run attribution after `execute`/cache at your
discretion. A run that abstains everywhere reports its abstention count; the caller decides
how to handle it.

## API reference

Full signatures and strictness rules are in the [API reference](../reference/api.md).
