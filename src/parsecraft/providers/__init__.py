"""Provider plug-ins for the optional routing seams.

Each provider module exports its seam loader — ``load_judge(spec: JudgeSpec) ->
RoutingJudge`` (resolved by ``routing.judge_providers``) or
``load_classifier(spec: ClassifierSpec) -> PageOcrClassifier`` (resolved by
``routing.classifier``) — and is imported lazily (via ``importlib``) only when
its spec resolves, so provider dependencies stay outside the core import graph.
See ``AGENTS.md`` in this directory for the contract.

``BUILTIN_JUDGE_PROVIDERS`` is the declared set the CLI catalog enumerates
(``parsecraft judges``); each provider module owns its own model tokens,
credential, endpoint and extra so the command restates nothing.
"""

from __future__ import annotations

from pydantic import BaseModel

#: Built-in judge provider tokens, in display order. A token names the
#: **provider** (who serves the model), never the model type it serves:
#: ``typesafe-ai`` serves System One models, ``zen`` serves the same wire, and
#: ``ollama`` serves a local daemon.
BUILTIN_JUDGE_PROVIDERS: tuple[str, ...] = ("typesafe-ai", "zen", "ollama")


class JudgeProviderProfile(BaseModel):
    """What a judge provider declares about itself (spec spelling, requirements).

    One ``PROFILE`` per provider module is the single home for its model tokens,
    credential, endpoint and extra: the loader resolves from it, ``parsecraft
    judges`` prints it, and the docs table is checked against it, so no fact is
    restated in a second place.
    """

    name: str
    extra: str
    models: tuple[str, ...] = ()
    model_placeholder: str | None = None
    example_models: tuple[str, ...] = ()
    api_key_env: str | None = None
    base_url: str | None = None

    @property
    def specs(self) -> tuple[str, ...]:
        """The spec model tokens this provider accepts, in copy-paste spelling.

        Open-ended providers (a local daemon serving any compatible model)
        declare ``model_placeholder`` instead of a fixed list.
        """
        if self.models:
            return tuple(f"{self.name}/{model}" for model in self.models)
        return (f"{self.name}/{self.model_placeholder}",)
