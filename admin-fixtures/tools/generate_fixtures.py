#!/usr/bin/env python
"""Unified deterministic fixtures generator.

Goal: a single entry point to generate *canonical* Admin UI fixture datasets.

Seeds:
- greenfield-village-100
- riverside-town-50
 - greenfield-village-100-v2
 - riverside-town-50-v2

By default this writes into admin-fixtures/v1 (the canonical pack that Admin UI syncs).

Usage (from repo root):
    ./.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100
    ./.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50

Optional: build a named pack under admin-fixtures/packs/<seed_id>/v1 and activate it:
    ./.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100 --pack
    ./.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50 --pack --activate

Rationale:
- `admin-fixtures/v1` stays the single active canonical pack.
- `admin-fixtures/packs/*` can store multiple packs without ambiguity.

No external dependencies.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import sys


TOOLS_DIR = Path(__file__).resolve().parent
# tools/ is directly under admin-fixtures/
BASE_DIR = TOOLS_DIR.parent  # .../admin-fixtures
CANONICAL_V1_DIR = BASE_DIR / "v1"
PACKS_DIR = BASE_DIR / "packs"


def _load_seed_module(seed_id: str):
    if seed_id == "greenfield-village-100":
        path = TOOLS_DIR / "generate_seed_greenfield_village_100.py"
    elif seed_id == "riverside-town-50":
        path = TOOLS_DIR / "generate_seed_riverside_town_50.py"
    elif seed_id == "greenfield-village-100-v2":
        path = TOOLS_DIR / "generate_seed_greenfield_village_100_v2.py"
    elif seed_id == "riverside-town-50-v2":
        path = TOOLS_DIR / "generate_seed_riverside_town_50_v2.py"
    else:
        raise ValueError(f"Unknown seed_id: {seed_id}")

    # Load module by path (folder name has a hyphen, so normal imports are inconvenient).
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"geo_seed_{seed_id}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load seed module: {path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Unified deterministic fixtures generator")
    p.add_argument(
        "--seed",
        required=True,
        choices=[
            "greenfield-village-100",
            "riverside-town-50",
            "greenfield-village-100-v2",
            "riverside-town-50-v2",
        ],
        help="Which canonical community seed to generate",
    )
    p.add_argument(
        "--pack",
        action="store_true",
        help="Generate into admin-fixtures/packs/<seed_id>/v1 instead of admin-fixtures/v1",
    )
    p.add_argument(
        "--activate",
        action="store_true",
        help="After generating a pack, copy it into admin-fixtures/v1 (canonical active pack)",
    )

    args = p.parse_args(argv)

    if args.pack:
        out_v1 = PACKS_DIR / args.seed / "v1"
    else:
        out_v1 = CANONICAL_V1_DIR

    out_v1.mkdir(parents=True, exist_ok=True)

    mod = _load_seed_module(args.seed)

    if not hasattr(mod, "main"):
        raise RuntimeError(f"Seed module has no main(): {args.seed}")

    # Seed scripts support out-v1 override.
    mod.main(["--out-v1", str(out_v1)])

    if args.pack and args.activate:
        _copy_tree(out_v1, CANONICAL_V1_DIR)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
