#!/usr/bin/env python3
"""Script to generate credits from pyproject.toml dependencies."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()


def load_credits() -> str:
    """Load credits from pyproject.toml."""
    pyproject = ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    deps = data.get("dependency-groups", {}).get("dev", [])
    lines = ["# Credits", "", "The following packages are used to generate this documentation:", ""]
    for dep in deps:
        if isinstance(dep, str) and not dep.startswith(("-", "#")):
            pkg_name = dep.split("[")[0].split(">=")[0].strip()
            lines.append(f"- [{pkg_name}](https://pypi.org/project/{pkg_name}/)")

    return "\n".join(lines)


if __name__ == "__main__" or "__file__" not in globals():
    print(load_credits())
