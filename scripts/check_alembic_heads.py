"""Fail when the repository migration graph does not have exactly one head."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    config = Config(str(repo_root / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()

    if len(heads) != 1:
        rendered = ", ".join(heads) if heads else "<none>"
        raise SystemExit(
            f"Expected exactly one Alembic head, found {len(heads)}: {rendered}"
        )

    print(f"Alembic head check passed: {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
