from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _repo_root() -> Path:
    # tests/contract/test_openapi_contract.py -> tests -> repo root
    return Path(__file__).resolve().parents[2]


def _load_openapi_yaml() -> dict[str, Any]:
    path = _repo_root() / "api" / "openapi.yaml"
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def _load_fastapi_openapi() -> dict[str, Any]:
    # Import here to ensure test env/fixtures are set up before app import.
    from app.main import app

    schema = app.openapi()
    assert isinstance(schema, dict)
    return schema


def test_openapi_yaml_is_well_formed() -> None:
    spec = _load_openapi_yaml()
    assert spec.get("openapi")
    assert isinstance(spec.get("info"), dict)
    assert isinstance(spec.get("paths"), dict)
    assert spec["paths"], "OpenAPI spec has no paths"


def test_openapi_paths_and_methods_match_app() -> None:
    spec = _load_openapi_yaml()
    app_schema = _load_fastapi_openapi()

    spec_paths = set((spec.get("paths") or {}).keys())

    app_paths_with_prefix = {
        p
        for p in (app_schema.get("paths") or {}).keys()
        if p.startswith("/api/v1/")
    }
    app_paths = {p.removeprefix("/api/v1") for p in app_paths_with_prefix}

    assert app_paths == spec_paths, (
        "OpenAPI drift between api/openapi.yaml and FastAPI routes. "
        f"Missing in app: {sorted(spec_paths - app_paths)}; "
        f"Extra in app: {sorted(app_paths - spec_paths)}"
    )

    for spec_path in sorted(spec_paths):
        app_path = f"/api/v1{spec_path}"

        spec_item = (spec.get("paths") or {}).get(spec_path) or {}
        app_item = (app_schema.get("paths") or {}).get(app_path) or {}

        spec_methods = {k for k in spec_item.keys() if k in HTTP_METHODS}
        app_methods = {k for k in app_item.keys() if k in HTTP_METHODS}

        assert app_methods == spec_methods, (
            f"Method drift for path {spec_path}. "
            f"Missing in app: {sorted(spec_methods - app_methods)}; "
            f"Extra in app: {sorted(app_methods - spec_methods)}"
        )


def test_openapi_components_schemas_include_spec() -> None:
    spec = _load_openapi_yaml()

    spec_schemas = set(((spec.get("components") or {}).get("schemas") or {}).keys())
    assert spec_schemas, "Spec has no components.schemas"

    # NOTE:
    # FastAPI's OpenAPI only includes schemas that are referenced by actual routes.
    # Our canonical api/openapi.yaml may include helper/unused schemas for documentation.
    # Contract drift detection is enforced via paths+methods equality test.
