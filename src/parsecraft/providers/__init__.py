"""Judge providers — plug-in modules resolved by ``routing.judge_providers``.

Each provider module exports ``load_judge(spec: JudgeSpec) -> RoutingJudge``
and is imported lazily (via ``importlib``) only when its spec resolves, so
provider dependencies stay outside the core import graph. See ``AGENTS.md``
in this directory for the contract.
"""

from __future__ import annotations
