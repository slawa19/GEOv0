"""035 (review of 034 S4b): the money storability rule answers for EVERY finite decimal, whatever its size.

`money_storability_violation` (`app/utils/validation.py`) asked `abs(value) >= 10**max_integer_digits`. `abs()` is an
operation of the decimal context: for a finite value whose exponent is past the context's `Emax` (999999 by default)
it does not return - it raises `decimal.Overflow`. `Decimal("1E1000000")` is such a value: finite, positive, and the
one value this predicate exists to call `money_magnitude`.

WHERE A REAL INPUT REACHES IT (inventory of 2026-10-09):

* the simulator, which builds `Decimal(str(...))` out of scenario text and asks the rule directly - an initial line of
  `add_participant` and a `create_trustline` effect (`inject_executor.py`, `unstorable_limit`), and a trust line of the
  scenario being seeded (`real_scenario_seeder.py`). These RAISED; the tests below are red on `906cae90`.
* NOT the HTTP money doors. `parse_money_amount` refuses exponent notation, more than 50 digits and more than twelve
  integer digits on the STRING, before any `Decimal` exists, so a payment amount or a trust-line limit like these
  never reaches the rule. The two HTTP tests below were green before the fix and record that.

The fix reads the magnitude off the number's own digits and exponent (`adjusted()`), which no context can change.
For every value the old form could answer, the answer is the same - held by the table below, which also runs the
rule under a deliberately narrow context.

What this does not see: the fraction half of the rule (`quantize`), which still runs in the caller's context - it
has no input that fails under the application's contexts, and is recorded, not changed.
"""

from __future__ import annotations

import decimal
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.db.models.equivalent import Equivalent
from app.db.models.trustline import TrustLine
from app.utils.validation import (
    MONEY_FINITENESS,
    MONEY_MAGNITUDE,
    MONEY_QUANTIZATION,
    is_storable_money,
    money_storability_violation,
)
from tests.conftest import MODE_B
from tests.integration.test_p030_s3b_simulator_through_the_services_postgres import _inject, _world
from tests.integration.test_p034_s4b_a_bad_initial_line_does_not_split_the_participant_postgres import _add
from tests.integration.test_scenarios import register_and_login

#: Finite values no `Numeric(20, 8)` can hold, each past what the default decimal context can even negate.
_BEYOND_THE_CONTEXT = ["1E1000000", "-1E1000000", "9.99E1000000", "1E999999999999999999"]

#: value -> the reason, for values on both sides of every boundary of `Numeric(20, 8)`. Each row is the answer the
#: rule gave on `906cae90` (measured there, 2026-10-09) and must give still.
_BOUNDARIES = [
    ("0", None),
    ("-0", None),
    ("0E1000000", None),            # zero, whatever its exponent, is zero
    ("0E-1000000", None),
    ("0.00000001", None),           # the smallest storable fraction
    ("1E-8", None),
    ("0.000000001", MONEY_QUANTIZATION),
    ("1E-9", MONEY_QUANTIZATION),
    ("1E-1000000", MONEY_QUANTIZATION),
    ("0.123456789", MONEY_QUANTIZATION),
    ("0.1000000000", None),         # trailing zeros past the scale are not a fraction
    ("999999999999", None),
    ("999999999999.99999999", None),   # the largest storable value
    ("-999999999999.99999999", None),
    ("999999999999.999999999", MONEY_QUANTIZATION),
    ("1000000000000", MONEY_MAGNITUDE),   # 10**12
    ("1E12", MONEY_MAGNITUDE),
    ("-1E12", MONEY_MAGNITUDE),
    ("1E11", None),
    ("1000000000000.000000001", MONEY_MAGNITUDE),   # magnitude is asked before the fraction
    ("9" * 400, MONEY_MAGNITUDE),
    ("1E999999", MONEY_MAGNITUDE),     # the largest exponent the default context can still negate
    ("NaN", MONEY_FINITENESS),
    ("sNaN", MONEY_FINITENESS),
    ("Infinity", MONEY_FINITENESS),
    ("-Infinity", MONEY_FINITENESS),
]

#: THE ONE CLASS WHOSE REASON CHANGED NAME, measured 2026-10-09 against `906cae90`: a value below 10**12 written with
#: more than 28 significant digits. The default context has 28, so `abs()` ROUNDED it up to 10**12 and the old form
#: called it too large; it is not - it has a fraction the column would round away. Refused before and after
#: (`is_storable_money` is False both times); only the name of the refusal moved, from `money_magnitude` to
#: `money_quantization`. With 28 digits or fewer nothing changed (the second row).
_RENAMED = [
    ("999999999999." + "9" * 17, MONEY_QUANTIZATION),   # 29 significant digits: was `money_magnitude`
    ("999999999999." + "9" * 16, MONEY_QUANTIZATION),   # 28: `money_quantization` before too
    ("-999999999999." + "9" * 20, MONEY_QUANTIZATION),  # was `money_magnitude`
]


@pytest.mark.parametrize("text", _BEYOND_THE_CONTEXT)
def test_a_finite_value_past_the_decimal_context_is_a_magnitude_violation_not_an_exception(text: str) -> None:
    value = Decimal(text)
    assert value.is_finite()

    assert money_storability_violation(value) == MONEY_MAGNITUDE
    assert money_storability_violation(text) == MONEY_MAGNITUDE  # the same, given as text
    assert is_storable_money(value) is False
    # The storage boundaries pass their column's own capacity; the answer does not depend on which was asked.
    assert money_storability_violation(value, max_integer_digits=12, max_scale=8) == MONEY_MAGNITUDE


@pytest.mark.parametrize("text, reason", _BOUNDARIES)
def test_every_boundary_of_the_column_answers_as_before(text: str, reason) -> None:
    assert money_storability_violation(Decimal(text)) == reason


@pytest.mark.parametrize("text, reason", _RENAMED)
def test_a_value_just_under_the_bound_with_a_long_fraction_is_refused_for_its_fraction(text: str, reason) -> None:
    value = Decimal(text)
    assert abs(value.as_tuple().exponent) > 8 and value.copy_abs() < Decimal(10) ** 12  # compared, not computed
    assert money_storability_violation(value) == reason
    assert is_storable_money(value) is False


@pytest.mark.parametrize("text, reason", [row for row in _BOUNDARIES if row[1] in (None, MONEY_MAGNITUDE)
                                          and "E-" not in row[0] and len(row[0].split(".")[-1]) <= 8
                                          or row[1] == MONEY_FINITENESS])
def test_the_magnitude_answer_does_not_depend_on_the_callers_context(text: str, reason) -> None:
    """Under a context of six digits `abs(Decimal("999999999999.99999999"))` ROUNDS to 1.00000E+12, so the old form
    called the largest storable value too large. The magnitude is a fact about the number, not about the context.

    Only rows whose answer the magnitude decides are taken: the fraction half still uses the caller's context."""

    with decimal.localcontext(decimal.Context(prec=6, Emax=20, Emin=-20)):
        assert money_storability_violation(Decimal(text)) == reason


def test_the_rule_can_tell_too_large_from_storable() -> None:
    """Anti-vacuum for the table: a rule that answered `None` to everything, or `money_magnitude` to everything, would
    pass half of it. Both answers are reachable on neighbouring values."""

    assert money_storability_violation(Decimal("999999999999.99999999")) is None
    assert money_storability_violation(Decimal("1000000000000")) == MONEY_MAGNITUDE
    assert money_storability_violation(Decimal("1E1000000")) == MONEY_MAGNITUDE
    assert money_storability_violation(Decimal("0E1000000")) is None


# ------------------------------------------------------------------------------------- the simulator's entries


@pytest.mark.parametrize("status,expected", [("frozen", "suspended"), ("deleted", "deleted")])
@pytest.mark.parametrize("bad", ["1E1000000", "-1E1000000"])
@pytest.mark.asyncio
async def test_an_initial_limit_past_the_context_does_not_split_the_participant(db_session, status, expected, bad) -> None:
    """The finding: the rule raised inside `add_participant` after the participant was inserted `active` and before
    its declared status was set; the effect's general handler swallowed it and the event committed."""

    got = await _add(db_session, status=status, limits=["10", bad])
    assert got["fired"] == {0}, got
    assert (got["stored"], got["in_scenario"], got["lines"]) == (expected, [expected], 1), (
        f"scenario status {status!r}, initial limits ['10', {bad!r}]: the database says {got['stored']!r}, the run's "
        f"scenario {got['in_scenario']}, lines {got['lines']} ({got['stats']})")
    if bad.startswith("-"):
        return  # a negative limit is dropped before the storability door, as every non-positive one is
    assert got["stats"]["skipped_reasons"] == {"money_magnitude": 1}, got["stats"]


@pytest.mark.asyncio
async def test_a_create_trustline_limit_past_the_context_is_skipped_under_its_reason(db_session) -> None:
    eq, p = await _world(db_session)
    note = await _inject(db_session, eq, p, {"op": "create_trustline", "from": p["A"].pid, "to": p["B"].pid,
                                             "equivalent": eq.code, "limit": "1E1000000"})
    lines = await db_session.scalar(select(func.count()).select_from(TrustLine).where(TrustLine.equivalent_id == eq.id))
    assert lines == 0, note
    assert note["stats"]["skipped_reasons"] == {"money_magnitude": 1}, note["stats"]


@pytest.mark.parametrize("limit", ["1E12", "1E1000000"], ids=["too large, within the context (control)", "past the context"])
@pytest.mark.asyncio
async def test_seeding_skips_a_line_whose_limit_no_column_can_hold(db_session, limit) -> None:
    """The seeder skips one trust line whose limit is too large and seeds the rest. `1E12` is the control - it always
    did; a limit past the context raised out of the seeding instead."""

    n = uuid.uuid4().hex[:6].upper()
    a, b, code = f"OVF_A_{n}", f"OVF_B_{n}", f"OV{n}"
    scenario = {"equivalents": [code], "participants": [{"id": a}, {"id": b}],
                "trustlines": [{"from": a, "to": b, "equivalent": code, "limit": limit},
                               {"from": b, "to": a, "equivalent": code, "limit": "10"}]}

    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    await db_session.flush()

    eq_id = (await db_session.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
    limits = (await db_session.execute(select(TrustLine.limit).where(TrustLine.equivalent_id == eq_id))).scalars().all()
    assert limits == [Decimal("10")], limits


# ------------------------------------------------------------------------------------------ the HTTP money doors

_OVER_HTTP = ["1E1000000", "1E-1000000", "-1E1000000", "9" * 400, "1E999999"]


@MODE_B  # `POST /payments` opens its own sessions; the route is not reachable in mode A at all
@pytest.mark.parametrize("text", _OVER_HTTP, ids=["1E1000000", "1E-1000000", "-1E1000000", "400 nines", "1E999999"])
@pytest.mark.asyncio
async def test_a_payment_amount_like_these_is_refused_by_the_door_as_bad_input(client, db_session, text) -> None:
    """GREEN BEFORE THE FIX, recorded: the door refuses the SPELLING before a `Decimal` is made."""

    db_session.add(Equivalent(code="OVP", description="OVP", precision=2))
    await db_session.commit()
    payer = await register_and_login(client, f"OvfPayer{uuid.uuid4().hex[:6]}")
    payee = await register_and_login(client, f"OvfPayee{uuid.uuid4().hex[:6]}")

    response = await client.post("/api/v1/payments", headers=payer["headers"], json={
        "tx_id": str(uuid.uuid4()), "to": payee["pid"], "equivalent": "OVP", "amount": text, "signature": "x"})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "E009" and error["request_id"], error


@pytest.mark.parametrize("text", _OVER_HTTP, ids=["1E1000000", "1E-1000000", "-1E1000000", "400 nines", "1E999999"])
@pytest.mark.asyncio
async def test_a_trust_line_limit_like_these_is_refused_by_the_door_as_bad_input(client, db_session, text) -> None:
    """GREEN BEFORE THE FIX, recorded: the door refuses the SPELLING before a `Decimal` is made."""

    db_session.add(Equivalent(code="OVL", description="OVL", precision=2))
    await db_session.commit()
    creditor = await register_and_login(client, f"OvfCreditor{uuid.uuid4().hex[:6]}")
    debtor = await register_and_login(client, f"OvfDebtor{uuid.uuid4().hex[:6]}")

    response = await client.post("/api/v1/trustlines", headers=creditor["headers"], json={
        "to": debtor["pid"], "equivalent": "OVL", "limit": text, "signature": "x"})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "E009" and error["request_id"], error
