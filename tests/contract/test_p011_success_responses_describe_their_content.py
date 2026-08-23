"""RT-011-1 / T1106: no 2xx response may newly become an undescribed object.

Program 011, finding `F-011-1`.

`api/openapi.yaml` is authority number one, and for 36 operations it currently answers "some
object" - `type: object` with no `properties`. That is not a description; a consumer learns
nothing from it, and the contract gate has nothing to compare. The class has been caught four
separate times by four different passes, each time counted differently, which is the actual
problem this guard exists to end: `T1102` shrinks the list below, and nothing may grow it.

**The predicate declares its own definition, because the count is meaningless without one.** An
object is undescribed when it has no `properties`, no `allOf`/`oneOf`/`anyOf`, and free-form
values. Specifically:

* an empty `properties: {}` counts as undescribed - otherwise the guard goes green on a forgery;
* a *typed* map (`additionalProperties` holding a schema) is NOT undescribed by itself: it states
  the type of its values. Counting typed maps as opaque was the first mistake made here, and it
  gave 41 operations instead of 34;
* the predicate DOES descend into the value schema of a typed map. That single choice is the
  difference between 34 and 36 operations, and it adds exactly `GET /integrity/status` and
  `POST /integrity/verify`, whose map values are themselves free-form objects. Descending is the
  stricter reading and the honest one: a map of undescribed objects describes nothing either.

Recursion covers property values, array items, composition branches and `$ref` targets, with
cycle protection, because the class hides at every one of those depths - `/admin/participants`
declares `properties` and looks described while each element of its array is opaque.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# Measured 2026-08-23 with the predicate below. This list may only shrink: every entry is an
# operation whose 2xx body the canon does not describe, and `T1102` exists to empty it.
#
# 2026-08-23 / T1102: ban and unban struck off (36 -> 34) - the canon now states the two keys
# `_set_participant_status` really emits, rather than `additionalProperties: true`.
# 2026-08-23: `GET /admin/health/db` added (34 -> 35). It was never described - it declares
# `schema: {}` - but the predicate could not see an empty schema until external review
# pointed at the hole. The list grew because the guard got sharper, not the canon worse.
# 2026-08-23 / T1102: the five operations the application already described, and the canon
# did not, are struck off (35 -> 30).
UNDESCRIBED_SUCCESS_RESPONSES = {
    ("GET", "/admin/audit-log"),
    ("GET", "/admin/equivalents"),
    ("POST", "/admin/equivalents"),
    ("PATCH", "/admin/equivalents/{code}"),
    ("GET", "/admin/graph/ego"),
    ("GET", "/admin/graph/snapshot"),
    # Found only after the predicate learned that `{}` describes nothing (2026-08-23).
    ("GET", "/admin/health/db"),
    ("GET", "/admin/liquidity/summary"),
    ("GET", "/admin/participants/{pid}/metrics"),
    ("GET", "/admin/trustlines"),
    ("GET", "/admin/trustlines/bottlenecks"),
    ("GET", "/equivalents"),
    ("GET", "/integrity/audit-log"),
    ("GET", "/integrity/checksum/{equivalent}"),
    ("GET", "/integrity/status"),
    ("POST", "/integrity/verify"),
    ("GET", "/participants"),
    ("POST", "/participants"),
    ("GET", "/participants/me"),
    ("PATCH", "/participants/me"),
    ("GET", "/participants/search"),
    ("GET", "/participants/{pid}"),
    ("GET", "/payments"),
    ("POST", "/payments"),
    ("GET", "/payments/{tx_id}"),
    ("GET", "/simulator/events/poll"),
    ("GET", "/trustlines"),
    ("POST", "/trustlines"),
    ("GET", "/trustlines/{id}"),
    ("PATCH", "/trustlines/{id}"),
}


def _canon() -> dict[str, Any]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _deref(node: Any, document: dict, seen: frozenset) -> tuple[Any, frozenset]:
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return None, seen
        seen = seen | {ref}
        cursor: Any = document
        for part in ref.lstrip("#/").split("/"):
            cursor = cursor.get(part, {}) if isinstance(cursor, dict) else {}
        node = cursor
    return node, seen


def is_undescribed(schema: Any, document: dict, seen: frozenset = frozenset()) -> bool:
    schema, seen = _deref(schema, document, seen)
    if not isinstance(schema, dict):
        return False

    # An empty schema is the emptiest description there is, and the first version of this
    # predicate let it through: the object branch below only opens on `type`/`properties`/
    # `additionalProperties`, none of which `{}` has. External review found the hole
    # (2026-08-23) and `GET /admin/health/db` was already sitting in it, declaring `schema: {}`
    # and passing this guard. A guard blind to the emptiest case is the anti-vacuum defect
    # AGENTS.md section 9 warns about.
    if not schema:
        return True

    for keyword in ("allOf", "oneOf", "anyOf"):
        for branch in schema.get(keyword) or []:
            if is_undescribed(branch, document, seen):
                return True

    declared_type = schema.get("type")
    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    composed = any(schema.get(key) for key in ("allOf", "oneOf", "anyOf"))
    typed_map = isinstance(additional, dict) and bool(additional)

    if declared_type == "object" or properties is not None or additional is not None:
        if not properties and not composed and not typed_map:
            return True
        for value in (properties or {}).values():
            if is_undescribed(value, document, seen):
                return True
        if typed_map and is_undescribed(additional, document, seen):
            return True

    if (declared_type == "array" or "items" in schema) and schema.get("items") is not None:
        if is_undescribed(schema["items"], document, seen):
            return True

    return False


def _measure() -> set[tuple[str, str]]:
    document = _canon()
    found = set()
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            for status, response in (operation.get("responses") or {}).items():
                if not str(status).startswith("2"):
                    continue
                content = response.get("content") or {}
                if not content:
                    # 204/205/304 carry no body by definition; any other 2xx that declares no
                    # content is undescribed, not exempt. Skipping these silently was the second
                    # half of the same hole.
                    if str(status) not in {"204", "205", "304"}:
                        found.add((method.upper(), path))
                    continue
                for media in content.values():
                    # A media type with no `schema` key describes nothing either. Checked here
                    # rather than inside the predicate, which answers about a schema and is
                    # legitimately False for a non-dict.
                    if "schema" not in media or is_undescribed(media["schema"], document):
                        found.add((method.upper(), path))
    return found


def test_no_new_undescribed_success_response() -> None:
    """The list may shrink. It may not grow."""

    measured = _measure()
    appeared = sorted(measured - UNDESCRIBED_SUCCESS_RESPONSES)
    assert not appeared, (
        "these 2xx responses newly describe nothing - the canon must state what they return: "
        f"{appeared}"
    )


def test_the_list_has_no_stale_entries() -> None:
    """Described operations must leave the list, or it stops meaning anything."""

    measured = _measure()
    stale = sorted(UNDESCRIBED_SUCCESS_RESPONSES - measured)
    assert not stale, (
        f"these operations are described now; remove them from the list: {stale}"
    )


def test_the_predicate_detects_the_forms_it_claims_to() -> None:
    """Guard the guard: every shape the finding names must actually trip the predicate."""

    document = {"components": {"schemas": {"Opaque": {"type": "object"}}}}

    assert is_undescribed({"type": "object"}, document)
    assert is_undescribed({"type": "object", "properties": {}}, document), (
        "an empty properties map must count as undescribed, or a forgery goes green"
    )
    assert is_undescribed({"type": "object", "additionalProperties": True}, document)

    # Described at the top, opaque in the array element - the /admin/participants shape.
    assert is_undescribed(
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                }
            },
        },
        document,
    )

    # Reached through a $ref.
    assert is_undescribed(
        {"type": "object", "properties": {"x": {"$ref": "#/components/schemas/Opaque"}}},
        document,
    )

    # A typed map is described; a map of opaque objects is not.
    assert not is_undescribed(
        {"type": "object", "additionalProperties": {"type": "string"}}, document
    )
    assert is_undescribed(
        {"type": "object", "additionalProperties": {"type": "object"}}, document
    )

    # A fully described object must not trip it.
    assert not is_undescribed(
        {"type": "object", "properties": {"id": {"type": "string"}}}, document
    )
