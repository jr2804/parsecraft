"""Provider plug-ins for the optional routing seams.

Each provider module exports its seam loader — ``load_judge(spec: JudgeSpec) ->
RoutingJudge`` (resolved by ``routing.judge_providers``) or
``load_classifier(spec: ClassifierSpec) -> PageOcrClassifier`` (resolved by
``routing.classifier``) — and is imported lazily (via ``importlib``) only when
its spec resolves, so provider dependencies stay outside the core import graph.
See ``AGENTS.md`` in this directory for the contract.
"""

from __future__ import annotations
