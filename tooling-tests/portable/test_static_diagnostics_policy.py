from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


def test_ruff_blocks_ci_while_black_remains_diagnostic() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = {
        step["name"]: step
        for step in workflow["jobs"]["static-diagnostics"]["steps"]
        if isinstance(step, dict) and "name" in step
    }

    ruff = steps["Ruff diagnostics"]
    black = steps["Black diagnostics"]

    assert ruff.get("continue-on-error") is not True
    assert ruff["run"] == "python -m ruff check app migrations --no-cache"
    assert black.get("continue-on-error") is True
    assert black["run"] == "python -m black --check app migrations"
