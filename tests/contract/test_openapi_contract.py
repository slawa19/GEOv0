"""Selected exact contracts and drift ratchets between canonical OpenAPI and FastAPI.

The YAML file is the reviewed public contract. FastAPI's generated OpenAPI is a
runtime projection with OpenAPI 3.1 nullable/title noise and incomplete custom
exception metadata. Normalization below ignores only that framework noise.
Existing semantic drift is locked by category digests: changing either side
requires inspecting the printed normalized diff and intentionally updating the
corresponding ratchet, rather than regenerating YAML blindly.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
ROOT_HEALTH_PATHS = {"/health", "/healthz", "/health/db"}
FRAMEWORK_SCHEMA_NOISE = {"description", "example", "examples", "title"}
AUTH_TRANSPORT_HEADERS = {"X-Admin-Token", "X-Simulator-Owner"}

# Exact semantic-debt ratchets. They intentionally fail even when drift shrinks,
# so every resolution is reviewed and the current/intended contract is recorded.
PARAMETER_SCHEMA_DRIFT_SHA256 = (
    "09c00b95eafbd980730a4709209a7038c7e791e66a819bb169353529c3cccbe3"
)
PARAMETER_SCHEMA_DRIFT_COUNT = 22
TRANSPORT_HEADER_DRIFT_SHA256 = (
    "a1b51fa7bd53346733f5debe5284e46449e2d36ac790ea2b9ceea1159ff492f1"
)
TRANSPORT_HEADER_DRIFT_COUNT = 59
REQUEST_SCHEMA_DRIFT_SHA256 = (
    "7eee1624c958db4900f2d24bf529bf7e6bab92aff055fd15403eb847dd7e5c25"
)
REQUEST_SCHEMA_DRIFT_COUNT = 13
# 2026-08-20 / p007_unblock_f0071: MetricPoint.v became nullable on both sides
# (canonical YAML and generated schema) so "not measured" is distinguishable from
# a measured zero. The GET /simulator/runs/{run_id}/metrics entry already carried
# unrelated drift, so only its content changed; the entry count stays 71 and the
# normalized diff for `v` is identical on both sides.
# 2026-08-20 / p007_t713_t714: the canonical MetricSeriesKey enum gained
# `active_participants` and `active_trustlines`, the two series that were already
# measured, persisted and declared by the pydantic model. Only the canonical side
# of the GET /simulator/runs/{run_id}/metrics entry moved, and it moved towards
# the generated side: both now list the same seven keys. That entry still drifts
# for unrelated pre-existing reasons, so the entry count stays 71.
# 2026-08-20 / p007_t715: `MetricPoint.v` became a decimal string on both sides.
# Two of the seven series (`total_debt`, `clearing_volume`) are amounts in the
# selected equivalent, so they are money and stay exact decimal (AGENTS.md §8);
# the field is one type for all seven series because they come from one column.
# The normalized delta inside the GET /simulator/runs/{run_id}/metrics entry is
# exactly, on the canonical and the generated side alike:
#     "v": {"nullable": true, "type": "number"}
#  -> "v": {"nullable": true, "type": "string"}
# Both sides moved together, so this entry still drifts only for the unrelated
# pre-existing reasons, and the entry count stays 71.
SUCCESS_SCHEMA_DRIFT_SHA256 = (
    "ccb4001a18a198528e7915b308c7cd98983ff42b8c223da10dba9aab520370ee"
)
SUCCESS_SCHEMA_DRIFT_COUNT = 71
# 2026-08-11 / T501: public DB health no longer declares exception details;
# the new admin diagnostic operation matches generated responses, so count stays 84.
# 2026-08-20 / p007_unblock_f0071: simulator metrics/bottlenecks declare 503 in the
# canonical contract (real mode refuses to substitute synthetic data). FastAPI does
# not know about it because the routes carry no `responses=`, so both operations —
# which already drifted for other reasons — gained a canonical-only 503; count stays 84.
ERROR_RESPONSE_DRIFT_SHA256 = (
    "b0af8a86310cdf916a93a5fcae795b95effa067204d89108c15a4c3e6ef214ec"
)
ERROR_RESPONSE_DRIFT_COUNT = 84
SECURITY_DRIFT_SHA256 = (
    "7fe6aad6a6fdae404bcea8e2a7696bb1e0788cbc95c2634ba90a3520ca89a5bd"
)
SECURITY_DRIFT_COUNT = 59


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_openapi_yaml() -> dict[str, Any]:
    path = _repo_root() / "api" / "openapi.yaml"
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    assert isinstance(data, dict)
    return data


def _load_fastapi_openapi() -> dict[str, Any]:
    from app.main import app

    schema = app.openapi()
    assert isinstance(schema, dict)
    return schema


def _resolve_ref(value: Any, document: dict[str, Any]) -> Any:
    if not isinstance(value, dict) or set(value) != {"$ref"}:
        return value
    reference = value["$ref"]
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return value
    current: Any = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    return _resolve_ref(current, document)


def _normalize_schema(
    value: Any,
    document: dict[str, Any],
    *,
    parameter: bool = False,
) -> Any:
    value = _resolve_ref(value, document)
    if not isinstance(value, dict):
        return value

    all_of = value.get("allOf")
    if len(value) == 1 and isinstance(all_of, list) and len(all_of) == 1:
        return _normalize_schema(all_of[0], document, parameter=parameter)

    any_of = value.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        non_null = [
            item for item in any_of if _resolve_ref(item, document) != {"type": "null"}
        ]
        if len(non_null) == 1:
            normalized = _normalize_schema(non_null[0], document, parameter=parameter)
            if parameter:
                return normalized
            if isinstance(normalized, dict):
                return {**normalized, "nullable": True}

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in FRAMEWORK_SCHEMA_NOISE:
            continue
        if key == "required" and isinstance(item, list):
            normalized[key] = sorted(item)
        elif isinstance(item, dict):
            normalized[key] = _normalize_schema(item, document, parameter=parameter)
        elif isinstance(item, list):
            normalized[key] = [
                _normalize_schema(element, document, parameter=parameter)
                if isinstance(element, dict)
                else element
                for element in item
            ]
        else:
            normalized[key] = item
    return normalized


def _operation_pairs(
    canonical: dict[str, Any], generated: dict[str, Any]
) -> list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]]:
    pairs = []
    for path, canonical_item in sorted(canonical["paths"].items()):
        generated_item = generated["paths"][f"/api/v1{path}"]
        for method in sorted(set(canonical_item) & HTTP_METHODS):
            key = f"{method.upper()} {path}"
            pairs.append(
                (
                    key,
                    canonical_item[method],
                    generated_item[method],
                    canonical_item,
                    generated_item,
                )
            )
    return pairs


def _raw_parameters(operation: dict[str, Any], path_item: dict[str, Any]) -> list[Any]:
    return [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]


def _normalized_parameters(
    operation: dict[str, Any],
    path_item: dict[str, Any],
    document: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    parameters: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_parameter in _raw_parameters(operation, path_item):
        parameter = _resolve_ref(raw_parameter, document)
        name = str(parameter.get("name") or "")
        location = str(parameter.get("in") or "")
        identity = (location, name)
        assert (
            identity not in parameters
        ), f"Duplicate OpenAPI parameter identity: in={location!r}, name={name!r}"

        required = bool(parameter.get("required"))
        schema = _normalize_schema(
            parameter.get("schema") or {}, document, parameter=True
        )
        parameters[identity] = {"required": required, "schema": schema}
    return dict(sorted(parameters.items()))


def _display_parameters(
    parameters: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        f"{location}:{name}": value
        for (location, name), value in sorted(parameters.items())
    }


def _request_body(
    operation: dict[str, Any], document: dict[str, Any]
) -> dict[str, Any] | None:
    raw_body = operation.get("requestBody")
    if raw_body is None:
        return None
    body = _resolve_ref(raw_body, document)
    content = body.get("content") or {}
    return {
        "required": bool(body.get("required")),
        "content": {
            media_type: _normalize_schema(media.get("schema") or {}, document)
            for media_type, media in sorted(content.items())
        },
    }


def _normalized_responses(
    operation: dict[str, Any], document: dict[str, Any]
) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for raw_status, raw_response in (operation.get("responses") or {}).items():
        status = str(raw_status)
        response = _resolve_ref(raw_response, document)
        content = response.get("content") or {}
        responses[status] = {
            "content": {
                media_type: _normalize_schema(media.get("schema") or {}, document)
                for media_type, media in sorted(content.items())
            }
        }
    return dict(sorted(responses.items()))


def _normalized_security(
    operation: dict[str, Any], path_item: dict[str, Any], document: dict[str, Any]
) -> dict[str, Any]:
    requirements = operation.get("security", document.get("security") or [])
    schemes = (document.get("components") or {}).get("securitySchemes") or {}
    normalized_requirements: list[list[dict[str, Any]]] = []
    for requirement in requirements:
        normalized_and_requirement: list[dict[str, Any]] = []
        for scheme_name, scopes in requirement.items():
            scheme = _resolve_ref(schemes.get(scheme_name) or {}, document)
            scheme_type = scheme.get("type")
            if (
                scheme_type == "http" and scheme.get("scheme") == "bearer"
            ) or scheme_type == "oauth2":
                transport = "bearer"
            elif scheme_type == "apiKey":
                transport = f"apiKey:{scheme.get('in')}:{scheme.get('name')}"
            else:
                transport = f"scheme:{scheme_name}"
            normalized_and_requirement.append(
                {
                    "transport": transport,
                    "scopes": sorted(str(scope) for scope in (scopes or [])),
                }
            )
        normalized_requirements.append(
            sorted(
                normalized_and_requirement,
                key=lambda item: json.dumps(item, sort_keys=True),
            )
        )

    header_transports: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_parameter in _raw_parameters(operation, path_item):
        parameter = _resolve_ref(raw_parameter, document)
        name = str(parameter.get("name") or "")
        location = str(parameter.get("in") or "")
        if location == "header" and name in AUTH_TRANSPORT_HEADERS:
            identity = (location, name)
            assert identity not in header_transports
            header_transports[identity] = {
                "name": name,
                "in": location,
                "required": bool(parameter.get("required")),
                "schema": _normalize_schema(
                    parameter.get("schema") or {}, document, parameter=True
                ),
            }
    return {
        "requirements": normalized_requirements,
        "header_transports": [
            header_transports[identity] for identity in sorted(header_transports)
        ],
    }


def _drift_digest(drift: dict[str, Any]) -> str:
    payload = json.dumps(
        drift, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_drift_ratchet(
    name: str,
    drift: dict[str, Any],
    expected_digest: str,
    expected_count: int,
) -> None:
    actual_digest = _drift_digest(drift)
    assert len(drift) == expected_count, (
        f"{name} entry count changed: expected={expected_count}; actual={len(drift)}. "
        f"digest={actual_digest}; normalized_diff={json.dumps(drift, indent=2, sort_keys=True)}"
    )
    assert actual_digest == expected_digest, (
        f"{name} changed. Review current/intended behavior before updating the ratchet. "
        f"digest={actual_digest}; normalized_diff={json.dumps(drift, indent=2, sort_keys=True)}"
    )


def test_openapi_yaml_is_well_formed() -> None:
    spec = _load_openapi_yaml()
    assert spec.get("openapi")
    assert isinstance(spec.get("info"), dict)
    assert isinstance(spec.get("paths"), dict)
    assert spec["paths"], "OpenAPI spec has no paths"


def test_admin_equivalent_mutation_inputs_preserve_canonical_bounds() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()

    canonical_schemas = canonical["components"]["schemas"]
    equivalent_code = canonical_schemas["EquivalentCode"]
    equivalent_precision = canonical_schemas["Equivalent"]["properties"]["precision"]
    canonical_create = canonical_schemas["AdminEquivalentCreateRequest"]["properties"]
    canonical_update = canonical_schemas["AdminEquivalentUpdateRequest"]["properties"]

    assert equivalent_code["pattern"] == r"^[A-Z0-9_]{1,16}$"
    assert equivalent_precision["minimum"] == 0
    assert equivalent_precision["maximum"] == 18
    assert canonical_create["code"] == {"$ref": "#/components/schemas/EquivalentCode"}
    for properties in (canonical_create, canonical_update):
        assert properties["precision"]["minimum"] == equivalent_precision["minimum"]
        assert properties["precision"]["maximum"] == equivalent_precision["maximum"]

    generated_schemas = generated["components"]["schemas"]
    create_properties = generated_schemas["AdminEquivalentCreateRequest"]["properties"]
    update_properties = generated_schemas["AdminEquivalentUpdateRequest"]["properties"]

    assert create_properties["code"]["pattern"] == equivalent_code["pattern"]
    for properties in (create_properties, update_properties):
        precision = _normalize_schema(properties["precision"], generated)
        assert precision["minimum"] == equivalent_precision["minimum"]
        assert precision["maximum"] == equivalent_precision["maximum"]


def test_equivalent_reads_preserve_legacy_visibility_without_weakening_mutations() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()
    canonical_schemas = canonical["components"]["schemas"]
    generated_schemas = generated["components"]["schemas"]

    list_item_ref = {"$ref": "#/components/schemas/StoredEquivalent"}
    assert canonical_schemas["EquivalentsList"]["properties"]["items"]["items"] == (
        list_item_ref
    )
    assert generated_schemas["EquivalentsList"]["properties"]["items"]["items"] == (
        list_item_ref
    )

    strict = canonical_schemas["Equivalent"]
    stored = canonical_schemas["StoredEquivalent"]
    assert set(stored["properties"]) == set(strict["properties"])
    assert set(stored["required"]) == set(strict["required"])
    assert stored["properties"]["code"] == {"type": "string"}
    assert stored["properties"]["precision"] == {"type": "integer"}

    generated_stored = generated_schemas["StoredEquivalent"]
    assert set(generated_stored["properties"]) == set(strict["properties"])
    assert set(generated_stored["required"]) == set(strict["required"])
    assert generated_stored["properties"]["code"]["type"] == "string"
    assert "pattern" not in generated_stored["properties"]["code"]
    assert generated_stored["properties"]["precision"]["type"] == "integer"
    assert "minimum" not in generated_stored["properties"]["precision"]
    assert "maximum" not in generated_stored["properties"]["precision"]

    canonical_patch = canonical["paths"]["/admin/equivalents/{code}"]["patch"]
    canonical_errors = _normalized_responses(canonical_patch, canonical)
    error_envelope = _normalize_schema(
        {"$ref": "#/components/schemas/ErrorEnvelope"}, canonical
    )
    assert canonical_errors["409"]["content"]["application/json"] == error_envelope
    generated_409 = generated["paths"]["/api/v1/admin/equivalents/{code}"][
        "patch"
    ]["responses"]["409"]
    assert generated_409["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }


def _assert_exact_object_schema(
    schema: dict[str, Any],
    *,
    properties: set[str],
    required: set[str],
) -> None:
    assert schema["type"] == "object"
    assert set(schema["properties"]) == properties
    assert set(schema["required"]) == required
    assert schema.get("additionalProperties") is False


def test_selected_admin_and_integrity_success_schemas_are_exact() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()
    schemas = canonical["components"]["schemas"]
    generated_schemas = generated["components"]["schemas"]

    operation_refs = {
        ("get", "/admin/feature-flags"): "AdminFeatureFlags",
        ("patch", "/admin/feature-flags"): "AdminFeatureFlags",
        ("post", "/admin/participants/{pid}/freeze"): "AdminParticipantStatusResponse",
        ("post", "/admin/participants/{pid}/unfreeze"): "AdminParticipantStatusResponse",
        ("post", "/admin/transactions/{tx_id}/abort"): "AdminAbortTxResponse",
        ("post", "/admin/equivalents"): "Equivalent",
        ("patch", "/admin/equivalents/{code}"): "Equivalent",
        ("delete", "/admin/equivalents/{code}"): "AdminDeleteResponse",
        ("get", "/admin/equivalents/{code}/usage"): "AdminEquivalentUsageResponse",
        ("get", "/integrity/status"): "IntegrityStatusResponse",
        ("post", "/integrity/verify"): "IntegrityVerifyResponse",
        (
            "post",
            "/integrity/repair/net-mutual-debts",
        ): "IntegrityNetMutualDebtsRepairResponse",
        (
            "post",
            "/integrity/repair/cap-debts-to-trust-limits",
        ): "IntegrityCapDebtsRepairResponse",
    }
    for (method, path), component in operation_refs.items():
        expected_ref = f"#/components/schemas/{component}"
        canonical_schema = canonical["paths"][path][method]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        generated_schema = generated["paths"][f"/api/v1{path}"][method]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]
        assert canonical_schema == {"$ref": expected_ref}
        assert generated_schema == {"$ref": expected_ref}

    exact_shapes = {
        "AdminFeatureFlags": (
            {"multipath_enabled", "full_multipath_enabled", "clearing_enabled"},
            {"multipath_enabled", "full_multipath_enabled", "clearing_enabled"},
        ),
        "AdminParticipantStatusResponse": (
            {"pid", "status"},
            {"pid", "status"},
        ),
        "AdminAbortTxResponse": ({"tx_id", "status"}, {"tx_id", "status"}),
        "AdminDeleteResponse": ({"deleted"}, {"deleted"}),
        "AdminEquivalentUsageResponse": (
            {"code", "trustlines", "debts", "integrity_checkpoints"},
            {"code", "trustlines", "debts", "integrity_checkpoints"},
        ),
        "IntegrityStatusResponse": (
            {"status", "last_check", "equivalents", "alerts"},
            {"status", "last_check", "equivalents", "alerts"},
        ),
        "IntegrityVerifyResponse": (
            {"status", "checked_at", "equivalents", "alerts"},
            {"status", "checked_at", "equivalents", "alerts"},
        ),
        "IntegrityNetMutualDebtsRepairResponse": (
            {"ok", "action", "netted_pairs", "updated", "deleted"},
            {"ok", "action", "netted_pairs", "updated", "deleted"},
        ),
        "IntegrityCapDebtsRepairResponse": (
            {"ok", "action", "scanned", "updated", "deleted"},
            {"ok", "action", "scanned", "updated", "deleted"},
        ),
    }
    for component, (properties, required) in exact_shapes.items():
        _assert_exact_object_schema(
            schemas[component],
            properties=properties,
            required=required,
        )
        assert set(generated_schemas[component]["properties"]) == properties

    generated_exact_components = {
        "AdminFeatureFlags",
        "AdminParticipantStatusResponse",
        "AdminAbortTxResponse",
        "AdminDeleteResponse",
        "AdminEquivalentUsageResponse",
        "IntegrityNetMutualDebtsRepairResponse",
        "IntegrityCapDebtsRepairResponse",
    }
    for component in generated_exact_components:
        properties, required = exact_shapes[component]
        _assert_exact_object_schema(
            generated_schemas[component],
            properties=properties,
            required=required,
        )

    # These response models have defaults, so Pydantic's generated construction
    # schema is intentionally looser than the serialized response projection.
    # Pin that distinction explicitly instead of letting requiredness/extras go
    # unchecked; the canonical response remains exact and both routes pass alerts.
    for component, required in {
        "IntegrityStatusResponse": {"status", "last_check", "equivalents"},
        "IntegrityVerifyResponse": {"status", "checked_at", "equivalents"},
    }.items():
        generated_schema = generated_schemas[component]
        assert set(generated_schema["required"]) == required
        assert "additionalProperties" not in generated_schema

    assert schemas["AdminParticipantStatusResponse"]["properties"]["status"][
        "enum"
    ] == ["active", "suspended"]
    assert schemas["AdminAbortTxResponse"]["properties"]["status"]["pattern"] == (
        "^aborted$"
    )
    assert schemas["Equivalent"]["properties"]["code"] == {
        "$ref": "#/components/schemas/EquivalentCode"
    }
    assert set(schemas["Equivalent"]["properties"]) == {
        "code",
        "symbol",
        "description",
        "precision",
        "metadata",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert set(schemas["Equivalent"]["required"]) == {
        "code",
        "precision",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert "additionalProperties" not in schemas["Equivalent"]
    assert set(generated_schemas["Equivalent"]["properties"]) == set(
        schemas["Equivalent"]["properties"]
    )
    assert set(generated_schemas["Equivalent"]["required"]) == set(
        schemas["Equivalent"]["required"]
    )
    assert schemas["Equivalent"]["properties"]["precision"]["minimum"] == 0
    assert schemas["Equivalent"]["properties"]["precision"]["maximum"] == 18
    assert generated_schemas["Equivalent"]["properties"]["code"]["pattern"] == (
        schemas["EquivalentCode"]["pattern"]
    )
    assert (
        generated_schemas["Equivalent"]["properties"]["precision"]["minimum"]
        == schemas["Equivalent"]["properties"]["precision"]["minimum"]
    )
    assert (
        generated_schemas["Equivalent"]["properties"]["precision"]["maximum"]
        == schemas["Equivalent"]["properties"]["precision"]["maximum"]
    )
    for timestamp_field in ("created_at", "updated_at"):
        assert schemas["Equivalent"]["properties"][timestamp_field]["format"] == (
            "date-time"
        )
        assert (
            generated_schemas["Equivalent"]["properties"][timestamp_field]["format"]
            == "date-time"
        )

    health_values = ["healthy", "warning", "critical"]
    assert schemas["IntegrityStatusResponse"]["properties"]["status"]["enum"] == (
        health_values
    )
    assert schemas["IntegrityVerifyResponse"]["properties"]["status"]["enum"] == (
        health_values
    )
    assert schemas["IntegrityNetMutualDebtsRepairResponse"]["properties"]["action"][
        "enum"
    ] == ["net-mutual-debts"]
    assert schemas["IntegrityCapDebtsRepairResponse"]["properties"]["action"][
        "enum"
    ] == ["cap-debts-to-trust-limits"]


def test_run_status_schema_preserves_stop_and_counter_fields() -> None:
    canonical = _load_openapi_yaml()
    schema = canonical["components"]["schemas"]["RunStatus"]
    generated = _load_fastapi_openapi()["components"]["schemas"]["RunStatus"]

    _assert_exact_object_schema(
        schema,
        properties={
            "api_version",
            "run_id",
            "scenario_id",
            "mode",
            "state",
            "started_at",
            "stopped_at",
            "stop_requested_at",
            "stop_source",
            "stop_reason",
            "stop_client",
            "sim_time_ms",
            "intensity_percent",
            "ops_sec",
            "queue_depth",
            "errors_total",
            "committed_total",
            "rejected_total",
            "attempts_total",
            "timeouts_total",
            "errors_last_1m",
            "consec_all_rejected_ticks",
            "last_error",
            "last_event_type",
            "current_phase",
        },
        required={"api_version", "run_id", "scenario_id", "mode", "state"},
    )
    assert set(generated["properties"]) == set(schema["properties"])
    assert set(generated["required"]) == {"run_id", "scenario_id", "mode", "state"}

    for name in {
        "sim_time_ms",
        "intensity_percent",
        "ops_sec",
        "queue_depth",
        "errors_total",
        "committed_total",
        "rejected_total",
        "attempts_total",
        "timeouts_total",
        "errors_last_1m",
        "consec_all_rejected_ticks",
    }:
        assert schema["properties"][name]["nullable"] is True
        assert schema["properties"][name]["minimum"] == 0

    for name in {"started_at", "stopped_at", "stop_requested_at"}:
        assert schema["properties"][name]["nullable"] is True
        assert schema["properties"][name]["format"] == "date-time"
    for name in {"stop_source", "stop_reason", "stop_client"}:
        assert schema["properties"][name] == {"type": "string", "nullable": True}


def test_simulator_event_union_tracks_producer_families_and_wire_aliases() -> None:
    from app.schemas.simulator import (
        SimulatorAuditDriftEvent,
        SimulatorEvent,
        SimulatorRunStatusEvent,
        SimulatorTxFailedEvent,
        SimulatorTxUpdatedEvent,
    )

    canonical = _load_openapi_yaml()
    schemas = canonical["components"]["schemas"]
    expected_refs = [
        f"#/components/schemas/{event_type.__name__}"
        for event_type in get_args(SimulatorEvent)
    ]

    assert [item["$ref"] for item in schemas["SimulatorEvent"]["oneOf"]] == (
        expected_refs
    )
    assert expected_refs == [
        "#/components/schemas/SimulatorTxUpdatedEvent",
        "#/components/schemas/SimulatorTxFailedEvent",
        "#/components/schemas/SimulatorClearingDoneEvent",
        "#/components/schemas/SimulatorAuditDriftEvent",
        "#/components/schemas/SimulatorTopologyChangedEvent",
        "#/components/schemas/SimulatorRunStatusEvent",
    ]

    for model in (SimulatorTxUpdatedEvent, SimulatorTxFailedEvent):
        model_properties = model.model_json_schema(by_alias=True)["properties"]
        assert "from" in model_properties
        assert "from_" not in model_properties
    for component in ("SimulatorTxUpdatedEvent", "SimulatorTxFailedEvent"):
        assert "from" in schemas[component]["properties"]
        assert "from_" not in schemas[component]["properties"]
    assert schemas["SimulatorEventEdgeRef"]["required"] == ["from", "to"]
    assert "from_" not in schemas["SimulatorEventEdgeRef"]["properties"]

    assert set(schemas["SimulatorAuditDriftEvent"]["properties"]) == {
        "event_id",
        "ts",
        "type",
        "equivalent",
        "tick_index",
        "severity",
        "total_drift",
        "drifts",
        "source",
    }
    assert set(SimulatorAuditDriftEvent.model_json_schema()["properties"]) == set(
        schemas["SimulatorAuditDriftEvent"]["properties"]
    )
    assert set(schemas["SimulatorTopologyChangedEvent"]["properties"]) == {
        "event_id",
        "ts",
        "type",
        "equivalent",
        "payload",
        "reason",
    }
    assert set(schemas["TopologyChangedPayload"]["properties"]) == {
        "added_nodes",
        "removed_nodes",
        "frozen_nodes",
        "added_edges",
        "removed_edges",
        "frozen_edges",
        "node_patch",
        "edge_patch",
    }
    assert schemas["SimulatorTxUpdatedEvent"]["properties"]["amount_flyout"] == {
        "type": "boolean",
        "nullable": True,
    }
    assert set(schemas["SimulatorRunStatusEvent"]["properties"]) == {
        "event_id",
        "ts",
        "type",
        "run_id",
        "scenario_id",
        "state",
        "sim_time_ms",
        "intensity_percent",
        "ops_sec",
        "queue_depth",
        "attempts_total",
        "committed_total",
        "rejected_total",
        "errors_total",
        "timeouts_total",
        "consec_all_rejected_ticks",
        "last_event_type",
        "current_phase",
        "last_error",
    }
    assert set(SimulatorRunStatusEvent.model_json_schema()["properties"]) == set(
        schemas["SimulatorRunStatusEvent"]["properties"]
    )


def test_simulator_events_documents_replay_cursor_and_gone_response() -> None:
    spec = _load_openapi_yaml()
    generated = _load_fastapi_openapi()
    for canonical_path, generated_path in (
        ("/simulator/events", "/api/v1/simulator/events"),
        (
            "/simulator/runs/{run_id}/events",
            "/api/v1/simulator/runs/{run_id}/events",
        ),
    ):
        path_item = spec["paths"][canonical_path]
        operation = path_item["get"]
        generated_item = generated["paths"][generated_path]
        generated_operation = generated_item["get"]

        parameters = _normalized_parameters(operation, path_item, spec)
        assert parameters[("header", "Last-Event-ID")] == {
            "required": False,
            "schema": {"type": "string"},
        }
        assert _normalized_parameters(
            generated_operation, generated_item, generated
        )[("header", "Last-Event-ID")] == parameters[("header", "Last-Event-ID")]

        responses = _normalized_responses(operation, spec)
        assert responses["410"]["content"]["application/json"] == _normalize_schema(
            {"$ref": "#/components/schemas/ErrorEnvelope"}, spec
        )
        assert generated_operation["responses"]["410"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ErrorEnvelope"}

        without_cursor = copy.deepcopy(operation)
        without_cursor["parameters"] = [
            parameter
            for parameter in without_cursor["parameters"]
            if parameter.get("name") != "Last-Event-ID"
        ]
        assert parameters != _normalized_parameters(without_cursor, path_item, spec)

        without_gone = copy.deepcopy(operation)
        del without_gone["responses"]["410"]
        assert responses != _normalized_responses(without_gone, spec)


def test_payment_create_declares_exact_response_statuses_and_error_envelopes() -> None:
    spec = _load_openapi_yaml()
    operation = spec["paths"]["/payments"]["post"]
    responses = _normalized_responses(operation, spec)

    assert set(responses) == {
        "200",
        "400",
        "401",
        "403",
        "404",
        "409",
        "422",
        "429",
        "500",
        "504",
    }
    error_envelope = _normalize_schema(
        {"$ref": "#/components/schemas/ErrorEnvelope"},
        spec,
    )
    for status in {"400", "403", "404", "409", "422", "429", "500", "504"}:
        assert responses[status]["content"]["application/json"] == error_envelope

    unauthorized = responses["401"]["content"]["application/json"]
    assert unauthorized == {
        "oneOf": [
            error_envelope,
            {
                "properties": {"detail": {"type": "string"}},
                "required": ["detail"],
                "type": "object",
            },
        ]
    }


def test_security_normalization_preserves_anonymous_or_and_scopes() -> None:
    document = {
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
                "TenantKey": {"type": "apiKey", "in": "header", "name": "X-Tenant"},
            }
        }
    }
    required = {"security": [{"BearerAuth": ["read"]}]}
    optional = {
        "security": [
            {},
            {"BearerAuth": ["read"], "TenantKey": ["tenant"]},
        ]
    }

    required_normalized = _normalized_security(required, {}, document)
    optional_normalized = _normalized_security(optional, {}, document)

    assert required_normalized != optional_normalized
    assert optional_normalized["requirements"][0] == []
    assert optional_normalized["requirements"][1] == [
        {"transport": "bearer", "scopes": ["read"]},
        {"transport": "apiKey:header:X-Tenant", "scopes": ["tenant"]},
    ]


@pytest.mark.parametrize(
    ("field", "mutated_value"),
    [("required", True), ("schema", {"type": "integer"})],
)
def test_generated_transport_header_mutations_are_not_filtered(
    field: str, mutated_value: Any
) -> None:
    generated = _load_fastapi_openapi()
    path_item = generated["paths"][
        "/api/v1/simulator/runs/{run_id}/actions/tx-once"
    ]
    operation = path_item["post"]
    baseline_parameters = _normalized_parameters(operation, path_item, generated)

    assert ("header", "X-Admin-Token") in baseline_parameters
    assert ("header", "X-Simulator-Owner") in baseline_parameters

    mutated_operation = copy.deepcopy(operation)
    owner_header = next(
        parameter
        for parameter in mutated_operation["parameters"]
        if parameter.get("name") == "X-Simulator-Owner"
    )
    owner_header[field] = mutated_value

    assert baseline_parameters != _normalized_parameters(
        mutated_operation, path_item, generated
    )
    assert _normalized_security(operation, path_item, generated) != _normalized_security(
        mutated_operation, path_item, generated
    )


def test_canonical_admin_headers_follow_production_policy() -> None:
    canonical = _load_openapi_yaml()
    headers = []
    for path_item in canonical["paths"].values():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            for raw_parameter in _raw_parameters(operation, path_item):
                parameter = _resolve_ref(raw_parameter, canonical)
                if parameter.get("name") == "X-Admin-Token":
                    headers.append(parameter)

    assert headers, "Canonical OpenAPI declares no admin-token transports"
    for header in headers:
        assert header.get("in") == "header"
        assert header.get("required") is True
        assert _normalize_schema(header.get("schema") or {}, canonical) == {
            "type": "string"
        }


def test_content_normalization_detects_extra_media_types() -> None:
    operation = {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
        "responses": {
            "200": {
                "content": {"application/json": {"schema": {"type": "object"}}}
            }
        },
    }
    mutated = copy.deepcopy(operation)
    mutated["requestBody"]["content"]["text/plain"] = {
        "schema": {"type": "string"}
    }
    mutated["responses"]["200"]["content"]["text/plain"] = {
        "schema": {"type": "string"}
    }

    assert _request_body(operation, {}) != _request_body(mutated, {})
    assert _normalized_responses(operation, {}) != _normalized_responses(mutated, {})


@pytest.mark.asyncio
async def test_served_openapi_validation_statuses_are_not_filterable_noise(
    client,
) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    generated = response.json()
    non_action = generated["paths"]["/api/v1/auth/challenge"]["post"]
    action = generated["paths"][
        "/api/v1/simulator/runs/{run_id}/actions/tx-once"
    ]["post"]

    assert non_action["responses"]["422"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorEnvelope"}
    assert action["responses"]["422"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ErrorEnvelope"}
    assert action["responses"]["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SimulatorActionError"
    }

    without_validation = copy.deepcopy(non_action)
    without_validation["responses"].pop("422")
    assert _normalized_responses(non_action, generated) != _normalized_responses(
        without_validation, generated
    )
    without_transport_validation = copy.deepcopy(action)
    without_transport_validation["responses"].pop("422")
    assert _normalized_responses(action, generated) != _normalized_responses(
        without_transport_validation, generated
    )


def test_root_health_and_versioned_api_are_explicitly_classified() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()
    generated_paths = set(generated["paths"])

    assert ROOT_HEALTH_PATHS <= generated_paths
    assert {f"/api/v1{path}" for path in ROOT_HEALTH_PATHS} <= generated_paths
    assert ROOT_HEALTH_PATHS <= set(canonical["paths"])
    assert {
        path for path in generated_paths if path.startswith("/health")
    } == ROOT_HEALTH_PATHS
    assert {path for path in generated_paths if not path.startswith("/api/v1/")} == {
        *ROOT_HEALTH_PATHS,
        "/metrics",
    }
    for path in sorted(ROOT_HEALTH_PATHS):
        root_operation = generated["paths"][path]["get"]
        versioned_operation = generated["paths"][f"/api/v1{path}"]["get"]
        root_item = generated["paths"][path]
        versioned_item = generated["paths"][f"/api/v1{path}"]
        assert _normalized_parameters(
            root_operation, root_item, generated
        ) == _normalized_parameters(versioned_operation, versioned_item, generated)
        assert _request_body(root_operation, generated) == _request_body(
            versioned_operation, generated
        )
        assert _normalized_responses(
            root_operation, generated
        ) == _normalized_responses(versioned_operation, generated)
        assert _normalized_security(
            root_operation, root_item, generated
        ) == _normalized_security(versioned_operation, versioned_item, generated)


def test_openapi_paths_methods_business_parameter_identities_and_required_bodies_match_and_schemas_are_ratcheted() -> (
    None
):
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()

    canonical_paths = set(canonical["paths"])
    generated_versioned_paths = {
        path.removeprefix("/api/v1")
        for path in generated["paths"]
        if path.startswith("/api/v1/")
    }
    assert generated_versioned_paths == canonical_paths

    parameter_schema_drift: dict[str, Any] = {}
    transport_header_drift: dict[str, Any] = {}
    request_schema_drift: dict[str, Any] = {}
    for (
        key,
        canonical_operation,
        generated_operation,
        canonical_item,
        generated_item,
    ) in _operation_pairs(canonical, generated):
        assert {name for name in canonical_item if name in HTTP_METHODS} == {
            name for name in generated_item if name in HTTP_METHODS
        }, f"Method drift for {key}"

        canonical_parameters = _normalized_parameters(
            canonical_operation, canonical_item, canonical
        )
        generated_parameters = _normalized_parameters(
            generated_operation,
            generated_item,
            generated,
        )
        canonical_transports = {
            identity: value
            for identity, value in canonical_parameters.items()
            if identity[0] == "header" and identity[1] in AUTH_TRANSPORT_HEADERS
        }
        generated_transports = {
            identity: value
            for identity, value in generated_parameters.items()
            if identity[0] == "header" and identity[1] in AUTH_TRANSPORT_HEADERS
        }
        if canonical_transports != generated_transports:
            transport_header_drift[key] = {
                "canonical": _display_parameters(canonical_transports),
                "generated": _display_parameters(generated_transports),
            }

        canonical_business_parameters = {
            identity: value
            for identity, value in canonical_parameters.items()
            if identity not in canonical_transports
        }
        generated_business_parameters = {
            identity: value
            for identity, value in generated_parameters.items()
            if identity not in generated_transports
        }
        assert set(generated_business_parameters) == set(
            canonical_business_parameters
        ), (
            f"Parameter identity drift for {key}: "
            f"canonical={sorted(canonical_business_parameters)}; "
            f"generated={sorted(generated_business_parameters)}"
        )
        for identity, canonical_parameter in canonical_business_parameters.items():
            generated_parameter = generated_business_parameters[identity]
            if generated_parameter != canonical_parameter:
                parameter_schema_drift[f"{key} {identity}"] = {
                    "canonical": canonical_parameter,
                    "generated": generated_parameter,
                }

        canonical_body = _request_body(canonical_operation, canonical)
        generated_body = _request_body(generated_operation, generated)
        assert (canonical_body is None) == (
            generated_body is None
        ), f"Request body presence drift for {key}"
        if canonical_body is not None and generated_body is not None:
            assert canonical_body["required"] == generated_body["required"], (
                f"Required request body drift for {key}: "
                f"canonical={canonical_body['required']}; generated={generated_body['required']}"
            )
            if canonical_body["content"] != generated_body["content"]:
                request_schema_drift[key] = {
                    "canonical": canonical_body["content"],
                    "generated": generated_body["content"],
                }

    _assert_drift_ratchet(
        "Parameter schema drift",
        parameter_schema_drift,
        PARAMETER_SCHEMA_DRIFT_SHA256,
        PARAMETER_SCHEMA_DRIFT_COUNT,
    )
    _assert_drift_ratchet(
        "Transport header drift",
        transport_header_drift,
        TRANSPORT_HEADER_DRIFT_SHA256,
        TRANSPORT_HEADER_DRIFT_COUNT,
    )
    _assert_drift_ratchet(
        "Request schema drift",
        request_schema_drift,
        REQUEST_SCHEMA_DRIFT_SHA256,
        REQUEST_SCHEMA_DRIFT_COUNT,
    )


def test_openapi_success_error_responses_and_security_are_ratcheted() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()
    success_schema_drift: dict[str, Any] = {}
    error_response_drift: dict[str, Any] = {}
    security_drift: dict[str, Any] = {}

    for (
        key,
        canonical_operation,
        generated_operation,
        canonical_item,
        generated_item,
    ) in _operation_pairs(canonical, generated):
        canonical_responses = _normalized_responses(canonical_operation, canonical)
        generated_responses = _normalized_responses(generated_operation, generated)
        canonical_success = {
            status: schema
            for status, schema in canonical_responses.items()
            if status.startswith("2")
        }
        generated_success = {
            status: schema
            for status, schema in generated_responses.items()
            if status.startswith("2")
        }
        assert (
            set(canonical_success) == set(generated_success)
        ), f"Success status drift for {key}: canonical={canonical_success}; generated={generated_success}"
        if canonical_success != generated_success:
            success_schema_drift[key] = {
                "canonical": canonical_success,
                "generated": generated_success,
            }

        canonical_errors = {
            status: schema
            for status, schema in canonical_responses.items()
            if not status.startswith("2")
        }
        generated_errors = {
            status: schema
            for status, schema in generated_responses.items()
            if not status.startswith("2")
        }
        if canonical_errors != generated_errors:
            error_response_drift[key] = {
                "canonical": canonical_errors,
                "generated": generated_errors,
            }

        canonical_security = _normalized_security(
            canonical_operation, canonical_item, canonical
        )
        generated_security = _normalized_security(
            generated_operation, generated_item, generated
        )
        if canonical_security != generated_security:
            security_drift[key] = {
                "canonical": canonical_security,
                "generated": generated_security,
            }

    _assert_drift_ratchet(
        "Success response schema drift",
        success_schema_drift,
        SUCCESS_SCHEMA_DRIFT_SHA256,
        SUCCESS_SCHEMA_DRIFT_COUNT,
    )
    _assert_drift_ratchet(
        "Error response drift",
        error_response_drift,
        ERROR_RESPONSE_DRIFT_SHA256,
        ERROR_RESPONSE_DRIFT_COUNT,
    )
    _assert_drift_ratchet(
        "Security drift",
        security_drift,
        SECURITY_DRIFT_SHA256,
        SECURITY_DRIFT_COUNT,
    )


def _property_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(name) for name in properties)
        for child in value.values():
            names.update(_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_property_names(child))
    return names


def test_openapi_wire_schemas_use_from_alias_not_python_field_name() -> None:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()

    assert "from_" not in _property_names(canonical)
    assert "from_" not in _property_names(generated)
    assert (
        "from" in generated["components"]["schemas"]["TxOnceRequestBody"]["properties"]
    )


@pytest.mark.asyncio
async def test_simulator_action_validation_uses_flat_error_envelope(client) -> None:
    from app.config import settings

    response = await client.post(
        "/api/v1/simulator/runs/missing/actions/tx-once",
        json={},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "INVALID_REQUEST"
    assert payload["message"] == "Invalid request"
    assert isinstance(payload.get("details", {}).get("errors"), list)
    assert "error" not in payload


@pytest.mark.asyncio
async def test_simulator_invalid_owner_transport_uses_nested_error_envelope(
    client,
) -> None:
    from app.config import settings

    response = await client.post(
        "/api/v1/simulator/runs/missing/actions/tx-once",
        json={"equivalent": "USD"},
        headers={
            "X-Admin-Token": settings.ADMIN_TOKEN,
            "X-Simulator-Owner": "invalid owner!",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "E009"
    assert payload["error"]["message"] == "Invalid X-Simulator-Owner header"
    assert "code" not in payload


@pytest.mark.asyncio
async def test_simulator_action_disabled_uses_documented_403_envelope(
    client, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.delenv("SIMULATOR_ACTIONS_ENABLE", raising=False)
    response = await client.post(
        "/api/v1/simulator/runs/missing/actions/tx-once",
        json={"equivalent": "USD"},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "E006"


@pytest.mark.asyncio
async def test_simulator_terminal_action_uses_documented_409_envelope(
    client, monkeypatch
) -> None:
    from app.api.v1.simulator import runtime
    from app.config import settings
    from app.core.simulator.models import RunRecord

    terminal_run = RunRecord(
        run_id="terminal-run",
        scenario_id="scenario",
        mode="real",
        state="stopped",
    )
    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(runtime, "get_run", lambda _run_id: terminal_run)

    response = await client.post(
        "/api/v1/simulator/runs/terminal-run/actions/clearing-once",
        json={"equivalent": "USD"},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "E008"


@pytest.mark.asyncio
async def test_non_action_validation_keeps_geo_error_envelope(client) -> None:
    response = await client.post("/api/v1/auth/challenge", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "E009"
    assert isinstance(payload["error"].get("details", {}).get("errors"), list)
