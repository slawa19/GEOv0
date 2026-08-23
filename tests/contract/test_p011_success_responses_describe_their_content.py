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
# 2026-08-23 / T1102: ten more struck off (30 -> 20) whose only opacity was a node in
# ACCEPTED_FREE_FORM above - declared-open content, not a gap.
# 2026-08-23 / T1102: five more (20 -> 15) - InvariantResult.details and PaymentError.details
# now state their variants. The payments trio also needed the predicate to stop treating
# `additionalProperties: false` with no properties as silence: that describes the empty
# object, which is what a plain abort really returns.
# 2026-08-23 / T1102: five more (15 -> 10). TrustLine.policy declares the five keys the
# validator allows, and the two STABLE integrity nodes - invariants_status and the
# integrity audit after_state - state their fixed key sets.
# 2026-08-23 / T1102: /admin/health/db struck off (10 -> 9). It was the operation the empty
# `schema: {}` had hidden; both sides now declare the body the handler builds literally.
UNDESCRIBED_SUCCESS_RESPONSES = {
    ("GET", "/admin/audit-log"),
    ("GET", "/admin/graph/ego"),
    ("GET", "/admin/graph/snapshot"),
    ("GET", "/admin/liquidity/summary"),
    ("GET", "/admin/participants/{pid}/metrics"),
    ("GET", "/admin/trustlines"),
    ("GET", "/admin/trustlines/bottlenecks"),
    ("GET", "/integrity/audit-log"),
    ("GET", "/simulator/events/poll"),
}


def _canon() -> dict[str, Any]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# Nodes whose content is genuinely open, with the reason each one is open. These are NOT gaps:
# the code emits arbitrary content there, so inventing `properties` would make authority number
# one describe something the service never promises - worse than describing nothing. Established
# 2026-08-23 by enumerating every writer.
#
# This is an allowlist of (schema, property) pairs rather than a rule like "a description makes it
# fine", because a rule of that shape is satisfied by adding prose. Adding a row here is a claim
# about the code, and `test_accepted_free_form_nodes_are_still_free_form` below checks the claim
# still holds.
ACCEPTED_FREE_FORM: dict[tuple[str, str], str] = {
    ("ParticipantProfile", "contacts"): (
        "client-supplied JSON echoed unmodified; the only writers are the create/update handlers "
        "(app/core/participants/service.py:65 and :175-178) and no validator constrains it"
    ),
    ("Equivalent", "metadata"): (
        "admin-supplied JSON stored verbatim (app/api/v1/admin.py:1150, :1236). A validator exists "
        "- validate_equivalent_metadata - but is called only from scripts/seed_db.py and tests, "
        "never from app/, so the API path accepts any object"
    ),
    ("StoredEquivalent", "metadata"): "same column and writers as Equivalent.metadata",
    ("PaymentError", "details"): (
        "documented variants plus a deliberately open tail: both abort paths forward the "
        ".details of whatever 4xx GeoException prepare or commit raised "
        "(app/core/payments/service.py:896-903), so a new details= anywhere under "
        "app/core/payments/ changes this node with no schema change. The known shapes are "
        "described in the canon; the tail cannot be closed without making a real response "
        "non-conforming"
    ),
}


def _deref(node: Any, document: dict, seen: frozenset) -> tuple[Any, frozenset, str | None]:
    """Resolve $refs, and report the name of the last component resolved through.

    The name is what lets `ACCEPTED_FREE_FORM` be keyed by (schema, property): without it the
    walker only ever sees anonymous sub-objects and cannot tell `Equivalent.metadata` from any
    other bare object.
    """

    component: str | None = None
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return None, seen, component
        seen = seen | {ref}
        component = ref.rsplit("/", 1)[-1]
        cursor: Any = document
        for part in ref.lstrip("#/").split("/"):
            cursor = cursor.get(part, {}) if isinstance(cursor, dict) else {}
        node = cursor
    return node, seen, component


def is_undescribed(
    schema: Any,
    document: dict,
    seen: frozenset = frozenset(),
    component: str | None = None,
) -> bool:
    schema, seen, resolved = _deref(schema, document, seen)
    component = resolved or component
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
            if is_undescribed(branch, document, seen, component):
                return True

    declared_type = schema.get("type")
    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    composed = any(schema.get(key) for key in ("allOf", "oneOf", "anyOf"))
    typed_map = isinstance(additional, dict) and bool(additional)

    if declared_type == "object" or properties is not None or additional is not None:
        # `additionalProperties: false` with no properties is not silence - it describes an
        # object that can hold nothing, which is a complete and useful statement. The abort
        # payload of PaymentError.details is exactly that case.
        describes_the_empty_object = additional is False
        if not properties and not composed and not typed_map and not describes_the_empty_object:
            return True
        for name, value in (properties or {}).items():
            if (component, name) in ACCEPTED_FREE_FORM:
                continue
            if is_undescribed(value, document, seen, component):
                return True
        if typed_map and is_undescribed(additional, document, seen, component):
            return True

    if (declared_type == "array" or "items" in schema) and schema.get("items") is not None:
        if is_undescribed(schema["items"], document, seen, component):
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


def test_accepted_free_form_nodes_are_still_free_form() -> None:
    """Counter-check: every accepted exemption must still name a real, still-open node.

    Without this the allowlist rots in both directions - a node that got described would stay
    exempt and nobody would notice, and a renamed schema would silently exempt nothing while
    still looking like protection.
    """

    document = _canon()
    schemas = document.get("components", {}).get("schemas", {})

    for (schema_name, property_name), reason in sorted(ACCEPTED_FREE_FORM.items()):
        schema = schemas.get(schema_name)
        assert schema is not None, (
            f"{schema_name} no longer exists; the exemption for {property_name} exempts nothing"
        )
        node = (schema.get("properties") or {}).get(property_name)
        assert node is not None, (
            f"{schema_name}.{property_name} no longer exists; drop the exemption"
        )
        resolved, _seen, _component = _deref(node, document, frozenset())
        assert isinstance(resolved, dict), f"{schema_name}.{property_name} did not resolve"
        assert not resolved.get("properties"), (
            f"{schema_name}.{property_name} now declares properties, so it is no longer "
            f"free-form. Remove it from ACCEPTED_FREE_FORM - the reason recorded there "
            f"({reason.split(';')[0]}) no longer holds."
        )
        # A declared-open node must actually be declared open, not merely undescribed.
        assert resolved.get("additionalProperties") is not False, (
            f"{schema_name}.{property_name} forbids additional properties yet declares none, "
            f"which describes an object that can only ever be empty"
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
