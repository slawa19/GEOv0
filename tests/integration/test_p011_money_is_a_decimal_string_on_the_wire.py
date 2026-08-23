"""Program 011: money leaves this service as an exact decimal string, on trustlines as on payments.

Required by external review BEFORE the `/payments` and `/trustlines` bodies are described in
`api/openapi.yaml`, because the two families do not rest on the same footing.

`app/schemas/payment.py` annotates `amount: str` - the type is the contract. `app/schemas/trustline.py`
annotates `limit`, `used` and `available` as `Decimal`. Those reach the wire as JSON strings only
because pydantic v2 serializes `Decimal` to a string in json mode. That is library behaviour, not a
declared type: a serializer config change, a pydantic upgrade, or a hand-rolled `model_dump()` would
turn `"100.50"` into `100.5` without touching a single annotation, and nothing in the suite would
notice. AGENTS.md section 8 requires the opposite - money stays exact decimal text, and floats never
enter domain values.

So every assertion below reads the RAW JSON the client receives. A model-level test would keep
passing while the wire broke, which is the whole failure mode.

Two anti-vacuum axes, because a green run here must mean something (AGENTS.md section 9):
  * the checker itself is proved able to fail, against the exact shapes a regression produces;
  * an empty `items: []` satisfies "every money field is a string" while exercising nothing, so
    every payload assertion is preceded by a non-emptiness assertion, and the trustline case
    requires `used > 0` - true only if the payment leg really moved debt.

Existing coverage deliberately not duplicated, and one thing it does not do: three tests already
sum route amounts with `float(...)` - `tests/integration/test_payments_multipath.py:138`,
`tests/integration/test_admin_routing_max_paths.py:136`, `tests/integration/test_scenarios.py:556`.
All three would pass unchanged if the wire became numbers, which is precisely why this module reads
types rather than values.
"""

from __future__ import annotations

import base64
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

# Chosen so a float round-trip is observable: 100.50 -> float -> "100.5" loses declared scale.
TRUSTLINE_LIMIT = "100.50"
PAYMENT_AMOUNT = "10.25"

# Plain decimal text only. Rejects "1E-8", "NaN", "Infinity", "+1" - every one a shape a
# float-based serializer can emit while the value is still, technically, a str.
_PLAIN_DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?$")

_WHY = (
    "WHY THIS MATTERS (AGENTS.md section 8): a JSON number is an IEEE-754 double in every "
    "mainstream client. It cannot represent 0.1 exactly, it silently drops declared scale "
    "('100.50' -> 100.5), and once a client has parsed it the exact amount is unrecoverable. "
    "On trustlines the contract is especially fragile: limit/used/available are declared Decimal, "
    "not str, so they are strings only by pydantic's json-mode behaviour. If this test failed, "
    "establish whether the implementation or the expectation is wrong - do not relax it."
)


def assert_exact_decimal_string(
    value: Any, *, where: str, expected: str | None = None, min_scale: int | None = None
) -> None:
    """Assert a raw JSON value is exact decimal text - not a number, not a float artefact."""

    # bool first: isinstance(True, int) is True, and `true` on a money field earns its own message.
    assert not isinstance(value, bool), (
        f"{where} is the JSON literal {str(value).lower()}, not a decimal string.\n{_WHY}"
    )
    assert not isinstance(value, (int, float)), (
        f"{where} reached the wire as a JSON NUMBER ({value!r}). This is the exact regression "
        f"this module exists to catch.\n{_WHY}"
    )
    assert isinstance(value, str), f"{where} is {type(value).__name__} ({value!r}).\n{_WHY}"
    assert _PLAIN_DECIMAL_TEXT.match(value), (
        f"{where} is {value!r}, which is not plain decimal text. Exponent notation, 'NaN' and "
        f"'Infinity' are the fingerprints of a value that went through a float.\n{_WHY}"
    )

    try:
        parsed: Decimal | None = Decimal(value)
    except InvalidOperation:
        parsed = None
    assert parsed is not None, f"{where} is {value!r}, which Decimal() cannot parse.\n{_WHY}"
    assert str(parsed) == value, (
        f"{where} is {value!r} but its canonical decimal form is {str(parsed)!r}; the wire value "
        f"is a formatting artefact rather than the stored decimal.\n{_WHY}"
    )

    if min_scale is not None:
        scale = -parsed.as_tuple().exponent
        assert scale >= min_scale, (
            f"{where} is {value!r}: {scale} decimal place(s), at least {min_scale} were stored. "
            f"Losing trailing zeros is what float serialization looks like when the result is "
            f"still a string - str(float(Decimal('100.50'))) == '100.5'. An isinstance check "
            f"alone would not catch it.\n{_WHY}"
        )

    if expected is not None:
        assert parsed == Decimal(expected), f"{where} is {value!r}, expected {expected!r}.\n{_WHY}"


async def _seed_equivalent(db_session, code: str = "USD") -> None:
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == code))
    if result.scalar_one_or_none() is None:
        db_session.add(Equivalent(code=code, description=code, precision=2))
        await db_session.commit()


@pytest_asyncio.fixture
async def money_scenario(client: AsyncClient, db_session) -> dict:
    """One committed payment over one trustline, built from nothing.

    Direction is easy to get backwards: a trustline `from -> to` is creditor -> debtor, so a
    payment Alice -> Bob rides the trustline Bob -> Alice, and `used` shows on Bob's outgoing line.
    """

    await _seed_equivalent(db_session, "USD")

    alice = await register_and_login(client, "Alice_P011_Money")
    bob = await register_and_login(client, "Bob_P011_Money")

    bob_key = SigningKey(base64.b64decode(bob["priv"]))
    response = await client.post(
        "/api/v1/trustlines",
        headers=bob["headers"],
        json={
            "to": alice["pid"],
            "equivalent": "USD",
            "limit": TRUSTLINE_LIMIT,
            "signature": _sign_trustline_create_request(
                signing_key=bob_key,
                to_pid=alice["pid"],
                equivalent="USD",
                limit=TRUSTLINE_LIMIT,
            ),
        },
    )
    assert response.status_code == 201, response.text
    trustline_created = response.json()

    alice_key = SigningKey(base64.b64decode(alice["priv"]))
    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=alice["headers"],
        json={
            "tx_id": tx_id,
            "to": bob["pid"],
            "equivalent": "USD",
            "amount": PAYMENT_AMOUNT,
            "signature": _sign_payment_request(
                signing_key=alice_key,
                tx_id=tx_id,
                from_pid=alice["pid"],
                to_pid=bob["pid"],
                equivalent="USD",
                amount=PAYMENT_AMOUNT,
            ),
        },
    )
    assert response.status_code == 200, response.text
    payment_created = response.json()
    # Without this, every assertion downstream would inspect an error envelope and pass.
    assert payment_created["status"] == "COMMITTED", (
        f"setup did not commit, so the wire assertions would be vacuous: {payment_created!r}"
    )

    return {
        "alice": alice,
        "bob": bob,
        "tx_id": tx_id,
        "trustline_id": trustline_created["id"],
        "trustline_created": trustline_created,
        "payment_created": payment_created,
    }


class _MoneyAsDeclaredToday(BaseModel):
    limit: Decimal
    used: Decimal
    available: Decimal


class _MoneyAfterTheRegression(BaseModel):
    """The same fields after the break this module prevents.

    Declaring them `float` reproduces in one line what a serializer-config change or a pydantic
    upgrade that stops stringifying Decimal would produce. No field NAME changes - which is
    exactly why no existing test would notice.
    """

    limit: float
    used: float
    available: float


def test_the_checker_itself_rejects_float_serialized_money() -> None:
    """Guard the guard: prove the checker can fail, before trusting it anywhere else."""

    money = {"limit": Decimal("100.50"), "used": Decimal("10.25"), "available": Decimal("90.25")}

    # Positive control: today's declaration really does yield strings, so the checker is not
    # simply rejecting everything - which would make every assertion below meaningless.
    for field, value in _MoneyAsDeclaredToday(**money).model_dump(mode="json").items():
        assert_exact_decimal_string(value, where=f"declared-today.{field}", min_scale=2)

    regressed = _MoneyAfterTheRegression(**money).model_dump(mode="json")
    assert regressed["limit"] == 100.5, (
        "the float twin no longer emits a JSON number, so this negative control has stopped "
        "modelling the regression and must be rewritten rather than deleted"
    )
    for field, value in regressed.items():
        with pytest.raises(AssertionError):
            assert_exact_decimal_string(value, where=f"regressed.{field}", min_scale=2)

    # Shapes that stay `str` and would slip past an isinstance-only test.
    for bad, min_scale in (
        (str(float(Decimal("100.50"))), 2),  # '100.5' - scale silently dropped
        ("1E-8", None),
        ("nan", None),
        ("Infinity", None),
        ("", None),
    ):
        with pytest.raises(AssertionError):
            assert_exact_decimal_string(bad, where="hand-serialized", min_scale=min_scale)

    for bad in (True, False, 100.5, 100):
        with pytest.raises(AssertionError):
            assert_exact_decimal_string(bad, where="non-string literal")


def _assert_trustline_wire(item: dict, *, where: str, from_pid: str, to_pid: str) -> None:
    for key in ("from", "to", "equivalent", "limit", "used", "available", "status", "id"):
        assert key in item, f"{where} is missing the wire key {key!r}; keys: {sorted(item)}"

    # The Python attribute names must never surface: the schema carries serialization aliases,
    # and a response emitted without them would rename three public fields at once.
    for internal in ("from_pid", "to_pid", "equivalent_code", "from_"):
        assert internal not in item, (
            f"{where} emitted the internal name {internal!r}; that is a breaking wire change "
            f"for every client."
        )

    assert item["from"] == from_pid, f"{where}: 'from' must be the creditor PID"
    assert item["to"] == to_pid, f"{where}: 'to' must be the debtor PID"
    assert item["equivalent"] == "USD", f"{where}: 'equivalent' must be the code"


def _assert_trustline_money(item: dict, *, where: str) -> None:
    assert_exact_decimal_string(
        item["limit"], where=f"{where}.limit", expected=TRUSTLINE_LIMIT, min_scale=2
    )
    assert_exact_decimal_string(
        item["used"], where=f"{where}.used", expected=PAYMENT_AMOUNT, min_scale=2
    )
    assert_exact_decimal_string(item["available"], where=f"{where}.available", min_scale=2)

    # Anti-vacuum: a zero `used` would mean the payment never moved debt through this line, and
    # the three assertions above would have inspected defaults rather than computed amounts.
    assert Decimal(item["used"]) > 0, (
        f"{where}.used is zero, so the money path was never exercised and this test checks "
        f"nothing. Fix the scenario before trusting a green run."
    )
    assert Decimal(item["limit"]) - Decimal(item["used"]) == Decimal(item["available"]), (
        f"{where}: available must equal limit - used exactly.\n{_WHY}"
    )


@pytest.mark.asyncio
async def test_trustline_list_money_is_decimal_text(client: AsyncClient, money_scenario) -> None:
    bob, alice = money_scenario["bob"], money_scenario["alice"]

    response = await client.get(
        "/api/v1/trustlines",
        headers=bob["headers"],
        params={"direction": "outgoing", "status": "active"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]

    assert items, (
        "GET /trustlines returned no items. An empty list satisfies every money assertion "
        "vacuously; the fixture created exactly one active outgoing line, so this is a setup "
        "failure rather than a pass."
    )
    _assert_trustline_wire(
        items[0], where="GET /trustlines items[0]", from_pid=bob["pid"], to_pid=alice["pid"]
    )
    _assert_trustline_money(items[0], where="GET /trustlines items[0]")


@pytest.mark.asyncio
async def test_trustline_by_id_money_is_decimal_text(client: AsyncClient, money_scenario) -> None:
    bob, alice = money_scenario["bob"], money_scenario["alice"]

    response = await client.get(
        f"/api/v1/trustlines/{money_scenario['trustline_id']}", headers=bob["headers"]
    )
    assert response.status_code == 200, response.text
    item = response.json()

    _assert_trustline_wire(
        item, where="GET /trustlines/{id}", from_pid=bob["pid"], to_pid=alice["pid"]
    )
    _assert_trustline_money(item, where="GET /trustlines/{id}")


@pytest.mark.asyncio
async def test_trustline_create_money_is_decimal_text(money_scenario) -> None:
    """The 201 body is the same schema and the same fragility, one route earlier.

    `min_scale` is deliberately not asserted: on a fresh line `used` is Decimal('0') computed in
    Python, so '0' is correct and demanding trailing zeros would pin an accident.
    """

    item = money_scenario["trustline_created"]
    for field in ("limit", "used", "available"):
        assert_exact_decimal_string(item[field], where=f"POST /trustlines .{field}")
    assert Decimal(item["limit"]) == Decimal(TRUSTLINE_LIMIT)


def _assert_payment_wire(item: dict, *, where: str, from_pid: str, to_pid: str) -> None:
    for key in ("tx_id", "status", "from", "to", "equivalent", "amount", "created_at"):
        assert key in item, f"{where} is missing the wire key {key!r}; keys: {sorted(item)}"
    assert "from_" not in item, (
        f"{where} emitted 'from_'; the trailing underscore is a Python keyword workaround and "
        f"must never reach a client."
    )
    assert item["from"] == from_pid and item["to"] == to_pid

    assert_exact_decimal_string(
        item["amount"], where=f"{where}.amount", expected=PAYMENT_AMOUNT, min_scale=2
    )

    routes = item.get("routes")
    assert routes, (
        f"{where}.routes is empty or absent, so the per-route assertions would inspect nothing. "
        f"A committed payment always carries at least one route."
    )
    total = Decimal("0")
    for index, route in enumerate(routes):
        assert route.get("path"), f"{where}.routes[{index}].path is empty"
        assert_exact_decimal_string(route["amount"], where=f"{where}.routes[{index}].amount")
        total += Decimal(route["amount"])
    assert total == Decimal(PAYMENT_AMOUNT), (
        f"{where}: route amounts sum to {total}, expected exactly {PAYMENT_AMOUNT}. If this ever "
        f"needs a tolerance, a float has entered the path.\n{_WHY}"
    )


@pytest.mark.asyncio
async def test_payment_create_money_is_decimal_text(money_scenario) -> None:
    _assert_payment_wire(
        money_scenario["payment_created"],
        where="POST /payments",
        from_pid=money_scenario["alice"]["pid"],
        to_pid=money_scenario["bob"]["pid"],
    )


@pytest.mark.asyncio
async def test_payment_by_tx_id_money_is_decimal_text(client: AsyncClient, money_scenario) -> None:
    alice = money_scenario["alice"]

    response = await client.get(
        f"/api/v1/payments/{money_scenario['tx_id']}", headers=alice["headers"]
    )
    assert response.status_code == 200, response.text
    item = response.json()

    assert item["tx_id"] == money_scenario["tx_id"]
    _assert_payment_wire(
        item,
        where="GET /payments/{tx_id}",
        from_pid=alice["pid"],
        to_pid=money_scenario["bob"]["pid"],
    )


@pytest.mark.asyncio
async def test_payment_list_money_is_decimal_text(client: AsyncClient, money_scenario) -> None:
    alice = money_scenario["alice"]

    response = await client.get(
        "/api/v1/payments", headers=alice["headers"], params={"direction": "sent"}
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]

    assert items, (
        "GET /payments returned no items; the fixture committed exactly one sent payment, so an "
        "empty list is a setup failure rather than a pass."
    )
    matching = [item for item in items if item["tx_id"] == money_scenario["tx_id"]]
    assert len(matching) == 1, f"the committed payment is not in the list response: {items!r}"

    _assert_payment_wire(
        matching[0],
        where="GET /payments items[0]",
        from_pid=alice["pid"],
        to_pid=money_scenario["bob"]["pid"],
    )
