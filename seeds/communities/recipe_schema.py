"""Schema of a seed recipe (`seeds/communities/<community>/recipe.json`).

STUB. The rules live in the next commit; this one carries the recipes, the
tests and a validator that only parses. It exists so the counter-checks in
`tests/unit/test_p017_t1713_community_recipes.py` are written before the rules
they describe and are seen failing on a validator that has none (AGENTS.md §9,
anti-vacuum; §16.3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "recipe/1"

COMMUNITIES_DIR = Path(__file__).resolve().parent


class RecipeError(ValueError):
    """A recipe does not agree with the community description it names."""


def validate_recipe(recipe: Any, community: Any, *, source: str = "<recipe>") -> dict[str, Any]:
    if not isinstance(recipe, dict):
        raise RecipeError(f"{source}: recipe must be an object, got {type(recipe).__name__}")
    if not isinstance(community, dict):
        raise RecipeError(f"{source}: community description must be an object")
    return recipe


def max_hops_for(command: dict[str, Any]) -> int | None:
    """The `PaymentConstraints.max_hops` a payment command must be sent with."""

    return 1 if command.get("routing") == "direct" else None


def load_recipe(community_id: str, *, root: Path | None = None) -> dict[str, Any]:
    base = root if root is not None else COMMUNITIES_DIR
    from community_schema import load_community  # noqa: PLC0415

    community = load_community(community_id, root=base)
    path = base / community_id / "recipe.json"
    recipe = json.loads(path.read_text(encoding="utf-8"))
    validate_recipe(recipe, community, source=str(path))
    return recipe
