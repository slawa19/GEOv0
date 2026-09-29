"""R-024-7 (programme 024, `T2414.1`): a setting is read from `Settings`, by a key it has, with no second default.

A FORM guard (AGENTS.md §11). It parses the source of `app/` and checks two shapes:

* `getattr(settings, "KEY", ...)` with a literal key - the call site carries its own default, which drifted
  from the `Settings` default in several places (`ROUTING_PATH_FINDING_TIMEOUT_MS` 50 against 500,
  `COMMIT_RETRY_ATTEMPTS` 1 against 3) and named one key `Settings` does not have
  (`BALANCE_SUMMARY_CACHE_MAX_ENTRIES`: with `extra="ignore"` its environment variable was dropped
  silently and the literal was the only value it ever had). Direct access `settings.KEY` is the fix;
* `settings.KEY` for a key `Settings` does not declare.

What it does not see: a settings object bound to another name, a key built at run time (`getattr(settings,
key)` in `/admin/config` is not flagged, by design), and whether a value is USED correctly. For those, read
the call site; `tests/unit/test_p024_no_os_environ_outside_config.py` holds the other half (no environment
read outside `app/config.py`).
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.config import Settings
from tests.p019_support import require_target

APP = Path(__file__).resolve().parents[2] / "app"


def _is_settings(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "settings"


def _findings(tree: ast.AST, where: str) -> tuple[list[str], int]:
    """Return (findings, number of `settings.KEY` reads seen)."""

    found: list[str] = []
    reads = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_settings(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            key = node.args[1].value
            field = Settings.model_fields.get(key)
            if field is None:
                found.append(f"{where}:{node.lineno} getattr(settings, {key!r}) - Settings has no such field")
            elif len(node.args) >= 3:
                try:
                    default = ast.literal_eval(node.args[2])
                except ValueError:
                    default = "<not a literal>"
                if default != field.default:
                    found.append(
                        f"{where}:{node.lineno} getattr(settings, {key!r}, {default!r}) - Settings default is "
                        f"{field.default!r}"
                    )
                else:
                    found.append(f"{where}:{node.lineno} getattr(settings, {key!r}, ...) - a second copy of the default")
            else:
                found.append(f"{where}:{node.lineno} getattr(settings, {key!r}) - read it as settings.{key}")
        elif isinstance(node, ast.Attribute) and _is_settings(node.value) and node.attr.isupper():
            reads += 1
            if node.attr not in Settings.model_fields and not hasattr(Settings, node.attr):
                found.append(f"{where}:{node.lineno} settings.{node.attr} - Settings has no such field")
    return found, reads


def _scan_app() -> tuple[list[str], int]:
    found: list[str] = []
    reads = 0
    for path in sorted(APP.rglob("*.py")):
        more, seen = _findings(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(APP.parent).as_posix())
        found += more
        reads += seen
    return found, reads


def test_the_guard_sees_each_shape_it_names() -> None:
    """Anti-vacuum: each shape, planted, is found; a legitimate read is not."""

    planted = {
        "phantom key": 'getattr(settings, "NO_SUCH_SETTING", 1)',
        "divergent default": 'getattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 50)',
        "same default, second copy": 'getattr(settings, "ROUTING_MAX_HOPS", 6)',
        "no default": 'getattr(settings, "ROUTING_MAX_HOPS")',
        "phantom attribute": "settings.NO_SUCH_SETTING",
    }
    for name, source in planted.items():
        found, _ = _findings(ast.parse(source), "planted")
        assert len(found) == 1, f"{name}: {found}"
    for clean in ("settings.ROUTING_MAX_HOPS", "getattr(settings, key)", "settings.DEFAULT_ADMIN_TOKEN"):
        assert _findings(ast.parse(clean), "clean")[0] == [], clean


def test_app_reads_settings_by_declared_keys_without_second_defaults() -> None:
    found, reads = _scan_app()
    # The scan reaches the code: direct reads exist in the application today.
    assert reads >= 20, reads
    require_target(not found, "settings read outside the declared fields:\n" + "\n".join(found))
