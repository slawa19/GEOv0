"""RT-011-10 (runtime half): every 2xx body the suite produces must validate against the canon.

Program 011, finding `F-011-10`, general form.

`F-011-10` was closed in one narrow syntactic shape - `nullable: true` with no sibling `type`.
The general form is larger and it is not syntactic: **a node that says something the service
does not do**. Two spellings of it survived that slice, and both were found by running real
bodies against the document rather than by reading the document:

* `nullable: true` beside an `enum` that does not list `null`. `nullable` widens a `type`; it
  does not widen an `enum`. `OAS30Validator({'type':'integer','nullable':True,'enum':[-1,0,1]})`
  rejects `None`, measured, and the executable proof is in
  `test_p011_nullable_needs_a_sibling_type.py`.
* a property that really can be null, declared as a bare `$ref` or a bare typed schema. A
  successful payment sends `error: null`, `committed_at: null` and `routes: null`, and
  `PaymentResult.error` was `$ref: PaymentError`.

Neither is visible to a reader, and neither was visible to any check in this repository,
because until this file existed **nothing here validated a response body against
`api/openapi.yaml` at all**. The ~48 schemas this wave added were verified by ad-hoc runs
quoted in commit messages. AGENTS.md section 9: a claim is worth what an executed check proves.

**How it works.** `tests/contract/openapi_response_conformance.py` wraps
`httpx.AsyncClient.request` for the whole pytest session (installed from `tests/conftest.py`,
at import time, so nothing is missed) and validates every 2xx JSON body against the schema the
canon declares for that operation and that status, resolving `$ref`s against the whole
document. The aggregate assertion below is deferred to the end of the session by
`pytest_collection_modifyitems`, because it reads a registry the rest of the suite fills in.

**Why it rides on the suite instead of calling endpoints itself.** Hand-written calls cover
what their author thought of. This covers whatever the suite covers - about 70 operations -
grows for free, and exercises the *default* argument shapes, which is where the worst finding
lived: `GET /admin/graph/snapshot` with no `?equivalent=` returns `net_sign: null` on every
participant, and that is the ordinary call.

**What it cannot say.** Nothing about an operation nothing exercises. The report therefore
names the unobserved operations rather than counting them as passing, and the static guard in
`test_p011_nullable_needs_a_sibling_type.py` is the complement that reads nodes no test reaches.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.config import settings

from tests.contract.openapi_response_conformance import (
    HARNESS,
    Harness,
    canon_operations,
    load_canon,
    match_path,
    normalize_node,
    response_schema_pointer,
    validate_body,
)

# ------------------------------------------------------------------------------------------
# The fixed list. It may only shrink.
# ------------------------------------------------------------------------------------------

# FIRST measurement, 2026-08-23, by this harness over
# `ENV=test python -m pytest tests -m "not postgres"`: 70 of 96 declared operations observed,
# 732 bodies checked, 12 operations non-conforming across 19 (operation, node) pairs - with the
# whole contract suite green, because nothing in the repository had ever compared a response
# body to `api/openapi.yaml`.
#
# Ten of those twelve operations were canon defects, 17 of the 19 pairs, and all of them are
# fixed in `api/openapi.yaml` in the same change - each decided against the writing code exactly
# as the `F-011-10` slice decided its 18 sites. Nothing was loosened to make this test pass: two
# of the five `nullable`-beside-an-enum sites went the OTHER way and had the `nullable` DELETED,
# because null never reaches them (SimulatorGraphNodePatch.net_sign, MetricSeries.unit).
#
# An eleventh canon defect was found afterwards, by reading the writers rather than by measuring:
# `Participant.profile` was a bare `$ref`, and `create_participant` passes `profile=None`
# explicitly when the request omits it (app/core/participants/service.py:65). No fixture in the
# suite had ever omitted it, so the harness could not see it and the static guard could not
# either - a bare `$ref` has no `nullable` to be wrong about.
# `test_a_participant_created_without_a_profile_conforms` below now produces that body, which is
# what turned the reading into a measurement before the canon was changed.
#
# The ledger is EMPTY, and that is the point: every 2xx body this suite produces validates
# against the schema `api/openapi.yaml` declares for its operation and status.
#
# It was not empty when this file was written. Two rows recorded `TrustLine.policy` reading back
# as null on the two admin reads - and the cause turned out to be the fixtures, not the canon and
# not the service. Every writer in app/ produces a dict: TrustLineService.create writes
# `policy=data.policy or {}` (app/core/trustlines/service.py:190), update writes a dict
# (:334-336), the simulator trustline-create action omits the kwarg so the ORM default fires
# (app/api/v1/simulator.py:1116-1121), the injector and the real-mode seeder pass dicts
# (app/core/simulator/inject_executor.py:506, :622; real_scenario_seeder.py:213-224), and
# scripts/seed_db.py:359-382 coerces a non-dict. Two unit tests were inserting `policy=None`
# straight through the ORM, which bypasses the column's Python-side `default=`
# (app/db/models/trustline.py:15-21 - it fires only when the attribute is unset, and there is no
# server default), building a row the application cannot write. They now insert `{}`, and
# `test_the_policy_the_api_really_writes_is_an_object` below holds that claim by driving the real
# create path.
#
# Declaring `policy` nullable would have been the other way to make this list empty, and it would
# have weakened a true statement to accommodate a row nothing produces. The residual - a nullable
# column with no server default, which no writer ever leaves null - is schema hygiene rather than
# a contract defect, and is recorded in specs/BACKLOG.md.
#
# An entry here is a claim about the CODE, never a schema loosened to fit a body, and the
# aggregate test below fails if one stops reproducing over a full session. The list may only
# shrink; it is at zero, so any entry at all is now a regression.
KNOWN_NONCONFORMING: dict[tuple[str, str], str] = {}

# The stale-entry check is only meaningful over a full session. On a filtered run the fixtures
# that produce these two rows may simply not execute, and an entry that did not reproduce
# because nothing exercised it is not a stale entry. 70 operations is the measured full-tier
# denominator; 60 leaves the suite room to shrink without turning this into a second ratchet.
_LEDGER_QUORUM = 60


# ------------------------------------------------------------------------------------------
# Non-vacuity: the harness has to be alive, and it has to be looking at real traffic.
# ------------------------------------------------------------------------------------------


def test_the_harness_is_installed() -> None:
    """If the wrapper never went on, everything below is an empty set compared to an empty set."""

    assert HARNESS.installed, (
        "the response-conformance wrapper is not installed; tests/conftest.py should have "
        "called openapi_response_conformance.HARNESS.install() at import time"
    )


async def test_the_harness_records_a_real_response(client: AsyncClient) -> None:
    """One end-to-end round trip through the production path, inside the smallest tier.

    Running only `tests/contract` exercises almost no HTTP, so the aggregate assertion at the
    bottom of this file would pass over a near-empty denominator. This test makes the contract
    tier prove the machinery works regardless: a real request, through the real wrapper, landing
    in the real registry with the canonical path resolved.
    """

    before = HARNESS.bodies_checked
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert ("GET", "/health") in HARNESS.observed, (
        "the wrapper did not record GET /api/v1/health under its canonical path /health"
    )
    assert HARNESS.bodies_checked > before, "the wrapper recorded no body for a 200 JSON response"


def test_the_canon_denominator_is_what_we_think_it_is() -> None:
    """A guard on the guard: a typo in path matching would shrink the denominator silently."""

    declared = canon_operations(load_canon())
    assert len(declared) >= 85, len(declared)
    assert ("GET", "/health") in declared
    assert ("POST", "/payments") in declared


# ------------------------------------------------------------------------------------------
# The aggregate assertion. Deferred to the end of the session by pytest_collection_modifyitems.
# ------------------------------------------------------------------------------------------


def test_every_observed_2xx_body_validates_against_the_canon() -> None:
    report = HARNESS.report()
    written = HARNESS.write_report()

    measured = {(row["operation"], row["node"]): row for row in report["non_conforming"]}
    appeared = sorted(key for key in measured if key not in KNOWN_NONCONFORMING)

    summary = (
        f"\n\nobserved {len(report['observed_operations'])} of {report['canon_operations']} "
        f"declared operations, {report['bodies_checked']} bodies checked; report written to "
        f"{written}\nunobserved operations (this check says NOTHING about them):\n  "
        + "\n  ".join(report["unobserved_operations"])
    )

    assert not appeared, (
        f"{len(appeared)} response node(s) do not validate against api/openapi.yaml.\n\n"
        "Decide each one against the code that writes it, never by loosening the schema: if "
        "the value really occurs, say so in the canon (add `null` to the enum, or give the "
        "property a `oneOf` with a `{nullable: true, enum: [null]}` branch); if it does not, "
        "the BODY is the defect and the canon is right - fix what produced it and leave the "
        "canon alone.\n\n"
        + "\n".join(
            f"  {measured[key]['operation']}  {measured[key]['node']} = "
            f"{json.dumps(measured[key]['value'], default=str)}\n"
            f"      {measured[key]['message']}"
            for key in appeared
        )
        + summary
    )

    if len(report["observed_operations"]) < _LEDGER_QUORUM:
        return

    stale = sorted(key for key in KNOWN_NONCONFORMING if key not in measured)
    assert not stale, (
        f"{len(stale)} entr(ies) in KNOWN_NONCONFORMING no longer reproduce over a full "
        "session. The list may only shrink - delete them:\n  "
        + "\n  ".join(f"{operation}  {node}" for operation, node in stale)
        + summary
    )


async def test_the_policy_the_api_really_writes_is_an_object(
    client: AsyncClient, auth_user: dict
) -> None:
    """The claim behind both KNOWN_NONCONFORMING entries, executed instead of asserted in prose.

    A trustline created through the API comes back with `policy` as an OBJECT. That is why the
    canon is left saying so, and why the two null readings are a fixture defect rather than a
    canon defect: nothing the service writes puts NULL in that column. If this ever fails, the
    two ledger entries are wrong and the canon needs a null branch instead.
    """

    import base64

    from nacl.signing import SigningKey

    from app.core.auth.canonical import canonical_json
    from app.core.auth.crypto import generate_keypair

    public_key, private_key = generate_keypair()
    counterparty = {
        "display_name": "Counterparty",
        "type": "person",
        "public_key": public_key,
        "profile": {},
    }
    counterparty_key = SigningKey(base64.b64decode(private_key))
    created = await client.post(
        "/api/v1/participants",
        json={
            **counterparty,
            "signature": base64.b64encode(
                counterparty_key.sign(canonical_json(counterparty)).signature
            ).decode(),
        },
    )
    assert created.status_code == 201, created.text
    to_pid = created.json()["pid"]

    admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
    made = await client.post(
        "/api/v1/admin/equivalents",
        json={"code": "TLP", "name": "Trustline policy probe", "precision": 2},
        headers=admin,
    )
    assert made.status_code in (200, 201, 409), made.text

    payload = {"to": to_pid, "equivalent": "TLP", "limit": "100.00"}
    author = SigningKey(base64.b64decode(auth_user["private_key"]))
    response = await client.post(
        "/api/v1/trustlines",
        json={
            **payload,
            "signature": base64.b64encode(
                author.sign(canonical_json(payload)).signature
            ).decode(),
        },
        headers=auth_user["headers"],
    )
    assert response.status_code == 201, response.text

    policy = response.json()["policy"]
    assert isinstance(policy, dict), (
        "TrustLineService.create writes `policy=data.policy or {}` "
        "(app/core/trustlines/service.py:190); a null here would mean the KNOWN_NONCONFORMING "
        "entries for TrustLine.policy are wrong and the canon should carry a null branch"
    )


async def test_a_participant_created_without_a_profile_conforms(client: AsyncClient) -> None:
    """A body shape the suite never produced, added so the harness can see it.

    Every existing fixture posts `profile: {}`, so no observed body ever carried
    `profile: null` - and `Participant.profile` was a bare `$ref`, which the static guard cannot
    judge either, because there is no `nullable` there to be wrong about. Omitting the key makes
    `create_participant` pass `profile=None` explicitly
    (app/core/participants/service.py:65), which bypasses the column's Python-side
    `default=dict` (app/db/models/participant.py:16), and the read emits null.

    The request is the point: this test exists to WIDEN what the harness observes, not to assert
    a field. The conformance check on the response happens in the wrapper, and the aggregate at
    the top of this file is where a regression would surface.
    """

    import base64

    from nacl.signing import SigningKey

    from app.core.auth.canonical import canonical_json
    from app.core.auth.crypto import generate_keypair

    public_key, private_key = generate_keypair()
    body = {"display_name": "No Profile", "type": "person", "public_key": public_key}
    key = SigningKey(base64.b64decode(private_key))
    response = await client.post(
        "/api/v1/participants",
        json={
            **body,
            "signature": base64.b64encode(
                key.sign(canonical_json(body)).signature
            ).decode(),
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["profile"] is None, (
        "the shape this test exists to produce did not occur; if the service stopped emitting "
        "a null profile, the canon's null branch on Participant.profile is now unbacked"
    )


# ------------------------------------------------------------------------------------------
# Counter-checks. A conformance check that has never been shown to fail proves nothing.
# ------------------------------------------------------------------------------------------


async def test_a_deliberately_wrong_canon_node_makes_the_check_fail(
    client: AsyncClient,
) -> None:
    """Break one named node of one named operation and watch the harness report it.

    Operation: `GET /health`. Node: the inline response schema's `status` property, retyped
    from `string` to `integer`. The whole production path runs - path matching, pointer lookup,
    `$ref` resolution, validation - on a real response object, differing from the live harness
    only in which document it holds.
    """

    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert isinstance(response.json()["status"], str)

    broken = copy.deepcopy(load_canon())
    _health_status_node(broken).clear()
    _health_status_node(broken).update({"type": "integer"})

    saboteur = _harness_over(broken)
    saboteur.record(response)

    findings = saboteur.non_conformance()
    assert [(row["operation"], row["node"]) for row in findings] == [
        ("GET /health 200", "$.status")
    ], findings
    assert "is not of type 'integer'" in findings[0]["message"], findings

    # ... and the same harness over the REAL canon is silent on the same response, so the
    # failure above is the sabotage and not the response.
    honest = _harness_over(load_canon())
    honest.record(response)
    assert honest.non_conformance() == [], honest.non_conformance()
    assert honest.bodies_checked == 1


async def test_the_two_defect_shapes_are_the_ones_the_harness_catches(
    client: AsyncClient,
) -> None:
    """Reproduce both shapes `F-011-10`'s general form is about, on a live body.

    Shape 1: `nullable: true` beside an `enum` that does not contain null - the five-site class
    that made `GET /admin/graph/snapshot` non-conforming on every participant.
    Shape 2: a nullable value declared as a bare typed schema - the `PaymentResult.error` class.
    """

    response = await client.get("/api/v1/health")
    assert response.status_code == 200

    # Shape 1, in miniature: `status` is a string; declare it a null-tolerant enum that forgets
    # to list the string, and `nullable` does not save it.
    shape_one = copy.deepcopy(load_canon())
    _health_status_node(shape_one).clear()
    _health_status_node(shape_one).update(
        {"type": "string", "nullable": True, "enum": ["not-the-value-we-return"]}
    )
    caught = _harness_over(shape_one)
    caught.record(response)
    assert [row["node"] for row in caught.non_conformance()] == ["$.status"]

    # Shape 2: `version` is present and a string. Declare a property the body sends as null
    # would be caught the same way - here, prove the validator refuses null against a bare
    # typed schema, which is precisely why `PaymentResult.error: $ref PaymentError` failed.
    document = {"components": {"schemas": {"E": {"type": "object"}}}, "paths": {}}
    assert validate_body(
        document, "/components/schemas/E", None
    ), "a bare typed schema must reject null - if it does not, the PaymentResult finding is not real"
    assert (
        validate_body(
            {
                "components": {
                    "schemas": {
                        "E": {
                            "oneOf": [
                                {"type": "object"},
                                {"nullable": True, "enum": [None]},
                            ]
                        }
                    }
                }
            },
            "/components/schemas/E",
            None,
        )
        == []
    ), "the sanctioned null branch must accept null"


def _health_status_node(document: dict[str, Any]) -> dict[str, Any]:
    """The `status` property of the INLINE 200 schema of `GET /health`.

    Named by pointer rather than by component name on purpose: this operation's body is written
    inline in `paths`, and a counter-check that sabotaged a `components` entry would prove the
    harness resolves component refs while saying nothing about inline ones.
    """

    return document["paths"]["/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["properties"]["status"]


def _harness_over(document: dict[str, Any]) -> Harness:
    from tests.contract.openapi_response_conformance import registry_for

    harness = Harness()
    harness._document = document
    harness._registry = registry_for(document)
    harness._paths = list(document.get("paths", {}).keys())
    return harness


# ------------------------------------------------------------------------------------------
# The resolution machinery, checked on its own. A mis-resolved path validates the wrong schema
# and reports a green that means nothing.
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "observed,expected",
    [
        ("/api/v1/health", "/health"),
        ("/health", "/health"),
        ("/api/v1/admin/participants", "/admin/participants"),
        # The specific literal must beat the template it also matches.
        ("/api/v1/admin/participants/stats", "/admin/participants/stats"),
        ("/api/v1/participants/me", "/participants/me"),
        ("/api/v1/participants/p_abc", "/participants/{pid}"),
        (
            "/api/v1/simulator/runs/r1/actions/payment-real",
            "/simulator/runs/{run_id}/actions/payment-real",
        ),
        ("/api/v1/nothing/like/this", None),
        ("/openapi.json", None),
    ],
)
def test_path_matching_resolves_to_the_operation_the_canon_declares(
    observed: str, expected: str | None
) -> None:
    canon = load_canon()
    assert match_path(observed, canon["paths"].keys()) == expected


def test_the_pointer_is_status_specific() -> None:
    canon = load_canon()
    assert response_schema_pointer(canon, "GET", "/health", 200) == (
        "/paths/~1health/get/responses/200/content/application~1json/schema"
    )
    # A status the operation does not declare has no schema, and is reported as such rather
    # than silently validated against the 200.
    assert response_schema_pointer(canon, "GET", "/health", 418) is None


def test_node_normalization_collapses_array_indices() -> None:
    assert normalize_node(["results", 3, "net_sign"]) == "$.results[].net_sign"
    assert normalize_node([]) == "$"
