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
# 2026-08-23 / p011_t1101 (`F-011-2`): the eight Interact Mode operations were published, so
# they enter these dictionaries for the first time. The ratchet only ever walked operations the
# canon already declared, which is exactly why a money-moving surface could drift unmeasured.
#
# All three counts move by +8 - one entry per newly published operation - and every new entry
# reproduces a class already measured on older operations, verified pair by pair:
#   TRANSPORT_HEADER 59 -> 67  canon declares neither X-Admin-Token nor X-Simulator-Owner for
#                              these eight. It cannot: both are optional identity transports here,
#                              and the canonical policy guard below requires every declared
#                              X-Admin-Token to be `required: true`. Declaring it was tried and
#                              reverted.
#   SECURITY         59 -> 67  same cause - `_normalized_security` deliberately folds the transport
#                              headers in, so the same eight gaps surface a second time.
#   ERROR_RESPONSE   83 -> 91  the shared envelopes differ canon-vs-generated (`details` carries
#                              `additionalProperties: true` in the canon, `nullable: true` in the
#                              generated form). Pre-existing on ErrorEnvelope and
#                              SimulatorActionError alike; the eight inherit it by referencing them.
#
# What did NOT move is the point: SUCCESS 71, REQUEST 13, PARAMETER 22 are untouched, because the
# canon was written to state what the code really returns wherever it honestly could
# (`default: true` on `ok`, `minimum` on `cleared_cycles`/`hops`, the `max_depth` bounds).
#
# This is coverage growth, not a licence to raise a ratchet. Monotonicity holds for a FIXED set of
# compared operations; widening the denominator may raise a count only with a named, per-entry
# demonstration that no old entry got worse and each new one reproduces a measured class.
# Reconciling the shared envelopes globally would lower ERROR_RESPONSE, and is a separate slice:
# it touches every operation and needs its own measurement.
TRANSPORT_HEADER_DRIFT_SHA256 = (
    "493a3f3d4477b5914d93645788bb8987e460116e66e19a641fb69eff821502a2"
)
TRANSPORT_HEADER_DRIFT_COUNT = 67
# 2026-08-23 / p011_t1102, slice 5: count unchanged at 13, digest moves. Describing
# TrustLine.policy touches the create and update REQUEST bodies too - the same node is
# declared on all three schemas, and leaving one of the three vague would have been a
# worse contract than moving this digest.
# 2026-08-23 / p011_t1102 (`F-011-10`): count holds at 13, digest moves. The trustline policy
# request bodies declared `max_hop_usage` and `daily_limit` as `nullable: true` beside a `oneOf`,
# which admits no null at all - and validate_trustline_policy explicitly accepts null there
# (app/utils/validation.py:210-211). They now carry an explicit null branch.
#
# The count holding needed one change to the comparator, recorded because it is a change of
# instrument rather than of contract. `_drop_inert_nullable` now discards a `nullable` with no
# sibling `type`, the same way `title` is discarded: it asserts nothing on either side. Without
# it the two `Optional[Any]` seed bodies would have drifted permanently, canon `seed: {}` against
# generated `seed: {nullable: true}` - a difference the canon cannot answer, because the guard
# added in this slice forbids the canon from writing the form at all while FastAPI keeps emitting
# it. Measured: with the normalization the pair matches and the count returns to 13.
REQUEST_SCHEMA_DRIFT_SHA256 = (
    "2fc89128f9b2146d7d6be86669fedecae68ac41b8ad7bb0257211cd52f268c24"
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
# 2026-08-23 / p011_t1102 (`F-011-1`): 71 -> 69. Ban and unban stop answering "some object" and
# declare the two keys `_set_participant_status` really emits (`app/api/v1/admin.py:860`).
#
# The mechanism is worth stating, because the first attempt got it wrong: describing the CANON
# alone does not remove an entry. Both operations were already in this dictionary, and stayed in
# it with a different canonical half, because their handlers carry no `response_model` and the
# generated side was still empty. An entry leaves only when BOTH sides state the same shape.
#
# So the application declares it too - through `responses={200: {"model": ...}}`, never
# `response_model=`. That distinction is the whole reason this is safe: `responses` documents,
# while `response_model` would make FastAPI filter the handler output down to the declared
# fields, which is a wire change and forbidden here. Verified in isolation on this FastAPI
# version: with `responses=` a key absent from the model still reaches the client, with
# `response_model=` it is silently dropped.
# 2026-08-23 / p011_t1102, slice 2: 69 -> 64. The five operations whose bodies the application
# already described and the canon did not - clearing/cycles, incidents, participants,
# participants/stats, runs/active. Transcribed into the canon by hand, in the style of that
# document; generating it from `app.openapi()` is forbidden by this program's own Non-goals and
# was tried and thrown away earlier in the same session.
# 2026-08-23 / p011_t1102, slice 4: count stays 64, digest moves. InvariantResult.details and
# PaymentError.details now state their variants instead of `additionalProperties: true`, which
# changes five entries - GET /integrity/status, POST /integrity/verify and the three payments
# operations - without adding or removing any. They stay in this dictionary because the canon got
# more precise than the generated schema, not less: the pydantic side is `Dict[str, Any]`, and
# tightening it there would change response validation on the money routes, which this program
# does not do.
# 2026-08-23 / p011_t1102, slice 5: still 64, digest moves again. TrustLine.policy declares the
# five keys validate_trustline_policy allows, and the two stable integrity nodes state their key
# sets. Same reason as slice 4 for the count holding: the canon is now more precise than the
# generated schema, whose pydantic side is Dict[str, Any].
# 2026-08-23 / p011_t1102, slice 7: still 64, digest moves. The two admin graph reads describe
# their bodies, including honest item schemas for the three collections the response model types
# as `list[Any]` and therefore does not shape at all.
# 2026-08-23 / p011_t1102, slice 8: 64 -> 63, and the one that leaves is GET /admin/audit-log.
# The five admin money reads all declare a real `response_model`, so describing them in the canon
# alone was never going to be enough - what was left over after the canon caught up was measured
# operation by operation:
#   GET /admin/audit-log                       LEAVES. Its last two differences were canon gaps in
#                                              AdminAuditLogItem: `id`/`actor_id` are UUID columns
#                                              typed as UUID in the model, and the canon declared
#                                              them as bare strings.
#   GET /admin/trustlines                      remain, all four for the same reason: they serve
#   GET /admin/trustlines/bottlenecks          TrustLine, whose `policy` the canon describes key by
#   GET /admin/liquidity/summary               key while the model says Dict[str, Any] (slice 5),
#   GET /admin/participants/{pid}/metrics      and whose `equivalent` the canon constrains by
#                                              pattern. The last one also declares its six
#                                              conditional metric blocks as nullable, which the
#                                              generated schema does not say at all.
# Four trustline reads and the two graph reads change without leaving: `TrustLine` gained
# `updated_at`, which every read emits and the canon never declared, and its `required` grew from
# six names to the ten the model declares without a default.
# 2026-08-23 / p011_t1102, slice 9 plus `F-011-7` and `F-011-10`: 63 -> 62. Three changes land
# together because they share this file and this document, and their effects were measured apart:
#   * `F-011-7`  GET /simulator/events/poll LEAVES. The canon promised an array of SimulatorEvent
#                and the handler is `# MVP: no replay buffer.` + `return []`. Both sides now say
#                `type: array, maxItems: 0` - the application through `responses=`, never
#                `response_model=`. This is the only operation that leaves.
#   * `F-011-10` 26 nodes wrote `nullable: true` with no sibling `type`, where it modifies nothing
#                and null is rejected. Content-only: no operation enters or leaves, but every
#                entry touching a fixed schema changes. Proved on a real body - InvariantResult
#                with `details: null` was INVALID against the canon before and is VALID after.
#   * `F-011-9`  the graph reads stop reusing AdminIncidentItem, whose `format: date-time` the
#                graph path does not honour, and get AdminGraphIncidentItem instead.
# 2026-08-23 / p011_t1102, slice 10: count holds at 62, digest moves. GET /integrity/audit-log was
# the last operation on the undescribed list, and describing it does not clear it from here: its
# three opaque leaves sit inside IntegrityAuditLogAfterState, whose pydantic side is
# Dict[str, Any], so the canon is now more precise than the generated schema rather than equal to
# it. Same reason slices 4 and 5 held their count.
# 2026-08-23 / p011_t1108: count holds at 62, digest moves, and this one was found by review
# rather than by the gate. Sixteen entries change content because eleven canon nodes now admit the
# null the service really sends there - twelve operations were returning 2xx bodies that
# api/openapi.yaml REJECTED, with this whole suite green, because nothing in the repository
# validated a response against the canon. `tests/contract/test_p011_responses_conform_to_the_canon.py`
# now does, over every body the suite produces.
#
# Two shapes did it. `nullable: true` beside an `enum` that has no `null` in it still rejects null,
# and the F-011-10 guard passed those because they do have a sibling `type`. And a property that
# can be null declared as a bare `$ref` or a bare typed schema has nothing to be wrong about -
# `Participant.profile` was invisible to both guards and to every fixture in the suite.
#
# The digest also absorbs the symmetric half of a normalization that already existed: the canon's
# only 3.0.3 spelling of "X or null" is a `oneOf` with an explicit null branch, and the generated
# side's 3.1 `anyOf: [X, null]` was already being collapsed. Comparing the two spellings recorded
# a difference that does not exist.
# 2026-08-23 / p011_t1108: 62 -> 63, and the growth is named, as the monotonicity invariant in the
# spec requires. GET /admin/config ENTERS. Nothing else moves.
#
# `AdminConfigItem.value` used to say `description: Any JSON value` and nothing else, and the
# undescribed-response guard passed it only because that guard's rule was "is the dict empty"
# rather than "is there a constraint here at all". Sharpening the guard (T1108) exposed it, and it
# turned out never to have been free form: `value` is `getattr(settings, key)` over the closed
# twelve-item literal in `_runtime_config_items`, measured on the real settings object as six
# booleans, five integers and one string. The canon now says that union.
#
# The generated side cannot follow, because the model field is `Any`. So this is the slice-4 and
# slice-5 situation again - the canon became more precise than the generated schema - except that
# here it adds an entry instead of changing one, because the two sides previously agreed on saying
# nothing. Tightening the pydantic field would change response validation on an admin read, which
# this programme does not do.
# Also 2026-08-23 / p011_t1108, same slice: count holds at 63, digest moves again. The two graph
# reads change content because AdminGraphIncidentItem and AdminGraphTransactionItem disagreed with
# each other about the same column - one declared `created_at` required and non-nullable, the
# other optional and nullable, both fed by helpers written the same way. The column is NOT NULL
# with a server default and both helpers always write the key, so the stricter one was right. The
# incident `state` also stopped being a bare string: the query filters to six of the nine
# transaction states.
SUCCESS_SCHEMA_DRIFT_SHA256 = (
    "d0c6cc29ecb4d645e54fd9aca7837132c7715531a52fa9db6978b8013fa1ace7"
)
SUCCESS_SCHEMA_DRIFT_COUNT = 63
# 2026-08-11 / T501: public DB health no longer declares exception details;
# the new admin diagnostic operation matches generated responses, so count stays 84.
# 2026-08-20 / p007_unblock_f0071: simulator metrics/bottlenecks declare 503 in the
# canonical contract (real mode refuses to substitute synthetic data). FastAPI does
# not know about it because the routes carry no `responses=`, so both operations —
# which already drifted for other reasons — gained a canonical-only 503; count stays 84.
# 2026-08-22 / p011_t1105 (`F-011-6`, inherited as `T716(а)` from 007): three operations
# declared 503 only in the canon, so a client generated from the application had no branch
# for a status the service returns. All three now declare it, and the measured delta is:
#   GET /simulator/runs/{run_id}/metrics      canonical-only 503 gone; entry REMAINS,
#                                             still drifting on 400/401/404 vs generated 422
#   GET /simulator/runs/{run_id}/bottlenecks  canonical-only 503 gone; entry REMAINS,
#                                             still drifting on 401/404 vs generated 422
#   GET /health/db                            503 was its ONLY difference -> entry REMOVED,
#                                             which is why the count moves 84 -> 83
# The count moving at all is the exception rather than the rule here: an operation leaves
# this dictionary only when its LAST difference is resolved.
# 2026-08-23 / p011_t1101: 83 -> 91, see the note above TRANSPORT_HEADER_DRIFT_SHA256.
# 2026-08-23 / p011_t1103a (`F-011-3`): 91 -> 83 in the same session, and the two moves are worth
# reading together. Publishing the eight raised this count by widening the denominator; declaring
# 429 and reconciling the shared envelopes then lowered it by 8, so the ledger ends where it began
# while covering eight more operations than it could see before.
#
# Two changes did the work:
#   - `429` is now declared on both sides for exactly the 95 operations the limiter can answer for
#     (derived from the route table, `_RATE_LIMIT_EXEMPT_PATHS` honoured), so those rows match
#     instead of drifting as generated-only.
#   - `ErrorEnvelope.error.details` and `SimulatorActionError.details` said
#     `additionalProperties: true` in the canon and omitted `nullable`. The models declare
#     `Optional[Dict[str, Any]] = None` (`app/schemas/common.py:15`), so the canon was simply
#     wrong about nullability and redundant about additionalProperties, which OpenAPI defaults to
#     true anyway. Corrected to what the code returns.
# 2026-08-23 / p011_t1106: 83 -> 53, the largest single move this ledger has made, and the first
# time it has moved because both documents were corrected at once rather than reconciled.
#
# The new guard, `tests/contract/test_p011_reachable_statuses_are_declared.py`, derives every
# status a route can answer from the route table and the dependency closure. It found 136
# reachable statuses api/openapi.yaml did not declare and 127 the application's own schema did not
# declare - 401 and 403 come out of GeoException subclasses raised inside dependencies, which
# `get_openapi` cannot see. Both sides were closed in one change, because closing either alone
# moves this digest across most of 78 operations and the ratchet would then be blessed twice over
# an unreadable diff.
#
# Thirty operations leave. None enters. The 53 that remain have two named causes and no others:
#
#   46  a canon-only 4xx that the generated document cannot learn - 404, 400, 409, 500, 504 raised
#       three or four frames deep in app/core/**. That is signal S6, which the T1106 survey
#       rejected as underivable: a rule loose enough to derive it would demand those statuses
#       across the whole surface and write into authority number one statuses the service never
#       returns. Closing these needs a per-operation reading, not a rule.
#   13  a generated-only 422 that is NOT reachable. FastAPI stamps 422 on any operation with any
#       flat parameter at all, and on these every parameter is string-like - nothing to coerce and
#       nothing that can be missing. The first version of this note said "the only parameter is an
#       optional X-Admin-Token"; that is true of nine and wrong about four, found by T1108.
#       GET /payments/{tx_id} has no admin header at all, only a required `str` path parameter;
#       /integrity/checksum/{equivalent} and /admin/equivalents/{code}/usage add a `str` path
#       parameter to the header; /admin/graph/snapshot adds two optional `str | None` query
#       parameters. The operative property is string-likeness - which is exactly the S1 exemption
#       the guard already implements - not the optionality of one header. Verified by execution on
#       /admin/config, /admin/whoami and /admin/migrations (403 for a wrong token, 200 for a valid
#       one, no input yielding 422) and by dumping `get_flat_params` for all thirteen. They are
#       left in place deliberately - the canon must not copy them, and suppressing them from the
#       generated document is a separate decision about what the application publishes.
#
# Three response components were added rather than bodies invented at 136 sites: Forbidden,
# UnprocessableEntity, and SimulatorIdentityUnprocessable. The last exists because ten simulator
# operations reach 422 with no falsifiable parameter at all - only a malformed X-Simulator-Owner -
# and describing those as "request validation failed" would have been a new false statement.
# 2026-08-23 / p011_t1109: count holds at 53, digest moves, and the decomposition above stops
# being false. Two changes, both closing findings from the final external review.
#
# The 401 body. `OAuth2PasswordBearer(auto_error=True)` answers inside the dependency solver,
# before the handlers that build ErrorEnvelope exist to run - so twenty operations really send a
# flat `{"detail": "Not authenticated"}` for any request whose Authorization SCHEME is not Bearer,
# and the envelope only once a Bearer token is present and rejected. The canon declared the
# envelope alone on nineteen of the twenty. Both documents now state the union through one shared
# component, so the ten of those already in this dictionary change content and none enters or
# leaves - and POST /payments, which had the honest union all along, converged onto the canon
# instead of moving.
#
# The phantom 401. POST /integrity/repair/net-mutual-debts and /cap-debts-to-trust-limits declared
# a 401 they cannot answer: both are guarded by require_admin alone, which has no Unauthorized
# branch, and executed with no header and with a wrong token both answer 403. That is this
# programme's defect facing the other way, and the reachability guard is one-directional by design
# so nothing caught it. Removed. Both operations stay in this dictionary on their generated-only
# 422 alone.
#
# Decomposition now, measured rather than asserted: of the 53, forty-four carry a canon-only 4xx
# raised deep in app/core/**, thirteen carry the non-reachable generated-only 422 FastAPI stamps on
# any operation with a flat parameter, four carry both, and NOTHING carries a third cause. The
# previous version of this note claimed two causes and no others while POST /payments 401 was a
# third; external review caught the claim, and closing the 401 body made it true.
ERROR_RESPONSE_DRIFT_SHA256 = (
    "17f0f6722b9b7ab900ebdde7a9e6ea25c58b282c01938cccfe69364cf7b68992"
)
ERROR_RESPONSE_DRIFT_COUNT = 53
# 2026-08-23 / p011_t1101: 59 -> 67, see the note above TRANSPORT_HEADER_DRIFT_SHA256.
# Missed by the first pass of this task: the error-response assert aborts before this one, so a
# run that stops there says nothing about security drift. Measured directly instead.
SECURITY_DRIFT_SHA256 = (
    "7b2c25ac469d081cae5e570eb5c82d1e25ed5731480d484669f4e50a1a32bb45"
)
SECURITY_DRIFT_COUNT = 67


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


def _drop_inert_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove a `nullable` that asserts nothing, the way `title` is removed.

    011/`F-011-10`: in OpenAPI 3.0.3 `nullable` modifies a sibling `type` and nothing else. On a
    schema with no `type` it permits nothing, so comparing it is comparing noise - and the two
    sides produce that noise for different reasons. `api/openapi.yaml` may not contain the form at
    all any more (`tests/contract/test_p011_nullable_needs_a_sibling_type.py` forbids it), while
    FastAPI still emits it for every `Optional[Any]` field, which no canon edit can answer.

    Dropping it is not a weakening: an untyped schema already admits null. Measured when
    introduced - with the canon fixed but this normalization absent, the two `Optional[Any]` seed
    bodies drifted forever with `seed: {}` against `seed: {nullable: true}`; with it they match
    and the count returns to where it was.
    """

    if schema.get("nullable") is True and "type" not in schema:
        return {key: value for key, value in schema.items() if key != "nullable"}
    return schema


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

    # 011/T1108: the canon's only way to say "X or null" in OpenAPI 3.0.3 is a `oneOf` with an
    # explicit null branch - `nullable` beside a composition asserts nothing, which is `F-011-10`,
    # and the guard forbids it. FastAPI emits the 3.1 spelling, which the `anyOf` branch below
    # already collapses to `{..., nullable: True}`. Collapsing the canon's spelling the same way is
    # the symmetric half: without it four properties drift on spelling alone while meaning exactly
    # the same thing, and the ledger records a difference that does not exist.
    one_of = value.get("oneOf")
    if len(value) == 1 and isinstance(one_of, list) and len(one_of) == 2:
        null_branches = [
            item for item in one_of if _resolve_ref(item, document).get("enum") == [None]
        ]
        others = [
            item for item in one_of if _resolve_ref(item, document).get("enum") != [None]
        ]
        if len(null_branches) == 1 and len(others) == 1:
            normalized_other = _normalize_schema(others[0], document, parameter=parameter)
            if parameter:
                return normalized_other
            if isinstance(normalized_other, dict) and "type" in normalized_other:
                return {**normalized_other, "nullable": True}

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
                if "type" in normalized:
                    return {**normalized, "nullable": True}
                if any(key in normalized for key in ("oneOf", "anyOf", "allOf")):
                    # 011/T1108: `nullable` beside a composition asserts nothing, so dropping it
                    # here would silently erase the nullability the `anyOf` carried - a canon that
                    # declares a composed field non-nullable would then compare EQUAL to a model
                    # that declares it Optional, which is the F-011-10 defect made invisible to
                    # the gate. State the null explicitly instead of losing it. No site reaches
                    # this branch today; it is here so that the first one to do so is measured
                    # rather than absorbed.
                    return {"oneOf": [normalized, {"enum": [None]}]}
                # A typeless, uncomposed schema already admits null, so there is nothing to keep.
                return normalized

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
    return _drop_inert_nullable(normalized)


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
        # 2026-08-23 / p011_t1103a: the twins now differ by exactly one status, and the
        # difference is real rather than drift. The versioned route is mounted under
        # `Depends(deps.rate_limit)` and can answer 429; the root route is declared with
        # @app.get, inherits no router dependencies, and cannot (`F-011-4`). T1103b decided on
        # 2026-08-23 to keep that asymmetry, so the declaration states it instead of hiding it.
        # Everything else about the twins must still match exactly.
        root_responses = _normalized_responses(root_operation, generated)
        versioned_responses = _normalized_responses(versioned_operation, generated)
        assert "429" not in root_responses, (
            f"{path} is outside the limiter but declares 429"
        )
        assert "429" in versioned_responses, (
            f"/api/v1{path} is rate limited but does not declare 429"
        )
        assert root_responses == {
            status: schema
            for status, schema in versioned_responses.items()
            if status != "429"
        }
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


def test_the_normalizer_drops_only_the_nullable_that_says_nothing() -> None:
    """Guard the comparator change made for `F-011-10`.

    `_drop_inert_nullable` removes a keyword from both sides before they are compared, so it can
    hide a real difference if it reaches too far. It must remove `nullable` only where OpenAPI
    3.0.3 gives it no meaning - beside no `type` at all - and must leave every effective one
    standing, at every depth.
    """

    document: dict[str, Any] = {}

    assert _normalize_schema({"nullable": True}, document) == {}
    assert _normalize_schema({"type": "string", "nullable": True}, document) == {
        "type": "string",
        "nullable": True,
    }, "a nullable with a sibling type is the whole point of the keyword and must survive"
    assert _normalize_schema({"type": "object", "nullable": False}, document) == {
        "type": "object",
        "nullable": False,
    }

    nested = _normalize_schema(
        {
            "type": "object",
            "properties": {
                "inert": {"nullable": True},
                "effective": {"type": "integer", "nullable": True},
            },
        },
        document,
    )
    assert nested["properties"] == {
        "inert": {},
        "effective": {"type": "integer", "nullable": True},
    }, "the rule must apply by depth, not only at the top of a schema"

    # And it must not make two genuinely different schemas compare equal.
    assert _normalize_schema({"type": "string"}, document) != _normalize_schema(
        {"type": "string", "nullable": True}, document
    )

    # The risky path is the one the first version of this test did not exercise: the `anyOf`
    # collapse, where the normalizer SYNTHESISES the nullability rather than reading it. If the
    # collapsed branch has no type of its own, dropping the keyword there would erase the fact
    # that the field can be null, and a canon declaring it non-nullable would compare equal to a
    # model declaring it Optional - `F-011-10` made invisible to the gate. Found by T1108.
    composed_nullable = _normalize_schema(
        {"anyOf": [{"oneOf": [{"type": "string"}, {"type": "integer"}]}, {"type": "null"}]},
        document,
    )
    composed_plain = _normalize_schema(
        {"oneOf": [{"type": "string"}, {"type": "integer"}]}, document
    )
    assert composed_nullable != composed_plain, (
        "a composed field that can be null must not normalize to the same thing as one that "
        "cannot"
    )
    assert {"enum": [None]} in composed_nullable["oneOf"]

    # But a typeless, uncomposed schema already admits null, so nothing is added there - this is
    # what keeps the two `Optional[Any]` request bodies matching instead of drifting forever.
    assert _normalize_schema({"anyOf": [{}, {"type": "null"}]}, document) == {}

    # The canon's 3.0.3 spelling of the same thing must collapse to the same normal form as the
    # generated side's 3.1 spelling - otherwise the canon is penalised for using the only wording
    # OpenAPI 3.0.3 allows, and the ledger records a difference that is not one.
    canon_spelling = _normalize_schema(
        {"oneOf": [{"type": "string"}, {"nullable": True, "enum": [None]}]}, document
    )
    generated_spelling = _normalize_schema(
        {"anyOf": [{"type": "string"}, {"type": "null"}]}, document
    )
    assert canon_spelling == generated_spelling == {"type": "string", "nullable": True}

    # And the collapse must not swallow a real two-branch union. `oneOf: [A, B]` with no null
    # branch, and a three-branch union that happens to include null, both stay as they are.
    union = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert _normalize_schema(dict(union), document) == union
    three = {
        "oneOf": [{"type": "string"}, {"type": "integer"}, {"nullable": True, "enum": [None]}]
    }
    assert _normalize_schema(dict(three), document) == {
        "oneOf": [{"type": "string"}, {"type": "integer"}, {"enum": [None]}]
    }, (
        "a union of more than one non-null branch must keep its branches; collapsing it would "
        "erase a real alternative. Only the inert `nullable` on the null branch itself is dropped"
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
