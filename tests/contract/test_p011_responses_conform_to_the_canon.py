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

**T1109: this gate used to be able to pass without checking anything.** External review ran the
aggregate on its own - `pytest <this file>::test_every_observed_2xx_body_validates_against_the_
canon` - and got `1 passed in 0.05s` over zero requests, and separately showed four ways a
response could be counted as coverage without its body ever being validated, plus one way a
recorder exception could disappear. Three things changed, and the counter-checks at the bottom
of this file exist because a hardening nobody has tried to break proves nothing:

1. `observed` now means *validated*. Everything else is a named category in
   `UNVALIDATED_2XX_ALLOWANCE` below, and the aggregate fails on any row that is not there.
2. A recorder exception is collected, not swallowed, and the aggregate fails on any.
3. A session that measured nothing SKIPS with a message saying so, instead of passing. A filtered
   run is an ordinary thing and must not be a failure; a false green is not. The fail-closed half
   - "the wrapper is alive and recording" - stays where it belongs, in
   `test_the_harness_records_a_real_response`, which makes a real request in the smallest tier.

**T1110: and then the allowance list became the next way to pass without checking anything.**
Point 1 above bought its fail-closed behaviour with a list of excused rows, and external review
found that the list had itself failed open. The row for
`GET /simulator/runs/{run_id}/artifacts/{name}` said "an honest binary download" over a route
that serves `status.json`, `summary.json`, `last_tick.json`, `events.ndjson` and `bundle.zip`
through one `FileResponse` (app/api/v1/simulator.py:2938) - so a real JSON body, and a body of
broken JSON, both came back `OBSERVED set() CHECKED 0 AGGREGATE PASS`. Two more things changed:

4. The engine classifies on what ARRIVED, not on what the canon promised, and a media type the
   canon does not declare for an operation is a finding rather than a skippable category. See
   `openapi_response_conformance.classify_response_declaration`.
5. An allowance row now has to name the media types it excuses, and the aggregate confronts the
   row with every response recorded under it. `UNVALIDATED_2XX_ALLOWANCE` maps to `Allowance`,
   not to prose, for exactly that reason - the prose was never compared to anything.

The canon was the other half of it, and the larger half: `api/openapi.yaml` declared one
`application/octet-stream` body for that route, which is a media type it never sends. It now
declares the four JSON documents, the `text/plain` the NDJSON export really arrives as, and both
spellings of the ZIP type - so the JSON artifacts are VALIDATED and the allowance row covers only
what genuinely has no schema.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pytest
from httpx import AsyncClient

from app.config import settings

from tests.contract.openapi_response_conformance import (
    HARNESS,
    REPORT_TEST,
    Harness,
    canon_operations,
    guarded_record,
    is_json_media_type,
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

# The stale-entry check - for BOTH fixed lists in this file - is only meaningful over a full
# session. On a filtered run the fixtures that produce a row may simply not execute, and an entry
# that did not reproduce because nothing exercised it is not a stale entry. 70 operations is the
# measured full-tier denominator (unchanged by T1109's re-definition of `observed`, which is now
# "a response of this operation was really checked"); 60 leaves the suite room to shrink without
# turning this into a second ratchet.
_LEDGER_QUORUM = 60


# ------------------------------------------------------------------------------------------
# The second fixed list, added by `T1109`. It may only shrink.
# ------------------------------------------------------------------------------------------

# Coverage means validation. Every 2xx response the wrapper sees is either validated against the
# schema the canon declares for it, or it lands in one of the categories in
# `openapi_response_conformance.UNVALIDATED_CATEGORIES` - and then it must be named HERE, with
# the reason it cannot be validated.
#
# This list exists because before `T1109` the engine had five separate silent `return`s: a body
# that was not JSON, a body that would not parse, a 2xx status the canon does not declare, a
# declared status with no JSON schema, and a request that matched no canonical path. Three of
# them reached the report and the aggregate never read it; two reached nothing at all. In every
# case the operation had ALREADY been added to `observed`, so an unvalidated response bought
# coverage, and a filtered run could report a green over zero bodies. A named allowance is the
# opposite of a silent skip: an entry is a claim that nothing here CAN be validated, and
# `_assert_the_session_is_conformant` deletes nothing - it fails on any row that is not below,
# and (over a full session) on any row below that no longer happens.
#
# Measured 2026-08-23 over `ENV=test python -m pytest tests -m "not postgres"` (on a task-local
# SQLite file, so a second suite running in parallel could not perturb it): 893 responses through
# the wrapper, 784 of them 2xx, 781 JSON bodies validated against the canon, 0 recorder errors,
# 70 of 96 declared operations observed, and exactly TWO unvalidated rows. Both are structural,
# not debt - neither is a body anyone could validate against `api/openapi.yaml`, and neither
# counts towards the 70:
class Allowance(NamedTuple):
    """One excused row, and the claim about the RESPONSE that excuses it.

    `media_types` is the whole point of this type existing. Until `T1110` the allowance was
    `key -> prose`, and the aggregate compared nothing but the key: the row said "a binary
    download, nothing to validate" and the engine happily filed a real `application/json` body
    under it, because the row's justification was never confronted with the response. Measured
    at `62b018a`, driving the reviewer's payloads through the harness:

        PAYLOAD b'{"status":"ok"}'  OBSERVED set() CHECKED 0  AGGREGATE PASS
        PAYLOAD b'{broken'          OBSERVED set() CHECKED 0  AGGREGATE PASS

    Now every media type recorded under a row has to appear in `media_types`, so a row survives
    only while what arrives under it is still what the row says arrives. Start sending something
    else - anything else - and the allowance fails instead of covering it.
    """

    media_types: tuple[str, ...]
    reason: str


# Categories in which an allowance row may cover a JSON response. Exactly one, and not because
# JSON is unvalidatable there: `/openapi.json` is not an operation of the canon at all, so there
# is no schema in this document to check it against, and the check that it is right is a whole
# other test. Everywhere else a JSON body means a schema was owed and an allowance row is the
# wrong answer - see `_assert_the_session_is_conformant`.
_CATEGORIES_THAT_MAY_EXCUSE_JSON = frozenset({"no-canonical-path"})


UNVALIDATED_2XX_ALLOWANCE: dict[str, Allowance] = {
    "no-canonical-path GET /openapi.json 200": Allowance(
        media_types=("application/json",),
        reason=(
            "the generated document itself. `test_openapi_contract.py` fetches `/openapi.json` "
            "to diff it against `api/openapi.yaml`, and that route is FastAPI's own - it is "
            "not, and must not be, an operation the canon declares, so this document holds no "
            "schema to check it against. It IS `application/json` and the row says so; the body "
            "is read and compared, by the contract diff rather than by this check. This is the "
            "only row allowed to name a JSON media type, and "
            "`_CATEGORIES_THAT_MAY_EXCUSE_JSON` is why."
        ),
    ),
    (
        "declared-with-no-json-media-type "
        "GET /simulator/runs/{run_id}/artifacts/{name} 200"
    ): Allowance(
        media_types=("text/plain", "application/zip", "application/x-zip-compressed"),
        reason=(
            "the two artifacts this suite really downloads, and neither has a JSON body. "
            "`events.ndjson` arrives as `text/plain` (mimetypes knows no `.ndjson`, so the "
            "`FileResponse` at app/api/v1/simulator.py:2938 falls back) and `bundle.zip` as "
            "`application/zip` or `application/x-zip-compressed` depending on the host's "
            "mimetypes database. All three are now DECLARED in the canon for this operation, so "
            "this row means what it says: the canon describes the media type that arrived, and "
            "that media type has no schema to validate against. What it no longer covers is the "
            "rest of the route: `status.json`, `summary.json` and `last_tick.json` arrive as "
            "`application/json`, the canon declares that too, and their bodies are VALIDATED. "
            "A JSON body can no longer reach this row - the engine will not put it here and "
            "`_CATEGORIES_THAT_MAY_EXCUSE_JSON` would reject it if it did. The downloads are "
            "driven by tests/integration/test_simulator_artifacts_events_ndjson.py and "
            "tests/integration/test_simulator_super_smoke.py, and what they contain is checked "
            "there."
        ),
    ),
}


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

    # This test, not the aggregate, is the fail-CLOSED half of the T1109 fix. The aggregate SKIPS
    # a session that measured nothing, which is right for a filtered run and useless as a guard
    # that the wrapper still works - so the guard lives here, in the smallest tier that ever runs,
    # where it is a failure and not a skip.
    assert not HARNESS.recorder_errors, HARNESS.recorder_errors


def test_the_canon_denominator_is_what_we_think_it_is() -> None:
    """A guard on the guard: a typo in path matching would shrink the denominator silently."""

    declared = canon_operations(load_canon())
    assert len(declared) >= 85, len(declared)
    assert ("GET", "/health") in declared
    assert ("POST", "/payments") in declared


# ------------------------------------------------------------------------------------------
# The aggregate assertion. Deferred to the end of the session by pytest_collection_modifyitems.
# ------------------------------------------------------------------------------------------


def _summary(report: dict[str, Any], written: str | None) -> str:
    return (
        f"\n\nobserved {len(report['observed_operations'])} of {report['canon_operations']} "
        f"declared operations over {report['two_xx_seen']} 2xx response(s) "
        f"({report['responses_seen']} response(s) in all); {report['bodies_checked']} JSON "
        f"bodies validated, {report['empty_bodies_confirmed']} declared-bodyless responses "
        f"confirmed empty, {len(report['unvalidated_2xx'])} unvalidated row(s); report written "
        f"to {written}\nunobserved operations (this check says NOTHING about them):\n  "
        + "\n  ".join(report["unobserved_operations"])
    )


def _assert_the_session_is_conformant(report: dict[str, Any], written: str | None = None) -> None:
    """Everything the aggregate asserts, over a report - so a counter-check can drive it too.

    The four checks below are in order of "what would make the rest of this meaningless": a
    recorder that threw measured less than it says; a body that was skipped without a reason was
    never measured at all; only then is the list of non-conforming nodes worth reading.
    """

    summary = _summary(report, written)

    # 1. The recorder itself. `guarded_record` deliberately does not re-raise - a recorder that
    #    can fail the request under test is a recorder that gets deleted - so this is the ONLY
    #    place a recorder defect can surface. It used to be `except Exception: pass`, and a
    #    harness that had stopped working reported the same empty finding list as a clean run.
    assert not report["recorder_errors"], (
        f"{len(report['recorder_errors'])} exception(s) escaped Harness.record during this "
        "session. Every one of them is a response that was NOT measured, and the emptiness of "
        "the finding list below is worth nothing until they are gone:\n  "
        + "\n  ".join(
            f"{row['where']}  {row['error']}\n{row['traceback']}"
            for row in report["recorder_errors"]
        )
        + summary
    )

    # 2. Coverage means validation. Every 2xx response whose body was NOT validated is named
    #    here, with the reason it could not be, and has to be allowed explicitly. A streaming
    #    body and a file download are legitimate; a body that silently failed to parse is not,
    #    and before T1109 the two were indistinguishable because both were an early `return`.
    unvalidated = report["unvalidated_2xx"]
    detail = {row["key"]: row["detail"] for row in report["unvalidated_2xx_detail"]}
    unexpected = [key for key in unvalidated if key not in UNVALIDATED_2XX_ALLOWANCE]
    assert not unexpected, (
        f"{len(unexpected)} 2xx response(s) were observed but never validated against "
        "api/openapi.yaml, and none of them is in UNVALIDATED_2XX_ALLOWANCE.\n\n"
        "This is not a licence to add rows. Each category means something different:\n"
        "  no-canonical-path                 the request did not resolve to any operation the "
        "canon declares - either the canon is missing the path or match_path is wrong;\n"
        "  status-not-declared               the service returned a 2xx the canon does not "
        "declare for that operation;\n"
        "  (a status the canon declares with no `content` never reaches this list: an empty "
        "body under it IS a check and counts as coverage, and a non-empty one is a "
        "non-conformance finding instead)\n"
        "  declared-with-no-json-media-type  the canon declares a non-JSON body (a download, a "
        "stream). Legitimate, and the reason belongs in the allowance;\n"
        "  declared-json-without-a-schema    the canon declares application/json and no schema "
        "- a canon gap, fix the canon;\n"
        "  body-was-not-json                 the canon declares a JSON schema and the service "
        "sent something else - one of the two is wrong;\n"
        "  body-was-empty                    the canon declares a JSON schema and the service "
        "sent no bytes - one of the two is wrong;\n"
        "  body-did-not-parse                the service sent broken JSON. Never legitimate;\n"
        "  body-was-not-read                 a streamed response nobody read. Legitimate for "
        "SSE, and the reason belongs in the allowance.\n\n"
        + "\n".join(f"  {key}" + (f"    [{detail[key]}]" if detail.get(key) else "")
                    for key in unexpected)
        + summary
    )

    # 2b. An allowance row is a CLAIM about the responses behind it, and until T1110 nothing
    #     compared the claim to them. The key alone says only "this operation, this status, this
    #     category" - and `GET /simulator/runs/{run_id}/artifacts/{name} 200` is one key over a
    #     route that serves JSON, NDJSON and ZIP, so a row written for the zip excused the JSON
    #     too. Every media type recorded under a row must be one the row names.
    falsified: list[str] = []
    for row in report["unvalidated_2xx_detail"]:
        allowed = UNVALIDATED_2XX_ALLOWANCE.get(row["key"])
        if allowed is None:
            continue
        for media in row["media_types"]:
            if media not in allowed.media_types:
                falsified.append(
                    f"  {row['key']}\n      carried {media or '<no content-type header>'}, "
                    f"which the row does not name (it names "
                    f"{', '.join(allowed.media_types)})"
                )
            elif (
                is_json_media_type(media)
                and row["category"] not in _CATEGORIES_THAT_MAY_EXCUSE_JSON
            ):
                falsified.append(
                    f"  {row['key']}\n      carried {media}, and a JSON body is not excusable "
                    f"under category {row['category']}: the canon owes it a schema"
                )
    assert not falsified, (
        f"{len(falsified)} allowance row(s) no longer describe the responses they excuse. A row "
        "in UNVALIDATED_2XX_ALLOWANCE is a claim that nothing validatable arrived under it, and "
        "this assertion is what turns that claim into a measurement. Do NOT widen the row's "
        "`media_types` to make this pass: if what arrived is JSON, the canon has to declare it "
        "with a schema so the body is validated; if it is a new binary type the service really "
        "sends, the canon has to declare that too, and only then does the row get to name it.\n"
        + "\n".join(falsified)
        + summary
    )

    # 3. The findings themselves.
    measured = {(row["operation"], row["node"]): row for row in report["non_conforming"]}
    appeared = sorted(key for key in measured if key not in KNOWN_NONCONFORMING)
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

    # 4. Both fixed lists may only shrink, and a stale entry is only meaningful over a full
    #    session: on a filtered run the traffic that produces a row may simply not happen.
    if len(report["observed_operations"]) < _LEDGER_QUORUM:
        return

    stale = sorted(key for key in KNOWN_NONCONFORMING if key not in measured)
    assert not stale, (
        f"{len(stale)} entr(ies) in KNOWN_NONCONFORMING no longer reproduce over a full "
        "session. The list may only shrink - delete them:\n  "
        + "\n  ".join(f"{operation}  {node}" for operation, node in stale)
        + summary
    )

    seen = set(unvalidated)
    stale_allowance = sorted(key for key in UNVALIDATED_2XX_ALLOWANCE if key not in seen)
    assert not stale_allowance, (
        f"{len(stale_allowance)} entr(ies) in UNVALIDATED_2XX_ALLOWANCE no longer reproduce "
        "over a full session, which means either the operation is now validated (good - delete "
        "the row) or nothing exercises it any more (delete it too; an allowance nothing tests "
        "is a licence with no measurement behind it):\n  "
        + "\n  ".join(stale_allowance)
        + summary
    )


def _vacuity_skip_reason(report: dict[str, Any]) -> str | None:
    """Why this session measured nothing, or `None` if it measured something.

    This function is the whole of the distinction the brief for `T1109` asks for, and it is
    deliberately a SKIP rather than a pass and rather than a failure.

    * A **pass** was the defect. `pytest <this file>::test_every_observed_2xx_body_validates_
      against_the_canon` on its own made zero requests and reported `1 passed in 0.05s` - a green
      that asserted an empty set against an empty set.
    * A **failure** would turn every ordinary filtered run red, which is a different way of
      making the gate useless: people delete gates that fail for doing nothing wrong.
    * A **skip** says out loud that this run measured nothing, and cannot be mistaken for the
      gate having held.

    A session that made requests but silently validated none of them is NOT this case, and does
    not reach here: `_assert_the_session_is_conformant` runs first and every unvalidated 2xx is
    named there. By the time this function is consulted, every skipped body is one the allowance
    justifies.

    The fail-CLOSED half - "the wrapper is installed and really recording" - is not this
    function's job and must not be. It lives in `test_the_harness_is_installed` and
    `test_the_harness_records_a_real_response`, which make a real request inside the smallest
    tier that ever runs, so a harness that has quietly stopped working fails rather than skips.
    """

    if report["responses_seen"] == 0:
        return (
            "this session made no HTTP requests at all, so no response body was compared to "
            "api/openapi.yaml. Reported as a skip, not a pass: an empty finding list over an "
            "empty denominator is not evidence. Run the full suite "
            '(`ENV=test pytest tests -m "not postgres"`) for the real measurement.'
        )
    if report["two_xx_seen"] == 0:
        return (
            f"this session made {report['responses_seen']} request(s) and not one of them "
            "returned 2xx, so there was no success body for this check to look at."
        )
    if report["bodies_checked"] == 0 and report["empty_bodies_confirmed"] == 0:
        return (
            f"this session saw {report['two_xx_seen']} 2xx response(s) and validated none of "
            "them: every one fell into a category UNVALIDATED_2XX_ALLOWANCE justifies "
            f"({', '.join(report['unvalidated_2xx']) or 'none listed'}). Nothing was measured, "
            "so this is a skip and not a pass."
        )
    return None


def test_every_observed_2xx_body_validates_against_the_canon() -> None:
    report = HARNESS.report()
    written = HARNESS.write_report()

    # Assert FIRST, skip second. A session that dropped a body without a named reason is a
    # defect even when it dropped every body, and it must not be able to hide behind the
    # vacuity skip below.
    _assert_the_session_is_conformant(report, written)

    reason = _vacuity_skip_reason(report)
    if reason is not None:
        pytest.skip(reason)


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


# ------------------------------------------------------------------------------------------
# Counter-checks for the five fail-open modes `T1109` found.
#
# Every one of these reproduces the reviewer's measurement - a `Harness` driven directly, the
# way they drove it - and every one of them FAILS against the implementation as it stood at
# `6a5bfe8`. Measured there, not assumed: the non-JSON, unparseable, undeclared-2xx and
# unschemad-2xx cases each returned `observed=['GET /health'] bodies_checked=0
# non_conforming=[]`, and the unmatched case returned an empty `observed` with the row visible
# only in a JSON report nothing asserted on. They are written in two halves on purpose:
#
#   * the ENGINE half asserts that the response was not credited as coverage and that it landed
#     in a named category, which is the part `openapi_response_conformance.py` is responsible for;
#   * the AGGREGATE half runs `_assert_the_session_is_conformant` over the resulting report and
#     requires it to raise, which is the part `test_...conform_to_the_canon.py` is responsible
#     for. Before T1109 the categories existed for three of these modes and the aggregate never
#     read them, so proving only the first half would have proved nothing.
# ------------------------------------------------------------------------------------------


def _response(
    method: str,
    path: str,
    status: int = 200,
    *,
    content: bytes = b"",
    content_type: str | None = "application/json",
    stream: bool = False,
) -> httpx.Response:
    """A real `httpx.Response` bound to a real `httpx.Request`, built to order.

    Real objects rather than stubs: `record` reads `status_code`, `request.method`,
    `request.url.path`, `headers`, `content` and `json()`, and a stub that got any one of those
    subtly wrong would make these counter-checks agree with a harness that does not exist.
    """

    request = httpx.Request(method, "http://test" + path)
    headers = {} if content_type is None else {"content-type": content_type}
    if stream:
        # An unread streamed response: `.content` raises `httpx.ResponseNotRead`.
        return httpx.Response(
            status, headers=headers, content=iter([content]), request=request
        )
    return httpx.Response(status, headers=headers, content=content, request=request)


def _recorded(document: dict[str, Any], *responses: httpx.Response) -> Harness:
    """Feed responses through the exact call the installed wrapper makes."""

    harness = _harness_over(document)
    for response in responses:
        guarded_record(harness, response)
    return harness


def _canon_without_the_health_schema() -> dict[str, Any]:
    document = copy.deepcopy(load_canon())
    del document["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    return document


def test_counter_check_mode_non_json_a_body_that_is_not_json_is_not_coverage() -> None:
    """Mode `NON_JSON`: `observed=["GET /health"] bodies_checked=0 non_conforming=[]`.

    The canon declares a JSON schema for `GET /health 200`; the service sends `text/plain`. One
    of the two is wrong, and neither is "nothing to see here". Before the fix the operation was
    added to `observed` before the content type was looked at, so this response bought coverage
    it had not earned and the aggregate printed a clean report.
    """

    harness = _recorded(
        load_canon(), _response("GET", "/api/v1/health", content=b"ok", content_type="text/plain")
    )

    assert harness.bodies_checked == 0
    assert ("GET", "/health") not in harness.observed, (
        "a body that was never validated must not count as coverage - this is the exact "
        "reviewer reproduction, where observed was ['GET /health'] with bodies_checked=0"
    )
    assert harness.unvalidated_keys() == ["body-was-not-json GET /health 200"]

    with pytest.raises(AssertionError, match="body-was-not-json GET /health 200"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_invalid_json_an_unparseable_body_is_not_coverage() -> None:
    """Mode `INVALID_JSON`: the parse failure used to be an unlogged `except Exception: return`."""

    harness = _recorded(
        load_canon(), _response("GET", "/api/v1/health", content=b'{"status": ')
    )

    assert harness.bodies_checked == 0
    assert ("GET", "/health") not in harness.observed
    assert harness.unvalidated_keys() == ["body-did-not-parse GET /health 200"]
    detail = harness.report()["unvalidated_2xx_detail"][0]["detail"]
    assert "JSONDecodeError" in detail or "Expecting" in detail, detail

    with pytest.raises(AssertionError, match="body-did-not-parse GET /health 200"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_undeclared_2xx_reaches_the_assertion() -> None:
    """Mode `UNDECLARED_2XX`: a 2xx the canon does not declare at all.

    The reviewer's run put `GET /health 299` in `observed_status_not_declared` *and* left
    `GET /health` in `observed` with an empty failure list, because the aggregate read neither.
    """

    harness = _recorded(
        load_canon(), _response("GET", "/api/v1/health", 299, content=b'{"status": "ok"}')
    )

    assert harness.bodies_checked == 0
    assert ("GET", "/health") not in harness.observed
    assert harness.unvalidated_keys() == ["status-not-declared GET /health 299"]

    with pytest.raises(AssertionError, match="status-not-declared GET /health 299"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_unschemad_2xx_reaches_the_assertion() -> None:
    """Mode `UNSCHEMAD_2XX`: `application/json` declared with no `schema` under it.

    Sabotage rather than a live example, because the canon currently has none - which is exactly
    why this mode could rot unnoticed. Deleting the node is the smallest change that produces it.
    """

    harness = _recorded(
        _canon_without_the_health_schema(),
        _response("GET", "/api/v1/health", content=b'{"status": "ok"}'),
    )

    assert harness.bodies_checked == 0
    assert ("GET", "/health") not in harness.observed
    assert harness.unvalidated_keys() == ["declared-json-without-a-schema GET /health 200"]

    with pytest.raises(AssertionError, match="declared-json-without-a-schema GET /health 200"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_unmatched_path_reaches_the_assertion() -> None:
    """`unmatched` was the third registry the aggregate never read.

    `/api/v1/nothing/like/this` and not `/openapi.json`, because `/openapi.json` is in
    UNVALIDATED_2XX_ALLOWANCE and a counter-check that lands on an allowed row would pass for
    the wrong reason.
    """

    harness = _recorded(
        load_canon(), _response("GET", "/api/v1/nothing/like/this", content=b"{}")
    )

    assert harness.observed == set()
    assert harness.unvalidated_keys() == [
        "no-canonical-path GET /api/v1/nothing/like/this 200"
    ]

    with pytest.raises(AssertionError, match="no-canonical-path"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_an_empty_body_under_a_declared_schema_is_not_coverage() -> None:
    """The canon promises a JSON body and the service sends none.

    Not one of the reviewer's five, but the same shape: the old engine returned early on
    `not response.content` after it had already credited the operation.
    """

    harness = _recorded(load_canon(), _response("GET", "/api/v1/health", content=b""))

    assert harness.bodies_checked == 0
    assert ("GET", "/health") not in harness.observed
    assert harness.unvalidated_keys() == ["body-was-empty GET /health 200"]

    with pytest.raises(AssertionError, match="body-was-empty GET /health 200"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_a_streamed_body_nobody_read_is_a_category_and_not_a_crash() -> None:
    """`.content` on an unread stream raises; that must be a named row, not a recorder error.

    The canon declares `text/event-stream` for the two SSE operations, so a live SSE response
    is filed under `declared-with-no-json-media-type` before its body is touched. This test
    covers the other order - a declared JSON schema over a body that was never read - which is
    what would happen if an SSE operation were ever re-declared as JSON.
    """

    harness = _recorded(
        load_canon(), _response("GET", "/api/v1/health", content=b"{}", stream=True)
    )

    assert harness.recorder_errors == [], harness.recorder_errors
    assert harness.bodies_checked == 0
    assert harness.unvalidated_keys() == ["body-was-not-read GET /health 200"]

    with pytest.raises(AssertionError, match="body-was-not-read GET /health 200"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_a_non_json_media_declaration_is_named_rather_than_skipped() -> None:
    """The legitimate case, and the proof that "legitimate" still has to be written down.

    `GET /simulator/runs/{run_id}/artifacts/{name}` declares `text/plain` for `events.ndjson`.
    Nothing can be validated against a `type: string`, so the row exists, is allowed by name in
    UNVALIDATED_2XX_ALLOWANCE, and does NOT count as coverage.

    **Moved by T1110**, and this is the one existing counter-check that had to move. It used to
    send `application/octet-stream` for a file called `report.json`, which was a response the
    service never produces: `FileResponse` with no `media_type` sends what
    `mimetypes.guess_type` says, and the canon no longer declares `octet-stream` for this
    operation because nothing ever arrives under it. The body it drives now is the real one -
    `events.ndjson`, `text/plain`, measured off a live `FileResponse` - and the assertion is the
    same assertion.
    """

    harness = _recorded(
        load_canon(),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/events.ndjson",
            content=b'{"type":"tick"}\n',
            content_type="text/plain; charset=utf-8",
        ),
    )

    key = (
        "declared-with-no-json-media-type "
        "GET /simulator/runs/{run_id}/artifacts/{name} 200"
    )
    assert harness.unvalidated_keys() == [key]
    assert harness.observed == set()
    assert key in UNVALIDATED_2XX_ALLOWANCE, (
        "this is the allowance's own subject; if it is no longer there the row above would be a "
        "failure and this test would be asserting the wrong thing"
    )
    # The charset parameter must not defeat the allowance's media-type check.
    assert harness.report()["unvalidated_2xx_detail"][0]["media_types"] == ["text/plain"]
    # ... and being allowed, it does not fail the aggregate.
    _assert_the_session_is_conformant(harness.report())


# ------------------------------------------------------------------------------------------
# T1110. The allowance list added by T1109 to close the fail-open findings opened one of its own:
# `GET /simulator/runs/{run_id}/artifacts/{name}` is a HETEROGENEOUS route excused by a single
# row written as though it were a binary download. Reproduced here before anything was changed,
# at `62b018a`:
#
#   FILERESPONSE status.json     'application/json'
#   FILERESPONSE summary.json    'application/json'
#   FILERESPONSE last_tick.json  'application/json'
#   FILERESPONSE events.ndjson   'text/plain; charset=utf-8'
#   FILERESPONSE bundle.zip      'application/x-zip-compressed'
#
#   PAYLOAD b'{"status":"ok"}'  OBSERVED set() CHECKED 0  AGGREGATE PASS
#   PAYLOAD b'{broken'          OBSERVED set() CHECKED 0  AGGREGATE PASS
#
# Two independent causes, and both are covered below: `Harness.record` classified from the media
# type the CANON declared and returned before reading the body, and the aggregate compared the
# allowance by key and never looked at the `content-type` it had recorded next to it.
# ------------------------------------------------------------------------------------------


_ARTIFACT_ROUTE = "/simulator/runs/{run_id}/artifacts/{name}"
_ARTIFACT_ALLOWANCE_KEY = f"declared-with-no-json-media-type GET {_ARTIFACT_ROUTE} 200"

# The three JSON artifacts, exactly as the application writes them. Not invented shapes: each is
# the literal dict at the anchor named beside it, so a schema that these bodies satisfy is a
# schema the service satisfies.
#
# app/core/simulator/artifacts.py:67 - `init_run_artifacts`, the FIRST shape of status.json.
_STATUS_JSON = json.dumps(
    {
        "api_version": "simulator-api/1",
        "run_id": "run_abc",
        "scenario_id": "sc_1",
        "mode": "real",
        "created_at": "2026-08-24T00:00:00+00:00",
        "seed": 42,
    }
).encode()

# The second shape: `get_run_status(...).model_dump(mode="json", by_alias=True)` on stop
# (app/core/simulator/runtime_impl.py:198 -> artifacts.py:343) - a `RunStatus`, the same document
# `GET /simulator/runs/{run_id}` returns.
_RUN_STATUS = {
    "api_version": "simulator-api/1",
    "run_id": "run_abc",
    "scenario_id": "sc_1",
    "mode": "real",
    "state": "stopped",
}

# app/core/simulator/artifacts.py:313-344 - summary.json.
_SUMMARY_JSON = json.dumps(
    {
        "api_version": "simulator-api/1",
        "generated_at": "2026-08-24T00:00:01+00:00",
        "run_id": "run_abc",
        "scenario_id": "sc_1",
        "mode": "real",
        "state": "stopped",
        "status": _RUN_STATUS,
    }
).encode()

# app/core/simulator/artifacts.py:64 - last_tick.json as the init path writes it.
_LAST_TICK_JSON = b'{"tick_index": 0, "sim_time_ms": 0}'


def _canon_with_the_artifact_route_binary_only() -> dict[str, Any]:
    """The canon as it stood at `62b018a`: one binary media type for the whole route."""

    document = copy.deepcopy(load_canon())
    document["paths"][_ARTIFACT_ROUTE]["get"]["responses"]["200"]["content"] = {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
    }
    return document


def test_counter_check_mode_json_from_the_artifact_route_is_validated_not_excused() -> None:
    """Mode `JSON_UNDER_A_BINARY_DECLARATION`: `OBSERVED set() CHECKED 0  AGGREGATE PASS`.

    The reviewer's first payload. A real `status.json` body, `application/json`, from the route
    whose allowance row called it a binary download. Against `62b018a` this was `bodies_checked=0`
    with the row silently allowed; the canon now declares `application/json` for this operation
    and the engine classifies on what arrived, so the body is VALIDATED and counts as coverage.

    All four JSON documents the route can serve are driven, one per `oneOf` branch, so the canon's
    new declaration is exercised rather than asserted: the init `status.json`, the `RunStatus`
    that overwrites it on stop, `summary.json` and `last_tick.json`.
    """

    harness = _recorded(
        load_canon(),
        *(
            _response(
                "GET",
                f"/api/v1/simulator/runs/r1/artifacts/{name}",
                content=body,
                content_type="application/json",
            )
            for name, body in (
                ("status.json", _STATUS_JSON),
                ("status.json", json.dumps(_RUN_STATUS).encode()),
                ("summary.json", _SUMMARY_JSON),
                ("last_tick.json", _LAST_TICK_JSON),
            )
        ),
    )

    assert harness.bodies_checked == 4, (
        "the reviewer reproduction: a real JSON body from this route was skipped as a binary "
        "download and never validated"
    )
    assert ("GET", _ARTIFACT_ROUTE) in harness.observed
    assert harness.unvalidated_keys() == []
    assert harness.report()["non_conforming"] == [], (
        "every one of the four bodies is the literal dict its writer builds; a finding here "
        "means the schemas added for them describe something else"
    )
    _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_broken_json_from_the_artifact_route_is_a_failure() -> None:
    """Mode `BROKEN_JSON_UNDER_A_BINARY_DECLARATION`: the same PASS over a body that is garbage.

    The reviewer's second payload. `b'{broken'` under `application/json` also came back
    `AGGREGATE PASS` at `62b018a` - the allowance row excused it without anyone reading a byte.
    It is now `body-did-not-parse`, which no row allows and which the aggregate raises on.
    """

    harness = _recorded(
        load_canon(),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/status.json",
            content=b"{broken",
            content_type="application/json",
        ),
    )

    assert harness.bodies_checked == 0
    assert harness.observed == set()
    assert harness.unvalidated_keys() == [f"body-did-not-parse GET {_ARTIFACT_ROUTE} 200"]

    with pytest.raises(AssertionError, match="body-did-not-parse"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_json_the_canon_does_not_declare_is_a_finding_not_a_row() -> None:
    """The engine rule, held independently of the canon that currently satisfies it.

    Sabotage the document back to `application/octet-stream` only and send the same real JSON
    body. The point is that fixing `api/openapi.yaml` is not what closed this: even with a canon
    that declares nothing but a binary type, a JSON body is a FINDING - not a category, because a
    category can be written into UNVALIDATED_2XX_ALLOWANCE and this must not be excusable.
    """

    harness = _recorded(
        _canon_with_the_artifact_route_binary_only(),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/status.json",
            content=_STATUS_JSON,
            content_type="application/json",
        ),
    )

    assert harness.bodies_checked == 0
    assert harness.observed == set()
    assert harness.unvalidated_keys() == [], (
        "this must not be filed as a skippable category - the allowance list would then be able "
        "to excuse it, which is the hole T1110 closed"
    )
    finding = harness.report()["non_conforming"]
    assert len(finding) == 1, finding
    assert finding[0]["operation"] == f"GET {_ARTIFACT_ROUTE} 200"
    assert finding[0]["value"] == "application/json"
    assert "does not declare" in finding[0]["message"]

    with pytest.raises(AssertionError, match="do not validate against api/openapi.yaml"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_the_artifact_schema_rejects_a_body_that_is_not_an_object() -> None:
    """`type: object` under `application/json` is load-bearing, not decoration.

    Every JSON artifact the application writes is a JSON object (status.json, summary.json,
    last_tick.json). If the route ever served a bare array under `.json`, the canon would be
    wrong and this says so - which is the difference between validating a body and merely
    parsing it.
    """

    harness = _recorded(
        load_canon(),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/status.json",
            content=b"[1, 2, 3]",
            content_type="application/json",
        ),
    )

    assert harness.bodies_checked == 1
    nodes = [row["node"] for row in harness.report()["non_conforming"]]
    assert nodes == ["$"], harness.report()["non_conforming"]

    with pytest.raises(AssertionError, match="do not validate against api/openapi.yaml"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_an_allowance_row_is_checked_against_the_response_it_excuses() -> None:
    """The aggregate half. A row may not cover a media type it does not name.

    The engine now keeps JSON out of `declared-with-no-json-media-type`, so the allowance's own
    check is demonstrated with a media type the canon declares and the row does not: a canon that
    also served `application/pdf` here would produce the very same key, and at `62b018a` the
    aggregate would have waved it through because it compared keys and nothing else.
    """

    document = copy.deepcopy(load_canon())
    document["paths"][_ARTIFACT_ROUTE]["get"]["responses"]["200"]["content"][
        "application/pdf"
    ] = {"schema": {"type": "string", "format": "binary"}}

    harness = _recorded(
        document,
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/report.pdf",
            content=b"%PDF-1.4",
            content_type="application/pdf",
        ),
    )

    assert harness.unvalidated_keys() == [_ARTIFACT_ALLOWANCE_KEY]
    assert _ARTIFACT_ALLOWANCE_KEY in UNVALIDATED_2XX_ALLOWANCE
    assert "application/pdf" not in UNVALIDATED_2XX_ALLOWANCE[
        _ARTIFACT_ALLOWANCE_KEY
    ].media_types

    with pytest.raises(AssertionError, match="no longer describe the responses they excuse"):
        _assert_the_session_is_conformant(harness.report())


def test_counter_check_a_row_records_every_media_type_that_arrived_under_it() -> None:
    """One key, several responses - and the row has to remember all of them.

    `unvalidated_detail` used to be assigned rather than accumulated, so the last response under
    a key overwrote every earlier one. On this route that is not hypothetical: `events.ndjson`
    and `bundle.zip` share the key within a single session, and a `application/json` detail
    could have been overwritten by a `text/plain` one that arrived after it - leaving a row whose
    recorded justification was true of only the last response behind it.
    """

    harness = _recorded(
        load_canon(),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/events.ndjson",
            content=b'{"type":"tick"}\n',
            content_type="text/plain; charset=utf-8",
        ),
        _response(
            "GET",
            "/api/v1/simulator/runs/r1/artifacts/bundle.zip",
            content=b"PK\x03\x04",
            content_type="application/x-zip-compressed",
        ),
    )

    assert harness.unvalidated_keys() == [_ARTIFACT_ALLOWANCE_KEY]
    row = harness.report()["unvalidated_2xx_detail"][0]
    assert row["media_types"] == ["application/x-zip-compressed", "text/plain"], row
    _assert_the_session_is_conformant(harness.report())


async def test_counter_check_every_artifact_the_app_writes_arrives_under_a_declared_media_type(
    tmp_path: Path,
) -> None:
    """The reviewer's own reproduction, kept as a test: real `FileResponse` objects.

    `artifacts_download` is `return FileResponse(path)` with no `media_type`
    (app/api/v1/simulator.py:2938), so the wire type is `mimetypes.guess_type(name)` with
    starlette's `text/plain` fallback - a fact about the host, not about this repository, which
    is why it is measured here rather than asserted from the helper in
    `app/core/simulator/helpers.py`. Every file the application writes into a run's artifacts
    directory is driven through a real `FileResponse` and its media type has to be one the canon
    declares for the download operation. If mimetypes ever answers differently, or a new artifact
    is added, this fails and the canon gets updated - which is the whole of what T1110 is about.
    """

    from starlette.applications import Starlette
    from starlette.responses import FileResponse
    from starlette.routing import Route

    # app/core/simulator/artifacts.py: :64 :67 :84 (init), :343 :344 (finalize), :333 (bundle),
    # :361 (real tick). This list is the artifacts directory as the application builds it.
    written = {
        "status.json": _STATUS_JSON,
        "summary.json": _SUMMARY_JSON,
        "last_tick.json": _LAST_TICK_JSON,
        "events.ndjson": b'{"type":"tick"}\n',
        "bundle.zip": b"PK\x03\x04",
    }
    for name, payload in written.items():
        (tmp_path / name).write_bytes(payload)

    async def download(request: Any) -> Any:
        return FileResponse(tmp_path / request.path_params["name"])

    app = Starlette(
        routes=[Route("/api/v1/simulator/runs/{run_id}/artifacts/{name}", download)]
    )

    declared = {
        media.lower()
        for media in load_canon()["paths"][_ARTIFACT_ROUTE]["get"]["responses"]["200"]["content"]
    }

    harness = _harness_over(load_canon())
    transport = httpx.ASGITransport(app=app)
    arrived: dict[str, str] = {}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for name in written:
            # `send(build_request(...))` and not `get(...)`: the session-wide wrapper patches
            # `AsyncClient.request`, and these five responses come from a Starlette app built
            # here, not from the application. Letting them reach `HARNESS` would credit
            # `GET /simulator/runs/{run_id}/artifacts/{name}` as observed coverage in the
            # full-session report on the strength of a fixture - measured: it did, and the
            # report said 71 operations and 784 bodies where the suite really produced 70 and
            # 781. Coverage means validation of the real service's traffic.
            response = await client.send(
                client.build_request("GET", f"/api/v1/simulator/runs/r1/artifacts/{name}")
            )
            assert response.status_code == 200, name
            media = response.headers["content-type"].split(";", 1)[0].strip().lower()
            arrived[name] = media
            guarded_record(harness, response)

    undeclared = {name: media for name, media in arrived.items() if media not in declared}
    assert not undeclared, (
        "the canon does not declare the media type these artifacts really arrive under; it "
        f"declares {sorted(declared)}\n  " + "\n  ".join(
            f"{name}: {media}" for name, media in sorted(undeclared.items())
        )
    )

    # The three JSON artifacts are validated; the other two are the honest allowance row.
    assert harness.bodies_checked == 3, arrived
    assert harness.unvalidated_keys() == [_ARTIFACT_ALLOWANCE_KEY]
    _assert_the_session_is_conformant(harness.report())


def test_counter_check_mode_wrapper_exception_is_recorded_not_swallowed() -> None:
    """Mode `WRAPPER_EXCEPTION_SWALLOWED 200 0 set()`.

    The reviewer made `record` throw and observed the wrapper return a perfectly ordinary
    response with an empty registry behind it. The exception still must not break the request -
    that is the reason the `try` is there at all - so it is collected and asserted at the end of
    the session instead.
    """

    harness = _harness_over(load_canon())

    def explode(response: Any) -> None:
        raise RuntimeError("the recorder is broken")

    harness.record = explode  # type: ignore[method-assign]
    guarded_record(harness, _response("GET", "/api/v1/health", content=b'{"status":"ok"}'))

    assert len(harness.recorder_errors) == 1, harness.recorder_errors
    row = harness.recorder_errors[0]
    assert row["error"] == "RuntimeError: the recorder is broken"
    assert row["where"] == "GET /api/v1/health 200"
    assert "explode" in row["traceback"], row["traceback"]

    with pytest.raises(AssertionError, match="escaped Harness.record"):
        _assert_the_session_is_conformant(harness.report())


async def test_counter_check_a_broken_recorder_still_lets_the_request_through(
    client: AsyncClient,
) -> None:
    """The other half of the same mode, end to end through the real installed wrapper.

    A second `Harness` is installed on top of the live one for the duration of one request, so
    the production wrapper - not a hand-rolled copy of it - is what swallows and files the
    exception. `uninstall` puts the live wrapper back; the live harness sees this request too.
    """

    saboteur = Harness()

    def explode(response: Any) -> None:
        raise RuntimeError("the recorder is broken, end to end")

    saboteur.record = explode  # type: ignore[method-assign]
    saboteur.install()
    try:
        response = await client.get("/api/v1/health")
    finally:
        saboteur.uninstall()

    assert response.status_code == 200, "a recorder defect must never fail the request under test"
    assert response.json()["status"]
    assert [row["error"] for row in saboteur.recorder_errors] == [
        "RuntimeError: the recorder is broken, end to end"
    ]
    assert HARNESS.installed, "the live wrapper must be back in place"


# ------------------------------------------------------------------------------------------
# The filtered run: the reviewer's headline reproduction.
# ------------------------------------------------------------------------------------------


def test_the_vacuity_verdict_tells_a_filtered_run_from_a_broken_one() -> None:
    """`_vacuity_skip_reason` over synthetic reports - the decision table, in one place."""

    def report(**overrides: Any) -> dict[str, Any]:
        base = {
            "responses_seen": 0,
            "two_xx_seen": 0,
            "bodies_checked": 0,
            "empty_bodies_confirmed": 0,
            "unvalidated_2xx": [],
        }
        base.update(overrides)
        return base

    assert "made no HTTP requests" in (_vacuity_skip_reason(report()) or "")
    assert "not one of them returned 2xx" in (
        _vacuity_skip_reason(report(responses_seen=9)) or ""
    )
    assert "validated none of them" in (
        _vacuity_skip_reason(report(responses_seen=9, two_xx_seen=4)) or ""
    )
    # One validated body is enough to make the run a measurement.
    assert _vacuity_skip_reason(report(responses_seen=9, two_xx_seen=4, bodies_checked=1)) is None
    # ... and so is one confirmed-empty body under a `content`-less declaration.
    assert (
        _vacuity_skip_reason(
            report(responses_seen=9, two_xx_seen=4, empty_bodies_confirmed=1)
        )
        is None
    )


def test_the_aggregate_run_alone_no_longer_reports_success_over_zero_bodies() -> None:
    """The reviewer's exact command, executed. It used to print `1 passed in 0.05s`.

    A subprocess and not a unit test on `_vacuity_skip_reason`, because the finding was about the
    whole path: collection, `tests/conftest.py` installing the wrapper, and the aggregate being
    the only selected item. The unit test above covers the decision; this covers the run.

    No recursion risk: the child selects one node id, which is not this test.
    """

    if os.environ.get("GEO_CONFORMANCE_NO_SUBPROCESS"):  # pragma: no cover - escape hatch
        pytest.skip("GEO_CONFORMANCE_NO_SUBPROCESS is set")

    repo_root = Path(__file__).resolve().parents[2]
    node_id = f"tests/contract/{Path(__file__).name}::{REPORT_TEST}"
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-s",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            node_id,
        ],
        cwd=str(repo_root),
        env={**os.environ, "ENV": "test", "GEO_CONFORMANCE_NO_SUBPROCESS": "1"},
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = completed.stdout + completed.stderr

    assert "1 skipped" in output, output
    assert not re.search(r"\b\d+ passed", output), (
        "the aggregate reported a PASS over zero requests - this is the T1109 reproduction:\n"
        + output
    )
    assert "made no HTTP requests" in output, output


def _canon_with_health_declared_bodyless() -> dict[str, Any]:
    """`GET /health 200` re-declared the way a real 204 is: a description and no `content`.

    Sabotage, because the canon currently has no such response at all - `T1109` measured it and
    the count is zero - so the only way to exercise this branch is to write one. It is the shape
    a `204 No Content` takes, and the engine has to treat it as a CHECK ("the canon says there is
    no body; is there one?") rather than as another silent skip.
    """

    document = copy.deepcopy(load_canon())
    del document["paths"]["/health"]["get"]["responses"]["200"]["content"]
    return document


def test_a_declared_bodyless_response_with_an_empty_body_is_coverage_not_a_skip() -> None:
    harness = _recorded(
        _canon_with_health_declared_bodyless(),
        _response("GET", "/api/v1/health", content=b"", content_type=None),
    )

    assert harness.unvalidated_keys() == [], harness.unvalidated_keys()
    assert harness.bodies_checked == 0, "no schema was exercised, so this is not a body check"
    assert harness.empty_bodies_confirmed == 1
    assert ("GET", "/health") in harness.observed, (
        "the canon's claim about this response WAS compared to the response and held; counting "
        "it as unobserved would understate coverage exactly as crediting an unvalidated body "
        "overstated it"
    )
    _assert_the_session_is_conformant(harness.report())


def test_a_declared_bodyless_response_that_carries_a_body_is_a_non_conformance() -> None:
    """The other half, and the reason the branch above is not a loophole.

    If the canon says a status has no body and the service sends one, that is a defect in one of
    them - not a category, not an allowance, and certainly not coverage.
    """

    harness = _recorded(
        _canon_with_health_declared_bodyless(),
        _response("GET", "/api/v1/health", content=b'{"status":"ok"}'),
    )

    assert harness.observed == set()
    assert harness.empty_bodies_confirmed == 0
    assert [(row["operation"], row["node"]) for row in harness.non_conformance()] == [
        ("GET /health 200", "$")
    ]
    assert "declares this status with no `content`" in harness.non_conformance()[0]["message"]

    with pytest.raises(AssertionError, match="do not validate against api/openapi.yaml"):
        _assert_the_session_is_conformant(harness.report())
