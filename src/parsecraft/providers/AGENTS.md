# AGENTS.md — src/parsecraft/providers/

## Purpose

Judge provider plug-ins resolved by `routing/judge_providers.py`: each module
turns a `JudgeSpec` into a `RoutingJudge`.

## Ownership

- `ollaya.py` — the ollama daemon provider (`load_judge`, the verified wire
  format, `OllayaJudge`, `OllayaJudgeError`).

## Local Contracts

- Every provider module exports `load_judge(spec: JudgeSpec) -> RoutingJudge`
  and is imported lazily (via `importlib`) on first resolve — top-level
  imports stay light (stdlib/core only); network and heavy work happens in
  `load_judge` or at call time.
- A judge only re-ranks candidates `plan_route` already deemed eligible —
  never widen, duplicate, or invent names.
- Daemon/config reading happens at call time (`OLLAYA_BASE_URL`), never at
  import; failures raise `OllayaJudgeError` (a `RoutingError`) — never fall
  back silently to a different ordering.

## Verification

`mise test` — `tests/test_routing_ollaya.py` (wire tests run offline; the
`judge` tier needs `pytest --run-judge` plus a live daemon).

## Child DOX Index

None — leaf boundary.
