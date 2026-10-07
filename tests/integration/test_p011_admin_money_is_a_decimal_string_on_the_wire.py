"""Program 011: the five admin reads keep money as exact decimal text - and keep ratios numeric.

032 S5 (owner decision 2026-10-07): `/admin/trustlines/bottlenecks` was removed, and the liquidity summary and the
participant metrics were narrowed to their money fields, so every ratio and threshold this module once pinned as a
JSON number left the wire with them. What remains: the trustline list, the audit log, the summary's three totals and
the metrics' balance rows - each still exact decimal text. The history below is the module as written for 011.

Commit `02ee236` described the 200 bodies of `GET /admin/trustlines`, `/admin/audit-log`,
`/admin/trustlines/bottlenecks`, `/admin/liquidity/summary` and `/admin/participants/{pid}/metrics`
in `api/openapi.yaml`, field by field, and recorded for each one whether it is money-as-string,
atoms-as-string, or a genuine JSON `number`. Nothing executed those routes to check. AGENTS.md
section 9 says a canon claim is worth what a response proves, so this module calls all five and
reads the bodies an admin client actually receives.

Why a separate module from `test_p011_money_is_a_decimal_string_on_the_wire.py`, whose
`assert_exact_decimal_string` it imports rather than copies: that module's `money_scenario` builds
a healthy trustline (90% of the limit still free), and three of the five routes here return only
the edges that are nearly EXHAUSTED. Reusing that fixture would give `items: []` on the bottleneck
route and `top_bottleneck_edges: []` on the summary - every loop below would iterate nothing and
pass. The fixture here deliberately drives one line down to 5.2% headroom so those collections are
populated, which is a different scenario rather than a variation on the same one.

Three things this module asserts that the public-route module does not:

  * The canon's `number` claims are as falsifiable as its `string` claims. `threshold`, `share`,
    `pct`, `top1`, `top5`, `hhi` and `percentile` are declared `type: number`; a service that
    "tidied" them into strings would break every client that does arithmetic on them just as
    surely as a stringified amount turning numeric would break one that does arithmetic on money.
  * The check reads the RAW response text, not only the parsed value. `assert_raw_key_is_quoted`
    walks every occurrence of a key in `response.text` and requires the next character to be a
    quote, so a money field nested somewhere no parsed-value loop reaches - a new row type, a
    deeper `trustline` object - still has to be text. It is also the only check that sees what the
    client's parser saw before it turned `100.50000000` into the double 100.5.
  * `TrustLine.updated_at`, which `02ee236` added to `required` for the first time. It was absent
    from `properties` altogether before that commit, so nothing anywhere proved it reaches a
    client.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.db.models.equivalent import Equivalent
from tests.integration.test_p011_money_is_a_decimal_string_on_the_wire import (
    _WHY,
    assert_exact_decimal_string,
)
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)
from tests.conftest import MODE_B

# The near-exhausted edge. 5.25 / 100.50 = 5.22%, comfortably under THRESHOLD, so
# /admin/trustlines/bottlenecks, summary.top_bottleneck_edges and metrics.capacity.bottlenecks all
# return it. Both figures carry two decimal places so `min_scale=2` can catch a float round-trip.
BOTTLENECK_LIMIT = "100.50"
BOTTLENECK_PAYMENT = "95.25"

# The healthy edge, in the opposite direction, so the metrics subject is a creditor as well as a
# debtor and `counterparty.debtors` / `capacity.out` are not empty.
HEALTHY_LIMIT = "60.00"
HEALTHY_PAYMENT = "12.75"

# Sent as text on purpose: the query parameter is parsed into a Decimal, and passing "0.10" rather
# than 0.1 keeps the request side free of the float this module is about.
THRESHOLD = "0.10"

# Derived once, so changing a constant above cannot silently invalidate an expectation below.
EXPECTED_TOTAL_LIMIT = Decimal(BOTTLENECK_LIMIT) + Decimal(HEALTHY_LIMIT)
EXPECTED_TOTAL_USED = Decimal(BOTTLENECK_PAYMENT) + Decimal(HEALTHY_PAYMENT)
EXPECTED_TOTAL_AVAILABLE = EXPECTED_TOTAL_LIMIT - EXPECTED_TOTAL_USED

_NUMBER_WHY = (
    "WHY THIS MATTERS: api/openapi.yaml declares this field `type: number`. A canon that says "
    "`number` has to be as falsifiable as one that says `string`, or 'we described it' means only "
    "that somebody wrote it down. Clients generated from this schema hand the value straight to "
    "arithmetic; a string there is a TypeError in their code, not a rounding nuisance. If this "
    "fails, establish whether the implementation or the canon is wrong - do not relax it."
)

_ADMIN_HEADERS = {"X-Admin-Token": settings.ADMIN_TOKEN}


# --------------------------------------------------------------------------------------------
# Wire-level checkers. `assert_exact_decimal_string` is imported; these are the ones this module
# adds, and `test_the_admin_wire_checkers_reject_what_they_exist_to_catch` proves each can fail.
# --------------------------------------------------------------------------------------------


def assert_json_number(value: Any, *, where: str, expected: float | None = None) -> None:
    """Assert a parsed JSON value is a number - the mirror image of the money checker."""

    # bool before int: isinstance(True, int) is True, and `true` under `share` is its own bug.
    assert not isinstance(value, bool), (
        f"{where} is the JSON literal {str(value).lower()}, not a number.\n{_NUMBER_WHY}"
    )
    assert not isinstance(value, str), (
        f"{where} reached the wire as a JSON STRING ({value!r}). The canon declares it a number; "
        f"one of the two has moved.\n{_NUMBER_WHY}"
    )
    assert isinstance(value, (int, float)), (
        f"{where} is {type(value).__name__} ({value!r}).\n{_NUMBER_WHY}"
    )
    if expected is not None:
        assert float(value) == pytest.approx(expected), (
            f"{where} is {value!r}, expected {expected!r}.\n{_NUMBER_WHY}"
        )


def assert_raw_key_is_quoted(
    raw: str, key: str, *, where: str, occurrences: int | None = None
) -> None:
    """Every `"key":` in the raw response text must be followed by a quoted value.

    The parsed-value loops elsewhere in this module visit the fields they were written to visit;
    this walks the bytes instead. `occurrences` is not decoration: without it a route that stopped
    emitting a field in half its rows would still satisfy "every occurrence I found was quoted".
    """

    found = re.findall(rf'"{re.escape(key)}"\s*:\s*(.)', raw)
    assert found, (
        f"{where}: the raw response text contains no {key!r} key at all, so this check inspected "
        f"nothing. Either the route stopped emitting it or the field was renamed."
    )
    if occurrences is not None:
        assert len(found) == occurrences, (
            f"{where}: expected {occurrences} occurrence(s) of {key!r} in the raw body, found "
            f"{len(found)}. The fixture and the expectation have drifted apart, so a green run "
            f"here would not mean what it claims."
        )
    for index, first_char in enumerate(found):
        assert first_char == '"', (
            f"{where}: occurrence {index} of {key!r} in the RAW response text is followed by "
            f"{first_char!r}, not a quote - the value is a bare JSON number.\n{_WHY}"
        )


def assert_raw_key_is_unquoted(raw: str, key: str, *, where: str) -> None:
    """The mirror: every `"key":` in the raw text must be followed by something other than a quote."""

    found = re.findall(rf'"{re.escape(key)}"\s*:\s*(.)', raw)
    assert found, (
        f"{where}: the raw response text contains no {key!r} key at all, so this check inspected "
        f"nothing."
    )
    for index, first_char in enumerate(found):
        assert first_char != '"', (
            f"{where}: occurrence {index} of {key!r} in the RAW response text is followed by a "
            f"quote - the canon declares it a number.\n{_NUMBER_WHY}"
        )


def float_leaves(value: Any, *, path: str = "") -> list[str]:
    """Paths of every JSON float inside a parsed structure. Ints are left alone - they are exact."""

    if isinstance(value, dict):
        return [
            leaf for key, item in value.items() for leaf in float_leaves(item, path=f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            leaf
            for index, item in enumerate(value)
            for leaf in float_leaves(item, path=f"{path}[{index}]")
        ]
    if isinstance(value, float) and not isinstance(value, bool):
        return [f"{path} = {value!r}"]
    return []


# --------------------------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------------------------


async def _seed_equivalent(db_session, code: str = "USD") -> None:
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    if result.scalar_one_or_none() is None:
        db_session.add(Equivalent(code=code, description=code, precision=2))
        await db_session.commit()


async def _open_trustline(client: AsyncClient, creditor: dict, debtor_pid: str, limit: str) -> dict:
    key = SigningKey(base64.b64decode(creditor["priv"]))
    response = await client.post(
        "/api/v1/trustlines",
        headers=creditor["headers"],
        json={
            "to": debtor_pid,
            "equivalent": "USD",
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=key, to_pid=debtor_pid, equivalent="USD", limit=limit
            ),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _pay(client: AsyncClient, payer: dict, payee_pid: str, amount: str) -> dict:
    key = SigningKey(base64.b64decode(payer["priv"]))
    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=payer["headers"],
        json={
            "tx_id": tx_id,
            "to": payee_pid,
            "equivalent": "USD",
            "amount": amount,
            "signature": _sign_payment_request(
                signing_key=key,
                tx_id=tx_id,
                from_pid=payer["pid"],
                to_pid=payee_pid,
                equivalent="USD",
                amount=amount,
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Without this every admin assertion downstream would read zeros and pass vacuously.
    assert body["status"] == "COMMITTED", f"setup payment did not commit: {body!r}"
    return body


@pytest_asyncio.fixture
async def admin_money_scenario(client: AsyncClient, db_session) -> dict:
    """Three participants, two trustlines, two committed payments - one edge near exhaustion.

    Direction is easy to get backwards: a trustline `from -> to` is creditor -> debtor, so the
    payment that consumes a line runs the other way. Alice paying Bob is what fills Bob's outgoing
    line to Alice.

    Resulting net positions (credit minus debt), which several assertions below name explicitly:
      Bob   +95.25   creditor only
      Alice -82.50   owes Bob 95.25, is owed 12.75 by Carol
      Carol -12.75   debtor only
    """

    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_P011_Admin")
    bob = await register_and_login(client, "Bob_P011_Admin")
    carol = await register_and_login(client, "Carol_P011_Admin")

    bottleneck = await _open_trustline(client, bob, alice["pid"], BOTTLENECK_LIMIT)
    await _pay(client, alice, bob["pid"], BOTTLENECK_PAYMENT)

    healthy = await _open_trustline(client, alice, carol["pid"], HEALTHY_LIMIT)
    await _pay(client, carol, alice["pid"], HEALTHY_PAYMENT)

    # The whole point of the scenario: without this the three bottleneck collections come back
    # empty and every loop over them proves nothing.
    headroom = (Decimal(BOTTLENECK_LIMIT) - Decimal(BOTTLENECK_PAYMENT)) / Decimal(BOTTLENECK_LIMIT)
    assert headroom < Decimal(THRESHOLD), (
        f"the 'bottleneck' edge has {headroom} headroom, which is not below the {THRESHOLD} "
        f"threshold this module queries with. Every bottleneck assertion would inspect an empty "
        f"list. Fix the constants before trusting a green run."
    )

    return {
        "alice": alice,
        "bob": bob,
        "carol": carol,
        "bottleneck_trustline_id": bottleneck["id"],
        "healthy_trustline_id": healthy["id"],
    }


# --------------------------------------------------------------------------------------------
# Shared shape assertions
# --------------------------------------------------------------------------------------------


def _assert_trustline_money(item: dict, *, where: str) -> None:
    for field in ("limit", "used", "available"):
        assert_exact_decimal_string(item[field], where=f"{where}.{field}", min_scale=2)

    assert Decimal(item["limit"]) - Decimal(item["used"]) == Decimal(item["available"]), (
        f"{where}: available must equal limit - used exactly. Needing a tolerance here would mean "
        f"a float had entered the path.\n{_WHY}"
    )


def _assert_trustline_updated_at(item: dict, *, where: str) -> None:
    """`updated_at` entered `required` in 02ee236; before that it was not even in `properties`.

    Presence is the claim under test, so a null or a missing key is the failure. The parse is here
    because `format: date-time` is part of the same claim, and a bare `str` check would let an
    empty string through.
    """

    assert "updated_at" in item, (
        f"{where} has no 'updated_at'. api/openapi.yaml lists it in TrustLine.required, so a "
        f"generated client treats its absence as a schema violation. Keys: {sorted(item)}"
    )
    assert item["updated_at"] is not None, f"{where}.updated_at is null, but the canon requires it."
    assert isinstance(item["updated_at"], str), (
        f"{where}.updated_at is {type(item['updated_at']).__name__}, not the declared string."
    )
    datetime.fromisoformat(str(item["updated_at"]).replace("Z", "+00:00"))


# --------------------------------------------------------------------------------------------
# Guard the guards
# --------------------------------------------------------------------------------------------


def test_the_admin_wire_checkers_reject_what_they_exist_to_catch() -> None:
    """Prove the three checkers added here can fail, before any green run below is trusted.

    `assert_exact_decimal_string` is not re-proved: the module it is imported from does that, and
    a second copy of that proof would only give the two something to drift apart on.
    """

    # Positive controls first. A checker that rejects everything would make every test in this
    # module meaningless, which is the failure mode this pairing exists to rule out.
    assert_json_number(0.1, where="control", expected=0.1)
    assert_json_number(0, where="control")
    assert_raw_key_is_quoted('{"limit":"100.50000000"}', "limit", where="control", occurrences=1)
    assert_raw_key_is_quoted('{"a":{"net":"-1.00"},"b":[{"net":"2.00"}]}', "net", where="control")
    assert_raw_key_is_unquoted('{"threshold":0.1}', "threshold", where="control")
    assert float_leaves({"a": 1, "b": "2", "c": True, "d": None, "e": [{"f": 3}]}) == []

    # A number that became a string is the regression the canon's `number` claims can suffer.
    for bad in ("0.1", "", True, False, None, [], {}):
        with pytest.raises(AssertionError):
            assert_json_number(bad, where="regressed")
    with pytest.raises(AssertionError):
        assert_json_number(0.2, where="regressed", expected=0.1)

    # The raw-text checkers must react to the exact byte shapes a serializer change produces,
    # including one bad occurrence hidden among good ones - the case a parsed-value loop that only
    # visits the top level would miss entirely.
    for raw in (
        '{"limit":100.5}',
        '{"limit": 100.50000000}',
        '{"ok":{"limit":"1.00"},"bad":{"limit":1.00}}',
    ):
        with pytest.raises(AssertionError):
            assert_raw_key_is_quoted(raw, "limit", where="regressed")
    with pytest.raises(AssertionError):
        assert_raw_key_is_unquoted('{"threshold":"0.10"}', "threshold", where="regressed")

    # A key that is simply absent must fail rather than pass vacuously: that is how a renamed or
    # dropped field would otherwise turn into a silent green.
    for checker in (assert_raw_key_is_quoted, assert_raw_key_is_unquoted):
        with pytest.raises(AssertionError):
            checker('{"other":1}', "limit", where="absent")
    with pytest.raises(AssertionError):
        assert_raw_key_is_quoted('{"limit":"1.00"}', "limit", where="miscounted", occurrences=2)

    # And the float scanner must find money that a free-form blob smuggled out as a double.
    assert float_leaves({"after_state": {"limit": 100.5}}) == [".after_state.limit = 100.5"]
    assert float_leaves([{"rows": [{"net": -1.25}]}]) == ["[0].rows[0].net = -1.25"]


# --------------------------------------------------------------------------------------------
# GET /admin/trustlines
# --------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_admin_trustlines_list_money_is_decimal_text(
    client: AsyncClient, admin_money_scenario
) -> None:
    """Every TrustLine in the admin list keeps limit/used/available as exact decimal text."""

    response = await client.get("/api/v1/admin/trustlines", headers=_ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 2 and len(body["items"]) == 2, (
        f"the fixture opened exactly two trustlines; GET /admin/trustlines reported "
        f"{body['total']} with {len(body['items'])} item(s). An empty or partial list satisfies "
        f"every money assertion below vacuously, so this is a setup failure rather than a pass."
    )
    for index, item in enumerate(body["items"]):
        _assert_trustline_money(item, where=f"GET /admin/trustlines items[{index}]")
        _assert_trustline_updated_at(item, where=f"GET /admin/trustlines items[{index}]")

    # Two items x three money fields, all quoted in the bytes the client received.
    for key in ("limit", "used", "available"):
        assert_raw_key_is_quoted(
            response.text, key, where="GET /admin/trustlines (raw)", occurrences=2
        )

    # The envelope the canon declares alongside the items: these are counts, so a string here
    # would be as wrong as a number on a money field.
    for key in ("page", "per_page", "total"):
        assert isinstance(body[key], int) and not isinstance(body[key], bool), (
            f"GET /admin/trustlines .{key} is {body[key]!r}; the canon declares type: integer."
        )


# --------------------------------------------------------------------------------------------
# GET /admin/audit-log
# --------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_admin_audit_log_declares_no_money_and_leaks_none(
    client: AsyncClient, admin_money_scenario
) -> None:
    """The one body of the five with no money field - so the claim is that it stays that way.

    `AdminAuditLogItem` declares no amount of any kind, and `before_state` / `after_state` are
    deliberately open (ACCEPTED_FREE_FORM). Open is exactly where an amount can appear without
    anyone editing a schema, and `float` is the shape it would take: the only way a Decimal
    reaches an untyped `dict[str, Any]` column as a number is somebody calling `float(...)` on it.
    So this walks both blobs and requires no float anywhere. It also pins the canon's global note
    about these five routes - none of them sets `response_model_exclude_none`, so every optional
    field is present-and-null rather than absent.
    """

    # An admin mutation, so the log holds a row with a real actor action and a populated
    # before/after pair, not only the auth.login rows the fixture's three logins leave behind.
    freeze = await client.post(
        f"/api/v1/admin/participants/{admin_money_scenario['carol']['pid']}/freeze",
        headers=_ADMIN_HEADERS,
        json={"reason": "p011-wire-test"},
    )
    assert freeze.status_code == 200, freeze.text

    response = await client.get(
        "/api/v1/admin/audit-log", headers=_ADMIN_HEADERS, params={"per_page": 200}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    items = body["items"]
    assert items, (
        "GET /admin/audit-log returned no items, so every per-item assertion below would be "
        "skipped. Three logins and one admin freeze happened in this test; an empty log is a "
        "setup failure rather than a pass."
    )
    mutations = [item for item in items if item["action"] == "admin.participants.freeze"]
    assert len(mutations) == 1, (
        f"the freeze row is not in the log, so the before_state/after_state scan would only ever "
        f"see nulls; actions present: {sorted({item['action'] for item in items})}"
    )

    for index, item in enumerate(items):
        where = f"GET /admin/audit-log items[{index}]"
        # required: [id, timestamp, action] - plus every nullable field, because nothing on this
        # route excludes unset or none.
        for key in (
            "id",
            "timestamp",
            "action",
            "actor_id",
            "actor_role",
            "object_type",
            "object_id",
            "reason",
            "before_state",
            "after_state",
            "request_id",
            "ip_address",
            "user_agent",
        ):
            assert key in item, (
                f"{where} is missing {key!r}. The canon documents this body as emitting every "
                f"declared field, null included; keys: {sorted(item)}"
            )

        leaks = float_leaves(item["before_state"], path=f"{where}.before_state") + float_leaves(
            item["after_state"], path=f"{where}.after_state"
        )
        assert not leaks, (
            f"a JSON float reached the audit log's free-form state: {leaks}. Those two objects are "
            f"open by design, which is precisely why an amount can arrive there without any schema "
            f"edit - and a float amount is a lossy amount.\n{_WHY}"
        )

    # The operator's own text and the object it names travel back unchanged. Every type in the
    # row can be correct while the record is still useless.
    assert mutations[0]["reason"] == "p011-wire-test"
    assert mutations[0]["object_id"] == admin_money_scenario["carol"]["pid"]
    assert mutations[0]["before_state"] == {"status": "active"}
    assert mutations[0]["after_state"] == {"status": "suspended"}


# --------------------------------------------------------------------------------------------
# GET /admin/liquidity/summary
# --------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_admin_liquidity_summary_money_is_decimal_text(
    client: AsyncClient, admin_money_scenario
) -> None:
    """The three totals stay exact decimal text (narrowed to six fields by 032 S5, F-2).

    The expected amounts are spelled out rather than recomputed from the response, because a
    summary that sums its own output consistently and wrongly would still agree with itself.
    """

    response = await client.get(
        "/api/v1/admin/liquidity/summary",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    for field, expected in (
        ("total_limit", EXPECTED_TOTAL_LIMIT),
        ("total_used", EXPECTED_TOTAL_USED),
        ("total_available", EXPECTED_TOTAL_AVAILABLE),
    ):
        assert_exact_decimal_string(
            body[field],
            where=f"GET /admin/liquidity/summary .{field}",
            expected=str(expected),
            min_scale=2,
        )
    assert Decimal(body["total_limit"]) - Decimal(body["total_used"]) == Decimal(
        body["total_available"]
    ), f"the summary totals do not reconcile exactly.\n{_WHY}"

    count = body["active_trustlines"]
    assert isinstance(count, int) and not isinstance(count, bool), (
        f"GET /admin/liquidity/summary .active_trustlines is {count!r}; the canon declares integer."
    )

    for key in ("total_limit", "total_used", "total_available"):
        assert_raw_key_is_quoted(
            response.text, key, where="GET /admin/liquidity/summary (raw)", occurrences=1
        )


# --------------------------------------------------------------------------------------------
# GET /admin/participants/{pid}/metrics
# --------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_admin_participant_metrics_money_is_decimal_text(
    client: AsyncClient, admin_money_scenario
) -> None:
    """Every amount of the balance rows (the only block left after 032 S5, F-1).

    Alice is the subject because she sits on both ends of the graph: debtor on the exhausted line,
    creditor on the healthy one, so none of the seven amounts below is a trivial zero.
    """

    alice = admin_money_scenario["alice"]
    response = await client.get(
        f"/api/v1/admin/participants/{alice['pid']}/metrics",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    rows = body["balance_rows"]
    assert len(rows) == 1, (
        f"expected one balance row for the single seeded equivalent, got {len(rows)}; an empty "
        f"list would skip all seven money assertions below."
    )
    row = rows[0]
    expected_row = {
        "outgoing_limit": Decimal(HEALTHY_LIMIT),
        "outgoing_used": Decimal(HEALTHY_PAYMENT),
        "incoming_limit": Decimal(BOTTLENECK_LIMIT),
        "incoming_used": Decimal(BOTTLENECK_PAYMENT),
        "total_debt": Decimal(BOTTLENECK_PAYMENT),
        "total_credit": Decimal(HEALTHY_PAYMENT),
        "net": Decimal(HEALTHY_PAYMENT) - Decimal(BOTTLENECK_PAYMENT),
    }
    for field, expected in expected_row.items():
        assert_exact_decimal_string(
            row[field],
            where=f"metrics .balance_rows[0].{field}",
            expected=str(expected),
            min_scale=2,
        )

    # Raw text sweep over every money key on this body.
    for key in (
        "outgoing_limit",
        "outgoing_used",
        "incoming_limit",
        "incoming_used",
        "total_debt",
        "total_credit",
        "net",
    ):
        assert_raw_key_is_quoted(response.text, key, where="metrics (raw)", occurrences=1)


# --------------------------------------------------------------------------------------------
# TrustLine.updated_at
# --------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_trustline_updated_at_reaches_the_wire_on_every_admin_route_that_serves_one(
    client: AsyncClient, admin_money_scenario
) -> None:
    """`updated_at` became required in 02ee236; until now nothing proved any route emits it.

    Four places served a TrustLine across these reads, each building the object a different way. Since 032 S5
    (F-1, F-2) the bottleneck helper and the metrics capacity block are gone, and `GET /admin/trustlines` is the
    one admin read here that serves a TrustLine; the graph reads have their own tests.
    """

    listed = await client.get("/api/v1/admin/trustlines", headers=_ADMIN_HEADERS)
    responses = (("/admin/trustlines", listed),)
    for label, response in responses:
        assert response.status_code == 200, f"{label}: {response.text}"

    served = {
        "/admin/trustlines items": listed.json()["items"],
    }
    for where, trustlines in served.items():
        assert trustlines, (
            f"{where} served no trustline, so this route contributed nothing to the check. The "
            f"fixture populates it; an empty one is a setup failure."
        )
        for index, trustline in enumerate(trustlines):
            _assert_trustline_updated_at(trustline, where=f"{where}[{index}]")
            # The rest of TrustLine.required, checked here because `updated_at` was not the only
            # thing 02ee236 corrected - `required` had named six of the ten fields.
            for key in (
                "id",
                "from",
                "to",
                "equivalent",
                "limit",
                "used",
                "available",
                "status",
                "created_at",
            ):
                assert key in trustline, (
                    f"{where}[{index}] is missing the required key {key!r}; keys: "
                    f"{sorted(trustline)}"
                )
            # Serialization aliases: the Python attribute names must never surface.
            for internal in ("from_pid", "to_pid", "equivalent_code", "from_"):
                assert internal not in trustline, (
                    f"{where}[{index}] emitted the internal name {internal!r}, which renames a "
                    f"public field for every client."
                )

    # And once against the bytes, so a route that emitted `"updated_at":null` - which satisfies
    # "the key is present" on a parsed dict - fails here as well.
    for label, response in responses:
        assert_raw_key_is_quoted(response.text, "updated_at", where=f"{label} (raw)")
