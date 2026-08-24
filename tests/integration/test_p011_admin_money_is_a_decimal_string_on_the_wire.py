"""Program 011: the five admin reads keep money as exact decimal text - and keep ratios numeric.

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
# GET /admin/trustlines/bottlenecks
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_bottlenecks_money_is_decimal_text_and_threshold_is_a_number(
    client: AsyncClient, admin_money_scenario
) -> None:
    """The near-exhausted edge comes back with text money, under a numeric threshold."""

    response = await client.get(
        "/api/v1/admin/trustlines/bottlenecks",
        headers=_ADMIN_HEADERS,
        params={"threshold": THRESHOLD, "equivalent": "USD"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    items = body["items"]
    assert len(items) == 1, (
        f"expected exactly the one near-exhausted edge, got {len(items)}. Zero would make every "
        f"assertion in this test vacuous; more than one means the fixture changed and the "
        f"identity assertion below no longer pins what it names."
    )
    edge = items[0]
    assert edge["id"] == admin_money_scenario["bottleneck_trustline_id"], (
        "the returned edge is not the line the fixture drove to 5.2% headroom"
    )

    _assert_trustline_money(edge, where="GET /admin/trustlines/bottlenecks items[0]")
    _assert_trustline_updated_at(edge, where="GET /admin/trustlines/bottlenecks items[0]")
    assert_exact_decimal_string(
        edge["limit"], where="bottlenecks items[0].limit", expected=BOTTLENECK_LIMIT, min_scale=2
    )
    assert_exact_decimal_string(
        edge["used"], where="bottlenecks items[0].used", expected=BOTTLENECK_PAYMENT, min_scale=2
    )

    assert_json_number(
        body["threshold"], where="GET /admin/trustlines/bottlenecks .threshold", expected=0.10
    )

    for key in ("limit", "used", "available"):
        assert_raw_key_is_quoted(
            response.text, key, where="GET /admin/trustlines/bottlenecks (raw)", occurrences=1
        )
    assert_raw_key_is_unquoted(
        response.text, "threshold", where="GET /admin/trustlines/bottlenecks (raw)"
    )


# --------------------------------------------------------------------------------------------
# GET /admin/liquidity/summary
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_liquidity_summary_money_is_decimal_text(
    client: AsyncClient, admin_money_scenario
) -> None:
    """Totals, every ranked `net`, and the embedded trustlines all stay exact decimal text.

    The expected amounts are spelled out rather than recomputed from the response, because a
    summary that sums its own output consistently and wrongly would still agree with itself.
    """

    alice = admin_money_scenario["alice"]
    bob = admin_money_scenario["bob"]
    carol = admin_money_scenario["carol"]

    response = await client.get(
        "/api/v1/admin/liquidity/summary",
        headers=_ADMIN_HEADERS,
        params={"threshold": THRESHOLD, "equivalent": "USD"},
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

    # Net positions, keyed by pid rather than by index: the ordering is a separate claim, and
    # pinning it here would make an unrelated sort change look like a money regression.
    expected_nets = {
        bob["pid"]: Decimal(BOTTLENECK_PAYMENT),
        alice["pid"]: Decimal(HEALTHY_PAYMENT) - Decimal(BOTTLENECK_PAYMENT),
        carol["pid"]: -Decimal(HEALTHY_PAYMENT),
    }
    for list_name, expected_length in (
        ("top_creditors", 1),
        ("top_debtors", 2),
        ("top_by_abs_net", 3),
    ):
        rows = body[list_name]
        assert len(rows) == expected_length, (
            f"GET /admin/liquidity/summary .{list_name} has {len(rows)} row(s), expected "
            f"{expected_length}. The per-row money assertions run inside this loop, so a short "
            f"list quietly reduces what this test proves."
        )
        for index, row in enumerate(rows):
            where = f"GET /admin/liquidity/summary .{list_name}[{index}]"
            assert_exact_decimal_string(row["net"], where=f"{where}.net", min_scale=2)
            assert row["pid"] in expected_nets, f"{where}.pid is not one of the fixture's three"
            assert Decimal(row["net"]) == expected_nets[row["pid"]], (
                f"{where}.net is {row['net']!r}, expected {expected_nets[row['pid']]}"
            )

    edges = body["top_bottleneck_edges"]
    assert len(edges) == 1, (
        f"expected the one near-exhausted edge in top_bottleneck_edges, got {len(edges)}; at zero "
        f"the trustline money assertions below would never run."
    )
    _assert_trustline_money(edges[0], where="GET /admin/liquidity/summary .top_bottleneck_edges[0]")
    _assert_trustline_updated_at(
        edges[0], where="GET /admin/liquidity/summary .top_bottleneck_edges[0]"
    )

    assert_json_number(
        body["threshold"], where="GET /admin/liquidity/summary .threshold", expected=0.10
    )
    for key in ("active_trustlines", "bottlenecks", "incidents_over_sla"):
        assert isinstance(body[key], int) and not isinstance(body[key], bool), (
            f"GET /admin/liquidity/summary .{key} is {body[key]!r}; the canon declares integer."
        )

    # Raw text: three totals and one embedded edge, plus six `net` values across the three ranked
    # lists (1 + 2 + 3), and the one numeric threshold.
    for key in ("total_limit", "total_used", "total_available", "limit", "used", "available"):
        assert_raw_key_is_quoted(
            response.text, key, where="GET /admin/liquidity/summary (raw)", occurrences=1
        )
    assert_raw_key_is_quoted(
        response.text, "net", where="GET /admin/liquidity/summary (raw)", occurrences=6
    )
    assert_raw_key_is_unquoted(
        response.text, "threshold", where="GET /admin/liquidity/summary (raw)"
    )


# --------------------------------------------------------------------------------------------
# GET /admin/participants/{pid}/metrics
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_participant_metrics_money_is_decimal_text(
    client: AsyncClient, admin_money_scenario
) -> None:
    """Every amount on the widest body of the five - and the atoms that are deliberately not amounts.

    Alice is the subject because she sits on both ends of the graph: debtor on the exhausted line,
    creditor on the healthy one. With a one-sided participant `counterparty.debtors`,
    `capacity.out` and half the concentration figures would be empty or zero, and the loops over
    them would prove nothing.
    """

    alice = admin_money_scenario["alice"]
    response = await client.get(
        f"/api/v1/admin/participants/{alice['pid']}/metrics",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD", "threshold": THRESHOLD},
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

    counterparty = body["counterparty"]
    # totalDebt / totalCredit are camelCase on the wire via serialization_alias; the snake_case
    # Python names must not surface, or the money moves to a key no client is reading.
    for internal in ("total_debt", "total_credit"):
        assert internal not in counterparty, (
            f"metrics .counterparty emitted {internal!r}; the canon documents the camelCase alias."
        )
    for field in ("totalDebt", "totalCredit"):
        assert_exact_decimal_string(
            counterparty[field], where=f"metrics .counterparty.{field}", min_scale=2
        )
    for side, expected_amount in (
        ("creditors", Decimal(BOTTLENECK_PAYMENT)),
        ("debtors", Decimal(HEALTHY_PAYMENT)),
    ):
        side_rows = counterparty[side]
        assert len(side_rows) == 1, (
            f"metrics .counterparty.{side} has {len(side_rows)} row(s); the fixture gives Alice "
            f"exactly one counterparty on each side, and an empty list would skip the assertion."
        )
        assert_exact_decimal_string(
            side_rows[0]["amount"],
            where=f"metrics .counterparty.{side}[0].amount",
            expected=str(expected_amount),
            min_scale=2,
        )

    assert_exact_decimal_string(body["rank"]["net"], where="metrics .rank.net", min_scale=2)
    # The canon warns that rank.net has been through atoms and back, so it may differ from
    # balance_rows[0].net below the equivalent's precision. At precision 2 with two-decimal
    # fixtures there is nothing below the precision to lose, so they must still agree exactly.
    assert Decimal(body["rank"]["net"]) == expected_row["net"], (
        f"metrics .rank.net is {body['rank']['net']!r} but .balance_rows[0].net is {row['net']!r}; "
        f"the canon says the atoms round-trip only costs sub-precision digits, and this fixture "
        f"has none."
    )

    capacity = body["capacity"]
    for side, expected_limit, expected_used in (
        ("out", Decimal(HEALTHY_LIMIT), Decimal(HEALTHY_PAYMENT)),
        ("inc", Decimal(BOTTLENECK_LIMIT), Decimal(BOTTLENECK_PAYMENT)),
    ):
        assert_exact_decimal_string(
            capacity[side]["limit"],
            where=f"metrics .capacity.{side}.limit",
            expected=str(expected_limit),
            min_scale=2,
        )
        assert_exact_decimal_string(
            capacity[side]["used"],
            where=f"metrics .capacity.{side}.used",
            expected=str(expected_used),
            min_scale=2,
        )

    bottlenecks = capacity["bottlenecks"]
    assert len(bottlenecks) == 1, (
        f"metrics .capacity.bottlenecks has {len(bottlenecks)} entries. The canon warns this list "
        f"is ALWAYS empty unless ?threshold= is supplied - this request supplies it, so an empty "
        f"list is either a regression or a request that lost its parameter, not a pass."
    )
    _assert_trustline_money(
        bottlenecks[0]["trustline"], where="metrics .capacity.bottlenecks[0].trustline"
    )
    _assert_trustline_updated_at(
        bottlenecks[0]["trustline"], where="metrics .capacity.bottlenecks[0].trustline"
    )

    # Atoms are strings too, but for the opposite reason: they are integers, not amounts, and the
    # canon says so explicitly. A decimal point here would mean somebody had started treating them
    # as money.
    distribution = body["distribution"]
    bins = distribution["bins"]
    assert bins, "metrics .distribution.bins is empty, so the atom assertions below would not run."
    atoms = [
        ("min_atoms", distribution["min_atoms"]),
        ("max_atoms", distribution["max_atoms"]),
        *[
            (f"bins[{i}].{k}", b[k])
            for i, b in enumerate(bins)
            for k in ("from_atoms", "to_atoms")
        ],
    ]
    for label, value in atoms:
        assert isinstance(value, str) and re.fullmatch(r"-?\d+", value), (
            f"metrics .distribution.{label} is {value!r}. The canon declares integer atoms as a "
            f"string via str(int): no decimal point, no exponent."
        )

    # Raw text sweep over every money-or-atoms key on this body, at whatever depth it appears.
    for key in (
        "outgoing_limit",
        "outgoing_used",
        "incoming_limit",
        "incoming_used",
        "total_debt",
        "total_credit",
        "net",
        "totalDebt",
        "totalCredit",
        "amount",
        "limit",
        "used",
        "available",
        "min_atoms",
        "max_atoms",
        "from_atoms",
        "to_atoms",
    ):
        assert_raw_key_is_quoted(response.text, key, where="metrics (raw)")


# --------------------------------------------------------------------------------------------
# The canon's `number` claims, and the split it records
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_ratio_fields_are_json_numbers_not_strings(
    client: AsyncClient, admin_money_scenario
) -> None:
    """`share`, `pct`, `top1`, `top5`, `hhi`, `percentile` are ratios, so the canon says number.

    Every one of them is derived from money by `float(Decimal / Decimal)` - the operation
    AGENTS.md section 8 forbids on money itself. That makes the boundary between the two the
    interesting thing, and a boundary has to be checked from both sides: otherwise a well-meaning
    cleanup moves a ratio into the money column, or an amount into the ratio one, and no test in
    the suite objects.
    """

    alice = admin_money_scenario["alice"]
    response = await client.get(
        f"/api/v1/admin/participants/{alice['pid']}/metrics",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD", "threshold": THRESHOLD},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    for side in ("creditors", "debtors"):
        rows = body["counterparty"][side]
        assert rows, f"metrics .counterparty.{side} is empty, so no `share` would be checked."
        for index, row in enumerate(rows):
            assert_json_number(row["share"], where=f"metrics .counterparty.{side}[{index}].share")

    for side in ("outgoing", "incoming"):
        for field in ("top1", "top5", "hhi"):
            assert_json_number(
                body["concentration"][side][field], where=f"metrics .concentration.{side}.{field}"
            )

    for side in ("out", "inc"):
        assert_json_number(body["capacity"][side]["pct"], where=f"metrics .capacity.{side}.pct")
    # Anti-vacuum: `pct` is 0.0 whenever the limit is 0, and 0.0 satisfies the type check while
    # saying nothing about a real ratio. The exhausted side must be near 1.
    assert body["capacity"]["inc"]["pct"] > 0.9, (
        f"metrics .capacity.inc.pct is {body['capacity']['inc']['pct']!r}; the fixture consumed "
        f"94.8% of that line, so a near-zero value means the ratio was never computed."
    )

    assert_json_number(body["rank"]["percentile"], where="metrics .rank.percentile")

    for key in ("share", "top1", "top5", "hhi", "pct", "percentile"):
        assert_raw_key_is_unquoted(response.text, key, where="metrics (raw)")


@pytest.mark.asyncio
async def test_one_threshold_parameter_comes_back_as_a_number_twice_and_a_string_once(
    client: AsyncClient, admin_money_scenario
) -> None:
    """An INCONSISTENCY the canon records rather than hides - pinned so it cannot drift in silence.

    The same `?threshold=0.10` is echoed three ways:
      * /admin/trustlines/bottlenecks     -> JSON number, via float(Decimal(...))
      * /admin/liquidity/summary          -> JSON number, the same conversion
      * /admin/participants/{pid}/metrics -> the decimal STRING "0.10", inside `meta`, because the
        handler puts the raw Decimal into a dict[str, Any] and pydantic infers the type per value.

    This is a ratio, not money, so AGENTS.md section 8 does not force either shape, and nothing
    here argues the split is right. The point is that api/openapi.yaml documents all three, and a
    canon is only honest for as long as the wire still agrees with it.

    IF YOU NORMALISE THIS - and normalising it would be a defensible thing to do - this test is
    where you will find out, and the canon has to move in the same commit. Do not delete the test
    to make the change go green: rewrite it to pin whatever the new single shape is.
    """

    alice = admin_money_scenario["alice"]

    bottlenecks = await client.get(
        "/api/v1/admin/trustlines/bottlenecks",
        headers=_ADMIN_HEADERS,
        params={"threshold": THRESHOLD},
    )
    summary = await client.get(
        "/api/v1/admin/liquidity/summary", headers=_ADMIN_HEADERS, params={"threshold": THRESHOLD}
    )
    metrics = await client.get(
        f"/api/v1/admin/participants/{alice['pid']}/metrics",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD", "threshold": THRESHOLD},
    )
    for label, response in (
        ("bottlenecks", bottlenecks),
        ("summary", summary),
        ("metrics", metrics),
    ):
        assert response.status_code == 200, f"{label}: {response.text}"

    # Half one: a number on the two aggregate routes. Note what the float conversion costs even
    # here - "0.10" comes back as 0.1, so the caller cannot tell from the response what scale they
    # sent. Harmless for a ratio; it is exactly the loss that would be unacceptable on an amount.
    assert_json_number(
        bottlenecks.json()["threshold"],
        where="/admin/trustlines/bottlenecks .threshold",
        expected=0.10,
    )
    assert_json_number(
        summary.json()["threshold"], where="/admin/liquidity/summary .threshold", expected=0.10
    )
    assert_raw_key_is_unquoted(
        bottlenecks.text, "threshold", where="/admin/trustlines/bottlenecks (raw)"
    )
    assert_raw_key_is_unquoted(summary.text, "threshold", where="/admin/liquidity/summary (raw)")

    # Half two: a decimal string on metrics, which is the only one of the three that preserves the
    # scale the caller sent.
    meta = metrics.json()["meta"]
    assert "threshold" in meta, (
        "metrics .meta has no 'threshold' key. The canon says the key is absent only when "
        "`equivalent` was omitted - this request supplied it, so its absence is a real change."
    )
    assert_exact_decimal_string(
        meta["threshold"],
        where="/admin/participants/{pid}/metrics .meta.threshold",
        expected=THRESHOLD,
    )
    assert meta["threshold"] == THRESHOLD, (
        f"metrics .meta.threshold is {meta['threshold']!r}, not the literal {THRESHOLD!r} that was "
        f"sent. Round-tripping the caller's scale is the whole substance of the difference between "
        f"this route and the other two."
    )
    assert_raw_key_is_quoted(
        metrics.text, "threshold", where="/admin/participants/{pid}/metrics (raw)", occurrences=1
    )

    # Stated once, plainly, so the divergence is itself an assertion rather than something a
    # reader has to infer from three assertions that happen to share a function.
    shapes = {
        "bottlenecks": type(bottlenecks.json()["threshold"]).__name__,
        "summary": type(summary.json()["threshold"]).__name__,
        "metrics.meta": type(meta["threshold"]).__name__,
    }
    assert shapes == {"bottlenecks": "float", "summary": "float", "metrics.meta": "str"}, (
        f"the threshold shapes are now {shapes}. api/openapi.yaml records this exact three-way "
        f"split (AdminTrustLinesBottlenecksResponse.threshold, "
        f"AdminLiquiditySummaryResponse.threshold, AdminParticipantMetricsResponse.meta.threshold). "
        f"If the service has been made consistent, the canon must be corrected in the same commit."
    )


@pytest.mark.asyncio
async def test_trustline_updated_at_reaches_the_wire_on_every_admin_route_that_serves_one(
    client: AsyncClient, admin_money_scenario
) -> None:
    """`updated_at` became required in 02ee236; until now nothing proved any route emits it.

    Four places serve a TrustLine across these five reads, and each builds the object a different
    way - the trustline service, the shared bottleneck helper, and the metrics module's
    hand-assembled dict. Only a per-route check shows that all four remembered the field.
    """

    alice = admin_money_scenario["alice"]

    listed = await client.get("/api/v1/admin/trustlines", headers=_ADMIN_HEADERS)
    bottlenecks = await client.get(
        "/api/v1/admin/trustlines/bottlenecks",
        headers=_ADMIN_HEADERS,
        params={"threshold": THRESHOLD},
    )
    summary = await client.get(
        "/api/v1/admin/liquidity/summary", headers=_ADMIN_HEADERS, params={"threshold": THRESHOLD}
    )
    metrics = await client.get(
        f"/api/v1/admin/participants/{alice['pid']}/metrics",
        headers=_ADMIN_HEADERS,
        params={"equivalent": "USD", "threshold": THRESHOLD},
    )
    responses = (
        ("/admin/trustlines", listed),
        ("/admin/trustlines/bottlenecks", bottlenecks),
        ("/admin/liquidity/summary", summary),
        ("/admin/participants/{pid}/metrics", metrics),
    )
    for label, response in responses:
        assert response.status_code == 200, f"{label}: {response.text}"

    served = {
        "/admin/trustlines items": listed.json()["items"],
        "/admin/trustlines/bottlenecks items": bottlenecks.json()["items"],
        "/admin/liquidity/summary top_bottleneck_edges": summary.json()["top_bottleneck_edges"],
        "/admin/participants/{pid}/metrics capacity.bottlenecks": [
            entry["trustline"] for entry in metrics.json()["capacity"]["bottlenecks"]
        ],
    }
    for where, trustlines in served.items():
        assert trustlines, (
            f"{where} served no trustline, so this route contributed nothing to the check. The "
            f"fixture populates all four; an empty one is a setup failure."
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
