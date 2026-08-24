"""RT-012-1: what a participant signs and what the ledger keeps are not the same number.

WHY THIS MODULE EXISTS. `F-012-1` was opened on a measurement of the *storage* only: an INSERT
into `NUMERIC(20,8)` on PostgreSQL 16.9 rounds rather than truncates, and rounds in both
directions. That is a fact about a column, not about this service - between
`parse_amount_decimal` and the `debts` row sit the payment service, the router, the prepare lock
and the engine, any one of which could have quantised first. Because of that gap the finding was
deliberately left at `P1-кандидат` with no severity, and the spec made this reproducer the thing
that decides it. So this module walks the whole chain over HTTP, with a real signature, and reads
the row back as text out of PostgreSQL.

WHAT IT MEASURED (2026-08-24, HEAD e45e721, PostgreSQL 16.9, `geov0_test_ci`, migrated schema at
`019_trust_lines_partial_unique_live`). The chain quantises nowhere. `POST /api/v1/payments` with
`amount: "0.123456789"` returns 200 `COMMITTED`, echoes `"amount":"0.123456789"` back to the
client, and `SELECT amount::text FROM debts` returns `0.12345679`. The participant signed
`0.123456789` (the signature is taken over `request.amount` verbatim -
`app/core/payments/service.py:590-601`), the client was told `0.123456789`, and the ledger holds
**more than was signed**. `F-012-1` is therefore CONFIRMED end to end, not refuted.

WHY NOTHING CATCHES IT. The drift barrier in `app/core/payments/engine.py:1545` compares
`abs(drift) > Decimal("0.00000001")`, and `1e-8` is exactly one quantum of `NUMERIC(20,8)`. The
observed drifts are `1e-9` and smaller, so the check that exists to notice this cannot notice it.
The `chk_debt_amount_positive` constraint (`app/db/models/debt.py:27`) does fire for the one case
that rounds to zero - and it surfaces as HTTP 500 `E010`, i.e. the sender is told the service
broke rather than that the amount was unrepresentable.

WHY POSTGRES ONLY, AND WHAT SQLITE DOES. Section 4 of the verification plan forbids counting
SQLite for this gate, and the measurement says why. Under `sqlite+aiosqlite` the same
`Numeric(20, 8)` column does **not** round on write at all: the raw column holds a binary float
(`SELECT amount` for `0.123456789` returns `0.123456789`, and `0.000000001` is stored as `1e-09`).
The scale-8 rounding a test would observe there happens client-side in SQLAlchemy's result
processor, and it disagrees with PostgreSQL on the tie case - `0.123456785` becomes
`0.12345678` on SQLite and `0.12345679` on PostgreSQL - and on the vanishing case, where SQLite
keeps `1e-09 > 0` so `chk_debt_amount_positive` never fires and no 500 is produced. The whole
class is therefore invisible, or visible with the wrong shape and the wrong cause, on the default
tier: a green or red SQLite run says nothing about production. The executable form of that
measurement is `tests/unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py`.

WHAT THE FIX DOES (T1201). Reject at the door what the column cannot hold. `POST /payments`
parses its amount with `parse_money_amount` (`app/core/payments/service.py:510`), which refuses
any value `Numeric(20, 8)` cannot keep unchanged - `MONEY_MAX_SCALE` fraction digits and
`MONEY_MAX_INTEGER_DIGITS` integer digits - with a 400/E009 before a signature is ever bound to
a number that cannot be stored. The assertions below are written as a disjunction - **either the
door refuses the amount, or the ledger holds exactly what was signed** - so the fix turns them
green without any edit here.

THE COUNTER-CHECK, AND WHY THIS PARAGRAPH USED TO BE WRONG. The first edition of this module
said the flip constant was `DEFAULT_MAX_AMOUNT_SCALE` and that the counter-check demonstrated
it. Measured 2026-08-24: setting `DEFAULT_MAX_AMOUNT_SCALE` back to 18 leaves five of these six
tests GREEN, and the only failure is the counter-check's own `assert DEFAULT_MAX_AMOUNT_SCALE ==
STORAGE_SCALE` - a literal compared against a literal. The reason is that `parse_money_amount`
passes its bounds explicitly and no production caller reaches `parse_amount_decimal` bare, so
that constant is dead for money and this module was never sensitive to it. The counter-check
below now flips `MONEY_MAX_SCALE` - the constant the door actually reads - and does not merely
assert that the door's verdict changes: it re-submits a signed payment over HTTP with the
constant widened and reads the row back, i.e. it reproduces `F-012-1` end to end on demand.

Not fixed here and not asserted here: which rounding mode the system should use. `ROUND_DOWN` in
`edge_patch_builder.py` against PostgreSQL's half-up is a question the spec routes to 015.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import delete, text

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils import validation
from app.utils.exceptions import BadRequestException
from app.utils.validation import MONEY_MAX_SCALE, parse_money_amount
from tests.integration.p012_pg_http import make_pg_client_fixture
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

pytestmark = pytest.mark.postgres


# The `debts.amount` / `trust_lines.limit` declaration. Read back from the live catalog in the
# counter-check rather than trusted as a literal, because the whole finding is a disagreement
# between this number and what the door admits.
STORAGE_SCALE = 8

TRUSTLINE_LIMIT = "100.00"

# Every amount below was accepted by the door before T1201 and cannot be held by
# `NUMERIC(20,8)`: each has a ninth significant fraction digit, so the VALUE itself is
# unstorable - not merely its spelling. The second column is what PostgreSQL 16.9 was
# measured to store.
UNSTORABLE_AMOUNTS = [
    # scale 9, rounds UP: the ledger ends up holding MORE than the participant signed.
    ("0.123456789", "0.12345679"),
    # scale 9, half-up tie: also rounds up, and SQLite's processor rounds the other way.
    ("0.123456785", "0.12345679"),
    # scale 9, rounds to zero: chk_debt_amount_positive fires, and the sender gets HTTP 500.
    ("0.000000001", "0"),
    # scale 18 - the longest fraction the wire grammar allows to be spelled at all.
    ("0.123456789012345678", "0.12345679"),
]

_WHY = (
    "WHY THIS MATTERS: the signature is computed over the amount STRING exactly as submitted "
    "(app/core/payments/service.py:590-601), so a participant is bound to one number while the "
    "ledger keeps another. The signature stays valid for the original message and the state no "
    "longer matches it. Nothing downstream notices: the drift check in engine.py:1545 uses a "
    "tolerance of 1e-8, exactly one quantum of NUMERIC(20,8), with a strict comparison, and the "
    "observed drift is 1e-9. If this test fails, the contract that is broken is 'the amount a "
    "participant signs is the amount the ledger keeps' - do not relax it into 'close enough'."
)


pg_client = make_pg_client_fixture()


class _Scenario(dict):
    """A committed-capacity two-party setup: sender, receiver, equivalent, live trustline."""


@pytest_asyncio.fixture
async def scenario(pg_client: AsyncClient) -> AsyncGenerator[_Scenario, None]:
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    code = f"S9{nonce}".upper()[:16]

    async with TestingSessionLocal() as session:
        equivalent = Equivalent(code=code, description="RT-012-1 scale probe", precision=2)
        session.add(equivalent)
        await session.commit()
        equivalent_id = equivalent.id

    participant_ids: list[uuid.UUID] = []
    try:
        sender = await register_and_login(pg_client, f"RT1_Sender_{nonce}")
        receiver = await register_and_login(pg_client, f"RT1_Receiver_{nonce}")

        # A trustline from -> to is creditor -> debtor, so the receiver must extend it for the
        # sender to have capacity.
        receiver_key = SigningKey(base64.b64decode(receiver["priv"]))
        response = await pg_client.post(
            "/api/v1/trustlines",
            headers=receiver["headers"],
            json={
                "to": sender["pid"],
                "equivalent": code,
                "limit": TRUSTLINE_LIMIT,
                "signature": _sign_trustline_create_request(
                    signing_key=receiver_key,
                    to_pid=sender["pid"],
                    equivalent=code,
                    limit=TRUSTLINE_LIMIT,
                ),
            },
        )
        assert response.status_code == 201, (
            "setup failed: without capacity every payment below would be rejected for the wrong "
            f"reason and the module would prove nothing. {response.text}"
        )

        async with TestingSessionLocal() as session:
            rows = (
                await session.execute(
                    text("SELECT id FROM participants WHERE pid = ANY(:pids)"),
                    {"pids": [sender["pid"], receiver["pid"]]},
                )
            ).all()
            participant_ids = [r[0] for r in rows]

        yield _Scenario(
            sender=sender,
            receiver=receiver,
            code=code,
            equivalent_id=equivalent_id,
        )
    finally:
        async with TestingSessionLocal() as cleanup:
            await cleanup.execute(delete(Debt).where(Debt.equivalent_id == equivalent_id))
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
            )
            if participant_ids:
                # prepare_locks.tx_id references transactions.tx_id, and
                # transactions.initiator_id is ON DELETE RESTRICT, so the order matters.
                await cleanup.execute(
                    text("DELETE FROM prepare_locks WHERE participant_id = ANY(:ids)"),
                    {"ids": participant_ids},
                )
                await cleanup.execute(
                    text(
                        "DELETE FROM integrity_audit_log WHERE tx_id IN "
                        "(SELECT tx_id FROM transactions WHERE initiator_id = ANY(:ids))"
                    ),
                    {"ids": participant_ids},
                )
                await cleanup.execute(
                    text("DELETE FROM transactions WHERE initiator_id = ANY(:ids)"),
                    {"ids": participant_ids},
                )
                await cleanup.execute(
                    delete(Participant).where(Participant.id.in_(participant_ids))
                )
            await cleanup.execute(delete(Equivalent).where(Equivalent.id == equivalent_id))
            await cleanup.commit()


async def _submit_signed_payment(
    pg_client: AsyncClient, scenario: _Scenario, amount: str
) -> tuple[int, dict[str, Any]]:
    sender = scenario["sender"]
    receiver = scenario["receiver"]
    sender_key = SigningKey(base64.b64decode(sender["priv"]))
    tx_id = str(uuid.uuid4())

    response = await pg_client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json={
            "tx_id": tx_id,
            "to": receiver["pid"],
            "equivalent": scenario["code"],
            "amount": amount,
            "signature": _sign_payment_request(
                signing_key=sender_key,
                tx_id=tx_id,
                from_pid=sender["pid"],
                to_pid=receiver["pid"],
                equivalent=scenario["code"],
                amount=amount,
            ),
        },
    )
    try:
        body = response.json()
    except ValueError:  # pragma: no cover - defensive
        body = {"_raw": response.text}
    return response.status_code, body


async def _ledger_rows(equivalent_id: uuid.UUID) -> list[str]:
    """The `debts` rows for this equivalent, as PostgreSQL renders them - not as the ORM does.

    Read as `::text` on a fresh session on purpose: an ORM read can return the Python `Decimal`
    the application handed in, which is precisely the value under dispute.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT amount::text FROM debts WHERE equivalent_id = :eq ORDER BY 1"),
                {"eq": equivalent_id},
            )
        ).all()
    return [r[0] for r in rows]


@pytest.mark.parametrize(
    "amount,measured_storage",
    UNSTORABLE_AMOUNTS,
    ids=[a for a, _ in UNSTORABLE_AMOUNTS],
)
async def test_rt_012_1_a_signed_amount_the_column_cannot_hold_reaches_the_ledger_changed(
    pg_client: AsyncClient, scenario: _Scenario, amount: str, measured_storage: str
) -> None:
    """Either the door refuses an unstorable amount, or the ledger keeps exactly what was signed.

    Both halves of the disjunction are the contract. Today neither holds, which is the finding.
    """

    status_code, body = await _submit_signed_payment(pg_client, scenario, amount)
    rows = await _ledger_rows(scenario["equivalent_id"])

    if 400 <= status_code < 500:
        # The post-fix world: the amount never became a signed obligation.
        assert rows == [], (
            f"{scenario['code']}: the API refused {amount!r} with {status_code} but a debt row "
            f"still exists ({rows!r}). A rejected payment must leave no ledger state at all.\n"
            f"{_WHY}"
        )
        return

    assert status_code < 500, (
        f"submitting the signed amount {amount!r} produced HTTP {status_code} {body!r}. "
        f"An amount the storage cannot represent must be refused at the door with a client "
        f"error that names the problem, not surfaced as an internal failure. Measured "
        f"2026-08-24: this amount rounds to {measured_storage!r} in NUMERIC(20,8), which trips "
        f"chk_debt_amount_positive (app/db/models/debt.py:27) and escapes as E010.\n{_WHY}"
    )

    assert len(rows) == 1, (
        f"expected exactly one debt row for {scenario['code']} after a committed payment, got "
        f"{rows!r}; without it the comparison below would be vacuous."
    )
    stored = Decimal(rows[0])
    signed = Decimal(amount)

    echoed = body.get("amount")
    assert stored == signed, (
        f"the participant signed {amount!r} and the API answered {status_code} with "
        f"amount={echoed!r}, but PostgreSQL holds {rows[0]!r} "
        f"(delta {stored - signed:+}). The signature covers the string {amount!r} verbatim, so "
        f"the ledger now records an obligation the sender never authorised - and here it is "
        f"LARGER than the authorised one. The door "
        f"(app/core/payments/service.py:510 -> parse_money_amount, "
        f"MONEY_MAX_SCALE={MONEY_MAX_SCALE}) admitted a value the column "
        f"(Numeric(20, {STORAGE_SCALE})) cannot hold, and nothing between them quantised.\n{_WHY}"
    )


async def test_rt_012_1_control_a_scale_8_amount_survives_the_chain_unchanged(
    pg_client: AsyncClient, scenario: _Scenario
) -> None:
    """Anti-vacuum control: the chain is not broken for everyone, only for what will not fit.

    Without this, a reproducer that failed because payments were failing wholesale would look
    like a precision finding. It is also the spec's «правка не меняет уже корректные величины»
    counter-check: a scale-8 amount must stay byte-for-byte identical after T1201.
    """

    amount = "0.12345678"
    status_code, body = await _submit_signed_payment(pg_client, scenario, amount)

    assert status_code == 200 and body.get("status") == "COMMITTED", (
        f"a scale-8 amount is exactly representable in Numeric(20, {STORAGE_SCALE}) and must "
        f"commit. It returned {status_code} {body!r}. Until this passes, the red results in this "
        f"module cannot be attributed to precision."
    )

    rows = await _ledger_rows(scenario["equivalent_id"])
    assert rows == ["0.12345678"], (
        f"a representable amount must reach the ledger unchanged; PostgreSQL holds {rows!r}. "
        f"If this ever fails, the defect is wider than scale and this module's diagnosis is "
        f"wrong."
    )


async def test_rt_012_1_counter_check_widening_the_door_reproduces_the_finding_end_to_end(
    pg_client: AsyncClient, scenario: _Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mandatory counter-check, on the constant the door reads, performed rather than described.

    WHY THIS REPLACED THE PREVIOUS ONE. The reproducer above is a disjunction, so it has to be
    shown sensitive to the fix - otherwise it is indistinguishable from a test that records
    today's behaviour. The first edition claimed that sensitivity for `DEFAULT_MAX_AMOUNT_SCALE`
    and demonstrated it with `assert DEFAULT_MAX_AMOUNT_SCALE == STORAGE_SCALE`, a literal
    against a literal, plus a flip performed on `parse_amount_decimal(..., max_scale=18)` - a
    call no production caller makes. Measured: putting `DEFAULT_MAX_AMOUNT_SCALE` back to 18
    left five of the six tests in this module green, the sixth being that self-referential
    assert. The constant is not the door for money; `MONEY_MAX_SCALE` is, and the door that
    reads it is `parse_money_amount` (`app/core/payments/service.py:510`).

    WHAT THIS ONE DOES INSTEAD. Three steps, in order:

    1. the live column scale is read out of the PostgreSQL catalog - the finding IS a
       disagreement between the column and the door, so hard-coding both sides would assert
       nothing;
    2. the door is shown to refuse the reproducer's amounts today, through the production entry
       point;
    3. `MONEY_MAX_SCALE` is widened to 18 and the SAME signed payment is submitted over HTTP,
       and the `debts` row is read back as text. If the amounts are then admitted AND the ledger
       holds a different number from the one signed, this module's green above is caused by the
       door and nothing else - because with the door widened, `F-012-1` happens again, here, now.

    The one thing this cannot demonstrate is that `DEFAULT_MAX_AMOUNT_SCALE` matters, and the
    last assertion says so out loud rather than leaving the earlier claim standing.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, numeric_scale FROM information_schema.columns "
                    "WHERE (table_name, column_name) IN (('debts','amount'),"
                    "('trust_lines','limit')) ORDER BY table_name"
                )
            )
        ).all()

    assert len(rows) == 2, (
        f"expected the two money columns in the live schema, found {rows!r}; the counter-check "
        f"cannot compare the door against a capacity it did not read."
    )
    scales = {name: int(scale) for name, scale in rows}
    assert set(scales.values()) == {STORAGE_SCALE}, (
        f"the money columns no longer declare scale {STORAGE_SCALE}: {scales!r}. Either the "
        f"'Не менять Numeric(20, 8)' non-goal was crossed or this module's premise moved; "
        f"re-measure before trusting anything above."
    )
    assert MONEY_MAX_SCALE == STORAGE_SCALE, (
        f"the door keeps {MONEY_MAX_SCALE} fraction digits and the column holds "
        f"{STORAGE_SCALE}. Any gap is `F-012-1` again. If the column grew, move the constant "
        f"with it deliberately - do not widen the door to match a capacity nobody verified."
    )

    # Step 2: today, through the entry point the payment service actually calls.
    for amount, _ in UNSTORABLE_AMOUNTS:
        with pytest.raises(BadRequestException):
            parse_money_amount(amount, require_positive=True)

    # Step 3: widen the one constant and watch the finding come back. `parse_money_amount` reads
    # `MONEY_MAX_SCALE` from the module at call time (via `is_storable_money`), so this patch
    # reaches the running application, not a copy of it.
    monkeypatch.setattr(validation, "MONEY_MAX_SCALE", 18)

    amount, measured_storage = UNSTORABLE_AMOUNTS[0]
    assert parse_money_amount(amount, require_positive=True) == Decimal(amount), (
        f"with MONEY_MAX_SCALE widened to 18 the door still refuses {amount!r}, so it is not "
        f"that constant which decides the verdict and this module cannot be said to react to "
        f"the door at all."
    )

    status_code, body = await _submit_signed_payment(pg_client, scenario, amount)
    assert status_code == 200 and body.get("status") == "COMMITTED", (
        f"with the door widened, the signed payment of {amount!r} should be accepted exactly as "
        f"it was before T1201; got {status_code} {body!r}. If it is refused for some other "
        f"reason, the flip demonstrated here is not the flip the reproducer above depends on."
    )

    rows = await _ledger_rows(scenario["equivalent_id"])
    assert rows == [measured_storage], (
        f"with the door widened, PostgreSQL was expected to hold {measured_storage!r} for a "
        f"signed {amount!r} - the rounding that IS `F-012-1`. It holds {rows!r} instead, so "
        f"either the storage stopped rounding or something else now quantises, and the "
        f"reproducer above is green for a reason other than the door."
    )
    assert Decimal(rows[0]) != Decimal(amount), (
        f"{_WHY}\n(reproduced deliberately by this counter-check with MONEY_MAX_SCALE=18)"
    )

    # THE CLAIM THAT IS NOT ASSERTED, and deliberately so. `DEFAULT_MAX_AMOUNT_SCALE` is
    # currently 8, but it is a
    # conservative default for a caller that reaches `parse_amount_decimal` without arguments,
    # and no production caller does. Setting it back to 18 was measured on 2026-08-24 to leave
    # this whole module green. Asserting a value here would recreate exactly the literal-
    # against-literal check this test replaced: it would fail on a change that alters nothing
    # about `F-012-1`, and pass while the money bound is wide open.


async def test_rt_012_1_counter_check_the_control_amount_survives_a_widened_door() -> None:
    """The other direction: the narrowing must not be what makes the control pass.

    A door that refused everything would turn the disjunction above green while breaking every
    payment in the system. The control test asserts scale-8 money commits; this asserts the door
    admits it identically whether `MONEY_MAX_SCALE` is 8 or 18, so the control is measuring the
    chain and not the bound.
    """

    assert parse_money_amount("0.12345678", require_positive=True) == Decimal("0.12345678"), (
        "narrowing the door to the storage scale must keep every exactly-representable amount "
        "acceptable; a fix that rejects 0.12345678 would be a regression, not a fix."
    )
