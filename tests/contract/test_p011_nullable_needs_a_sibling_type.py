"""RT-011-10 / T1107: `nullable: true` with no sibling `type` is not a declaration.

Program 011, finding `F-011-10`.

In OpenAPI **3.0.3** - which `api/openapi.yaml` declares, and which is not JSON Schema -
`nullable` is not a standalone keyword. It is a modifier on a sibling `type`, and it does
nothing whatsoever without one. So a node written like this:

    somefield:
      nullable: true
      oneOf:
        - $ref: '#/components/schemas/Something'

says `null` is allowed and then forbids it. The `oneOf` admits exactly one thing, an object
matching `Something`, and `null` is not that thing. Measured directly with
`openapi_schema_validator.OAS30Validator` (see the executable proof at the bottom of this file):

    {"nullable": true, "oneOf": [X]}   + None  ->  REJECTED, "is not valid under any of the
                                                   given schemas"
    {"nullable": true, "allOf": [X]}   + None  ->  REJECTED, "None for not nullable"
    {"oneOf": [X, {"nullable": true, "enum": [null]}]} + None  ->  ACCEPTED, and a
                                                   non-conforming object is still rejected

That third line is the sanctioned spelling, and the only one this guard exempts: a real branch
in the composition, saying `null` in a way the validator can act on.

**What the failure looked like in the wild.** On 2026-08-23 the canon carried 26 nodes with a
`nullable: true` and no sibling `type` - 24 beside a `oneOf`/`anyOf`/`allOf`, and 2 (the two
`seed` request fields) with nothing beside them at all. Six of the 24 were the optional blocks
of `AdminParticipantMetricsResponse`, which are `required` and present-and-null whenever the
caller omits `equivalent` (`app/core/admin/metrics.py:120` returns before they are computed).
A real 200 body from `GET /admin/participants/{pid}/metrics`, captured through the test client,
failed validation against authority number one on **all six** properties, twice each:

    at .counterparty: None for not nullable
    at .counterparty: None is not of type 'object'
    ... and the same pair for concentration, distribution, rank, capacity, activity

A correct response, rejected by the document that is supposed to describe it. That is the exact
class program 011 exists to close, and it had survived every earlier pass because a reader's eye
reads `nullable: true` and stops there.

**Why the rule is spelled "no sibling `type`" rather than "beside a composition".** The two bare
`seed` nodes had no composition either, and they were equally empty of meaning - a lone
`nullable: true` constrains nothing, so the field's type went undeclared while the document
looked as though it had said something. Narrowing the rule to compositions would have left them
standing. It also, and more importantly, leaves `{"$ref": ..., "nullable": true}` uncovered:
OpenAPI 3.0.3 ignores *every* sibling of a `$ref`, so that spelling is inert in the same way and
this predicate catches it for free.

**Fixing a flagged site is a decision, not a rewrite.** Each one is settled against the Python
model and its writers, never against the canon's own prose:

* `null` really does reach the wire -> keep the composition, add a `{nullable: true, enum: [null]}`
  branch, drop the inert sibling. **Fifteen** sites needed this.
* `null` never occurs -> the `nullable: true` was simply false; delete it and leave the
  composition alone. **Eleven** sites needed this. Adding a null branch to those would have made
  a lie validate, which is the same defect facing the other way.

The split is 15 + 11 = 26, measured by walking this predicate over the canon at the fix's parent
and reading each site's shape afterwards. Three earlier statements of it - two here saying "nine",
one in the commit message saying twelve and eleven - added up to 18 and 23 and were simply wrong;
T1108 caught them. **And 18 of the 26 were written by this wave**, in slices 4 through 8: at the
programme's own starting commit the predicate finds 8. The defect is mostly one this wave
introduced and then repaired, which is worth knowing, because the schema-authoring habit that
produced those 18 is the same one that produced the `nullable`-beside-a-non-null-`enum` sites the
second guard in this file now covers.

`test_sanctioned_null_branches_sit_inside_a_composition` below keeps the exemption from becoming
a loophole: `{nullable: true, enum: [null]}` is meaningful only as a member of a `oneOf`/`anyOf`,
and as a property schema in its own right it would declare a field that can only ever be null.

--------------------------------------------------------------------------------------------
**The second shape, added 2026-08-23: `nullable: true` beside an `enum` that omits `null`.**

The rule above asks for a sibling `type`, and these nodes all had one - so they passed it, and
they were still broken. `nullable` widens a `type`. It does not widen an `enum`, and where both
are present the `enum` is the narrower constraint, so the pair says "null is allowed" and then
lists the values null is not among. Measured, and re-run in
`test_nullable_does_not_widen_an_enum` at the bottom of this file:

    {"type": "integer", "nullable": True, "enum": [-1, 0, 1]}       + None  ->  REJECTED
    {"type": "integer", "nullable": True, "enum": [-1, 0, 1, None]} + None  ->  ACCEPTED,
                                                                       and 5 still REJECTED

Five sites carried it. Two were written by this wave -- `AdminGraphParticipant.net_sign` and
`.viz_color_key` -- and they were the worst of the five, because `_attach_net_viz` returns
before computing anything when `?equivalent=` is absent (app/api/v1/admin.py:1482-1485). The
DEFAULT call to `GET /admin/graph/snapshot` therefore returned a body the canon rejected on
every participant, and the whole contract suite was green.

The fix is the same decision as above, taken per site against the writers, and it went both
ways: three sites gained `null` as an enum MEMBER (the two graph nodes plus
`SimulatorGraphNode.net_sign`, all three of which really do emit null), and two had the
`nullable: true` DELETED (`SimulatorGraphNodePatch.net_sign`, whose only writer computes an int
at app/core/simulator/viz_patch_helper.py:284-292, and `MetricSeries.unit`, whose two writers
both pass a unit from a literal table). Widening all five would have made two lies validate.

This guard is static, so it reads nodes no test ever exercises. Its runtime complement -
`test_p011_responses_conform_to_the_canon.py` - validates real 2xx bodies against this document
and catches what a reader cannot see; neither subsumes the other.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest
import yaml

# Keys whose VALUES are data, not schemas. A body example that happens to contain the string
# "nullable" is not a declaration, and descending into one would let a false positive - or a
# forged pass - in through the example block.
_NOT_SCHEMA_POSITIONS = {"example", "examples", "default", "enum"}


def _canon() -> dict[str, Any]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _is_sanctioned_null_branch(node: dict[str, Any]) -> bool:
    """The one spelling of `null` that OpenAPI 3.0.3 actually honours.

    Exactly `enum: [null]` - not `enum: [null, "something"]`, which constrains a type this
    predicate can no longer see, and not a bare `nullable: true`, which is the defect itself.
    """

    return node.get("nullable") is True and node.get("enum") == [None]


def offending_sites(document: Any) -> list[str]:
    """Every `nullable: true` that has no sibling `type` and is not the sanctioned null branch.

    Walks the WHOLE document - `paths`, request bodies, parameters, responses, components -
    because request schemas carry this defect exactly as response schemas do: four of the 24
    original sites were the `policy` nodes of `TrustLineCreateRequest` and
    `TrustLineUpdateRequest`, where the question is what the server accepts.
    """

    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if node.get("nullable") is True and "type" not in node:
                if not _is_sanctioned_null_branch(node):
                    found.append(path or "<root>")
            for key, value in node.items():
                if key in _NOT_SCHEMA_POSITIONS:
                    continue
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(document, "")
    return found


def nullable_beside_a_non_null_enum(document: Any) -> list[str]:
    """Every `nullable: true` sitting next to an `enum` that does not list `null`.

    Disjoint from `offending_sites` by construction: that predicate fires only where there is
    NO sibling `type`, and every node this one has ever flagged had one. Keeping them separate
    keeps the two failure messages giving different advice, because the fixes differ - one adds
    a branch to a composition, the other edits an enum.
    """

    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            enum = node.get("enum")
            if (
                node.get("nullable") is True
                and isinstance(enum, list)
                and not any(value is None for value in enum)
            ):
                found.append(path or "<root>")
            for key, value in node.items():
                if key in _NOT_SCHEMA_POSITIONS:
                    continue
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(document, "")
    return found


def sanctioned_null_branches(document: Any) -> list[tuple[str, bool]]:
    """Locate every `{nullable: true, enum: [null]}` and say whether it sits in a composition."""

    found: list[tuple[str, bool]] = []

    def walk(node: Any, path: str, inside_composition: bool) -> None:
        if isinstance(node, dict):
            if _is_sanctioned_null_branch(node):
                found.append((path or "<root>", inside_composition))
            for key, value in node.items():
                if key in _NOT_SCHEMA_POSITIONS:
                    continue
                child = f"{path}.{key}" if path else str(key)
                if key in {"oneOf", "anyOf"} and isinstance(value, list):
                    for index, branch in enumerate(value):
                        walk(branch, f"{child}[{index}]", True)
                else:
                    walk(value, child, False)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", False)

    walk(document, "", False)
    return found


# ------------------------------------------------------------------------------------------
# The guard
# ------------------------------------------------------------------------------------------


def test_canon_declares_no_nullable_without_a_sibling_type() -> None:
    sites = offending_sites(_canon())
    assert sites == [], (
        f"{len(sites)} node(s) in api/openapi.yaml carry `nullable: true` with no sibling "
        "`type`. In OpenAPI 3.0.3 that keyword modifies a sibling `type` and nothing else, so "
        "each of these states nothing - and beside a `oneOf`/`anyOf`/`allOf` it actively "
        "FORBIDS the null it appears to allow.\n\nDecide each one against the Python model and "
        "its writers, not against the canon's prose: if null reaches the wire, add a "
        "`{nullable: true, enum: [null]}` branch to the composition and drop the inert sibling; "
        "if it does not, delete the `nullable: true` and leave the composition alone. Do not "
        "add a null branch to make a false claim validate.\n\nSites:\n  "
        + "\n  ".join(sites)
    )


def test_canon_declares_no_nullable_beside_an_enum_that_forbids_null() -> None:
    sites = nullable_beside_a_non_null_enum(_canon())
    assert sites == [], (
        f"{len(sites)} node(s) in api/openapi.yaml carry `nullable: true` next to an `enum` "
        "that does not contain `null`. The enum is the narrower constraint and it wins, so "
        "each of these FORBIDS the null it appears to allow - the same defect as the guard "
        "above, wearing a sibling `type` so that guard cannot see it.\n\nDecide each one "
        "against the writers: if null reaches the wire, add `null` to the enum (and keep the "
        "`nullable: true`, which is then true and doing work); if it does not, delete the "
        "`nullable: true` and leave the enum alone. Do not widen an enum to silence this.\n\n"
        "Sites:\n  " + "\n  ".join(sites)
    )


def test_sanctioned_null_branches_sit_inside_a_composition() -> None:
    """The exemption is for a BRANCH, not for a property schema.

    `{nullable: true, enum: [null]}` standing alone as a property declares a field that can only
    ever be null, which no field here is. Without this check the exemption above would be a
    ready-made way to silence the guard: paste `enum: [null]` next to the offending `nullable`
    and the site disappears from the report while still describing nothing.
    """

    branches = sanctioned_null_branches(_canon())
    assert branches, "expected the canon to carry explicit null branches; found none"
    stray = [path for path, inside in branches if not inside]
    assert stray == [], (
        "these `{nullable: true, enum: [null]}` nodes are not members of a `oneOf`/`anyOf`, so "
        "they declare a null-only value rather than a null branch:\n  " + "\n  ".join(stray)
    )


# ------------------------------------------------------------------------------------------
# Counter-checks: a guard nobody has tried to break proves nothing.
# ------------------------------------------------------------------------------------------

# The shape of the real defect, reduced. `AdminParticipantMetricsResponse.counterparty` and
# `AdminGraphTransactionItem.error` looked exactly like this before 2026-08-23.
_BROKEN_ONEOF = {"nullable": True, "oneOf": [{"$ref": "#/components/schemas/Thing"}]}
_BROKEN_ALLOF = {"nullable": True, "allOf": [{"$ref": "#/components/schemas/Thing"}]}
_BROKEN_BARE = {"nullable": True}
_BROKEN_REF_SIBLING = {"nullable": True, "$ref": "#/components/schemas/Thing"}

_FIXED_NULL_BRANCH = {
    "oneOf": [
        {"$ref": "#/components/schemas/Thing"},
        {"nullable": True, "enum": [None]},
    ]
}
_FIXED_DELETED = {"oneOf": [{"type": "string"}, {"type": "number"}]}
_FIXED_TYPED = {"type": "string", "nullable": True}


def _document_around(schema: Any) -> dict[str, Any]:
    """Bury the fragment where the real ones live: in a request body, under a path."""

    return {
        "openapi": "3.0.3",
        "paths": {
            "/thing": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"field": schema},
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


@pytest.mark.parametrize(
    "name,schema",
    [
        ("nullable beside oneOf", _BROKEN_ONEOF),
        ("nullable beside allOf", _BROKEN_ALLOF),
        ("nullable with nothing beside it", _BROKEN_BARE),
        ("nullable beside a $ref, which 3.0.3 ignores entirely", _BROKEN_REF_SIBLING),
    ],
)
def test_predicate_flags_the_broken_form(name: str, schema: dict[str, Any]) -> None:
    sites = offending_sites(_document_around(schema))
    assert sites == [
        "paths./thing.post.requestBody.content.application/json.schema.properties.field"
    ], f"the predicate did not flag {name}: {sites}"


@pytest.mark.parametrize(
    "name,schema",
    [
        ("explicit null branch inside the oneOf", _FIXED_NULL_BRANCH),
        ("the inert nullable simply deleted", _FIXED_DELETED),
        ("nullable doing its actual job, beside a type", _FIXED_TYPED),
    ],
)
def test_predicate_passes_the_fixed_form(name: str, schema: dict[str, Any]) -> None:
    assert offending_sites(_document_around(schema)) == [], (
        f"the predicate wrongly flagged {name}"
    )


# The second shape, reduced. `AdminGraphParticipant.net_sign` was literally this.
_BROKEN_ENUM_INT = {"type": "integer", "nullable": True, "enum": [-1, 0, 1]}
_BROKEN_ENUM_STR = {"type": "string", "nullable": True, "enum": ["a", "b"]}

_FIXED_ENUM_WIDENED = {"type": "integer", "nullable": True, "enum": [-1, 0, 1, None]}
_FIXED_NULLABLE_DELETED = {"type": "integer", "enum": [-1, 0, 1]}


@pytest.mark.parametrize(
    "name,schema",
    [
        ("nullable beside an integer enum", _BROKEN_ENUM_INT),
        ("nullable beside a string enum", _BROKEN_ENUM_STR),
    ],
)
def test_enum_predicate_flags_the_broken_form(name: str, schema: dict[str, Any]) -> None:
    sites = nullable_beside_a_non_null_enum(_document_around(schema))
    assert sites == [
        "paths./thing.post.requestBody.content.application/json.schema.properties.field"
    ], f"the predicate did not flag {name}: {sites}"


@pytest.mark.parametrize(
    "name,schema",
    [
        ("null added to the enum, nullable kept", _FIXED_ENUM_WIDENED),
        ("nullable deleted, enum left alone", _FIXED_NULLABLE_DELETED),
        ("an enum with no nullable at all", {"type": "string", "enum": ["a"]}),
        ("nullable with no enum beside it", {"type": "string", "nullable": True}),
    ],
)
def test_enum_predicate_passes_the_fixed_form(name: str, schema: dict[str, Any]) -> None:
    assert nullable_beside_a_non_null_enum(_document_around(schema)) == [], (
        f"the predicate wrongly flagged {name}"
    )


def test_the_two_predicates_do_not_shadow_each_other() -> None:
    """Each shape must be caught by its own guard, and by exactly one of them.

    If either predicate silently absorbed the other's cases, deleting one guard would look
    harmless while removing real coverage - and the two failure messages give opposite advice.
    """

    with_type = _document_around(copy.deepcopy(_BROKEN_ENUM_INT))
    assert nullable_beside_a_non_null_enum(with_type) != []
    assert offending_sites(with_type) == []

    without_type = _document_around(copy.deepcopy(_BROKEN_ONEOF))
    assert offending_sites(without_type) != []
    assert nullable_beside_a_non_null_enum(without_type) == []


def test_enum_predicate_ignores_an_example_that_looks_like_one() -> None:
    document = _document_around(
        {"type": "object", "example": {"nullable": True, "enum": ["a", "b"]}}
    )
    assert nullable_beside_a_non_null_enum(document) == []


def test_enum_predicate_reaches_into_components_and_responses() -> None:
    """The five real sites were all in `components/schemas`, two levels inside `properties`."""

    document = {
        "openapi": "3.0.3",
        "components": {
            "schemas": {
                "A": {"type": "object", "properties": {"n": copy.deepcopy(_BROKEN_ENUM_INT)}}
            }
        },
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": copy.deepcopy(_BROKEN_ENUM_STR),
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    assert sorted(nullable_beside_a_non_null_enum(document)) == [
        "components.schemas.A.properties.n",
        "paths./x.get.responses.200.content.application/json.schema.items",
    ]


def test_predicate_reaches_every_depth_the_defect_hides_at() -> None:
    """Four of the original 26 sites were two `properties` levels down, inside a request body.

    A walker that only swept `components/schemas/*/properties` would have reported 20 and gone
    green on the trustline policy nodes.
    """

    document = _document_around({"type": "object", "properties": {"policy": {
        "type": "object", "properties": {"max_hop_usage": copy.deepcopy(_BROKEN_ONEOF)}}}})
    sites = offending_sites(document)
    assert len(sites) == 1 and sites[0].endswith(
        "properties.field.properties.policy.properties.max_hop_usage"
    ), sites

    # ... and inside a response, a parameter, and a composition branch.
    nested = {
        "openapi": "3.0.3",
        "components": {"schemas": {"A": {"oneOf": [copy.deepcopy(_BROKEN_BARE)]}}},
        "paths": {
            "/x": {
                "get": {
                    "parameters": [{"name": "q", "in": "query",
                                    "schema": copy.deepcopy(_BROKEN_ALLOF)}],
                    "responses": {"200": {"content": {"application/json": {
                        "schema": {"type": "array",
                                   "items": copy.deepcopy(_BROKEN_ONEOF)}}}}},
                }
            }
        },
    }
    assert len(offending_sites(nested)) == 3, offending_sites(nested)


def test_predicate_ignores_the_word_nullable_inside_an_example() -> None:
    """An example body is data. Descending into one is how a guard gets forged past."""

    document = _document_around({
        "type": "object",
        "example": {"nullable": True, "oneOf": ["not a schema"]},
    })
    assert offending_sites(document) == []


def test_a_null_only_enum_does_not_launder_the_defect() -> None:
    """`enum: [null, "x"]` is not the sanctioned branch: it still leaves the type undeclared."""

    assert offending_sites(_document_around(
        {"nullable": True, "enum": [None, "x"], "oneOf": [{"type": "string"}]}
    )) != []
    assert offending_sites(_document_around({"nullable": True, "enum": [None]})) == []


def test_stray_null_branch_check_bites() -> None:
    """The companion guard, broken on purpose: a null branch used as a property schema."""

    document = _document_around({"nullable": True, "enum": [None]})
    branches = sanctioned_null_branches(document)
    assert branches and all(not inside for _, inside in branches), branches

    inside_oneof = _document_around(copy.deepcopy(_FIXED_NULL_BRANCH))
    branches = sanctioned_null_branches(inside_oneof)
    assert branches and all(inside for _, inside in branches), branches


# ------------------------------------------------------------------------------------------
# The premise itself, executed.
# ------------------------------------------------------------------------------------------


def test_openapi_30_really_ignores_nullable_without_a_sibling_type() -> None:
    """The measured evidence in the docstring, re-run rather than remembered.

    `openapi-schema-validator` is a declared dev dependency as of 2026-08-23, so this executes
    unconditionally. It used to be guarded with `importorskip`, which meant the premise of the
    whole file could pass by not running - the exact vacuum this programme keeps finding.
    """

    from openapi_schema_validator import OAS30Validator as oas30

    thing = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    conforming = {"a": "x"}
    not_conforming = {"b": 1}

    def accepts(schema: dict[str, Any], instance: Any) -> bool:
        return not list(oas30(schema).iter_errors(instance))

    # The defect: the sibling `nullable` is inert, so null is refused.
    assert not accepts({"nullable": True, "oneOf": [thing]}, None)
    assert not accepts({"nullable": True, "allOf": [thing]}, None)
    assert not accepts({"nullable": True, "anyOf": [thing]}, None)

    # The fix: a real branch. Null passes, and the composition still discriminates.
    fixed = {"oneOf": [thing, {"nullable": True, "enum": [None]}]}
    assert accepts(fixed, None)
    assert accepts(fixed, conforming)
    assert not accepts(fixed, not_conforming)

    # And the control: beside a `type`, `nullable` does exactly what it looks like.
    assert accepts({"type": "string", "nullable": True}, None)
    assert not accepts({"type": "string"}, None)


def test_nullable_does_not_widen_an_enum() -> None:
    """The premise of the SECOND guard, executed. This is the whole of `F-011-10`'s general form.

    A reader sees `nullable: true` and stops. The validator reads the `enum` as well, finds
    `null` absent from it, and refuses - which is why five nodes with a perfectly good sibling
    `type` still described a body the service could not send.
    """

    from openapi_schema_validator import OAS30Validator as oas30

    def accepts(schema: dict[str, Any], instance: Any) -> bool:
        return not list(oas30(schema).iter_errors(instance))

    broken = {"type": "integer", "nullable": True, "enum": [-1, 0, 1]}
    assert not accepts(broken, None)
    assert accepts(broken, 1)

    widened = {"type": "integer", "nullable": True, "enum": [-1, 0, 1, None]}
    assert accepts(widened, None)
    assert accepts(widened, 1)
    # Widening for null must not widen for anything else.
    assert not accepts(widened, 5)

    # The other direction: with the `nullable` deleted the enum says exactly what it meant.
    deleted = {"type": "integer", "enum": [-1, 0, 1]}
    assert not accepts(deleted, None)
    assert accepts(deleted, 0)

    # Strings behave the same way - `viz_color_key` and `MetricSeries.unit` were this shape.
    assert not accepts({"type": "string", "nullable": True, "enum": ["a"]}, None)
    assert accepts({"type": "string", "nullable": True, "enum": ["a", None]}, None)
