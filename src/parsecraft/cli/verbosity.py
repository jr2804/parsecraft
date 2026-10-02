"""Third-party output policy: quiet by default, everything with ``--verbose``.

A conversion drives libraries that are chatty on stderr — transformers'
tokenizer/generation advisories and hub/weight-loading progress bars, and the
odd download log line. None of it is our output, and a page of it buries the one
line that matters. So the CLI quiets those libraries unless the caller asks for
raw output; it never suppresses its own messages, and it never touches stdout
(the IR and the ``--json`` payload stay intact).

Two mechanisms, because they cover different moments:

- **Environment defaults**, applied before any backend imports its stack, which
  is the only way to reach libraries that read their verbosity at import time
  (``TRANSFORMERS_VERBOSITY``, ``HF_HUB_DISABLE_PROGRESS_BARS`` — the latter also
  gates transformers' own weight-loading bars).
- **Logger levels**, for whatever is already imported and for libraries that
  ignore the environment.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

#: Library defaults applied before a backend imports its stack, unless verbose.
QUIET_ENV: dict[str, str] = {
    "TRANSFORMERS_VERBOSITY": "error",
    "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
}

#: Third-party loggers held at ERROR unless verbose. Errors still get through;
#: INFO/WARNING chatter does not. Our own loggers are never in this list.
QUIET_LOGGERS: tuple[str, ...] = (
    "transformers",
    "huggingface_hub",
    "filelock",
    "urllib3",
    "httpx",
    "docling",
    "nvidia",
)


@contextmanager
def third_party_output(*, verbose: bool) -> Iterator[None]:
    """Suppress third-party library chatter for the duration, unless ``verbose``.

    ``verbose`` suppresses nothing at all: it leaves the environment and every
    logger exactly as it found them. Otherwise the quiet defaults are installed
    and restored afterwards, so a library import later in the process still sees
    the state the caller had.
    """
    if verbose:
        yield
        return
    previous_env = {name: os.environ.get(name) for name in QUIET_ENV}
    os.environ.update(QUIET_ENV)
    loggers = {name: logging.getLogger(name) for name in QUIET_LOGGERS}
    previous_levels = {name: logger.level for name, logger in loggers.items()}
    for logger in loggers.values():
        logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        for name, level in previous_levels.items():
            loggers[name].setLevel(level)
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
