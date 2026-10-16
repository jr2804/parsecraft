"""Backend option values: one coercion, shared by the CLI and the adapters.

Lives in ``backends`` rather than ``pipeline`` on purpose: a backend adapter must
not import the pipeline layer (the pipeline imports backends, so it would invert
the dependency). The pipeline re-exports it for callers that already import from
there.

``--backend-option KEY=VALUE`` arrives as text, while an adapter's validator
often needs a real type (``isinstance(raw, bool)``). Coercing per adapter made
the same CLI surface behave differently per backend — ``image_analysis=true``
died inside MinerU while ``extract_vector_graphics=true`` worked because
LiteParse coerced internally. So the coercion lives here once, and both the CLI
(where the string is parsed) and the adapters (when a library caller passes a
string) read the same rule.
"""

from __future__ import annotations

#: Tokens accepted as booleans, in the spellings operators actually type.
_TRUE_TOKENS = frozenset({"true", "yes", "on"})
_FALSE_TOKENS = frozenset({"false", "no", "off"})


def coerce_option_value(value: str | int | float | bool) -> str | int | float | bool:
    """Turn an option's text into the type it obviously means.

    ``"true"/"yes"/"on"`` → ``True``, ``"false"/"no"/"off"`` → ``False``, a
    numeric string → ``int``, anything else stays the raw string (an adapter's
    own validator is the judge of what a non-obvious value means, and a value it
    rejects must stay recognisable in its error message). Non-string values pass
    through untouched, so a library caller's real ``bool``/``int`` is never
    second-guessed.
    """
    if not isinstance(value, str):
        return value
    lowered = value.strip().lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
