"""R-024-8 (programme 024, `T2414.1`): the process environment is read in one place, `app/config.py`.

A FORM guard (AGENTS.md §11). It parses the source of `app/` and flags `os.environ`, `os.getenv` and
`from os import environ/getenv` outside `app/config.py`. An environment read beside `Settings` is a second
configuration channel: it ignores `.env`, has its own default and its own parsing, and does not appear in
`Settings` at all (`SIMULATOR_ACTIONS_ENABLE`, the Interact-mode flag, was one).

What it does not see: `os` bound to another name, reads through a third-party library, and subprocess
environments. `tests/unit/test_p024_settings_have_no_phantom_keys.py` holds the other half (no read of a key
`Settings` lacks).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.p019_support import TargetMismatch, require_target

APP = Path(__file__).resolve().parents[2] / "app"
_ENV_NAMES = {"environ", "getenv", "environb", "getenvb"}


def _findings(tree: ast.AST, where: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in _ENV_NAMES
        ):
            found.append(f"{where}:{node.lineno} os.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in _ENV_NAMES or alias.name == "*":
                    found.append(f"{where}:{node.lineno} from os import {alias.name}")
    return found


def test_the_guard_sees_each_shape_it_names() -> None:
    """Anti-vacuum: each shape, planted, is found; `os.path` is not an environment read."""

    for source in (
        'os.getenv("X", "1")',
        'os.environ.get("X")',
        'os.environ["X"]',
        "from os import getenv",
        "from os import environ as e",
    ):
        assert len(_findings(ast.parse(source), "planted")) == 1, source
    assert _findings(ast.parse('os.path.join("a", "b")'), "clean") == []


@pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="024 T2414.1: environment read beside Settings")
def test_no_environment_read_outside_config() -> None:
    config = APP / "config.py"
    files = [path for path in sorted(APP.rglob("*.py")) if path != config]
    # The scan reaches the code.
    assert len(files) > 100, len(files)
    found: list[str] = []
    for path in files:
        found += _findings(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(APP.parent).as_posix())
    require_target(not found, "environment read outside app/config.py:\n" + "\n".join(found))
