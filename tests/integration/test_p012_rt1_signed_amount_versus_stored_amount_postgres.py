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

WHAT THE FIX IS EXPECTED TO DO (T1201). Reject at the door what the column cannot hold:
`DEFAULT_MAX_AMOUNT_SCALE` (`app/utils/validation.py:91`, today 18) is brought down to the scale
of the money columns (`Numeric(20, 8)` - `app/db/models/debt.py:14`,
`app/db/models/trustline.py:14`), so `parse_amount_decimal` refuses `0.123456789` with a 4xx
before a signature is ever bound to a number that cannot be stored. The assertions below are
written as that disjunction - **either the door refuses the amount, or the ledger holds exactly
what was signed** - so the fix turns them green without any edit here, and re-raising
`DEFAULT_MAX_AMOUNT_SCALE` back to 18 turns them red again. The spec names that second direction
as the mandatory counter-check; `test_rt_012_1_counter_check_the_door_admits_more_scale_than_the_column_stores`
below makes it executable rather than a promise.

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
from app.utils.exceptions import BadRequestException
from app.utils.validation import DEFAULT_MAX_AMOUNT_SCALE, parse_amount_decimal
from tests.integration.p012_pg_http import make_pg_client_fixture
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

pytestmark = pytest.mark.postgres


# The `debts.amount` / `trust_lines.limit` declaration. Read back from the live catalog in the
# counter-check rather than trusted as a literal, because the whole finding is a disagreement
# between this number and DEFAULT_MAX_AMOUNT_SCALE.
STORAGE_SCALE = 8

TRUSTLINE_LIMIT = "100.00"

# Every amount below is accepted by `parse_amount_decimal` today (scale <= 18) and cannot be held
# by `NUMERIC(20,8)`. The third column is what PostgreSQL 16.9 was measured to store.
UNSTORABLE_AMOUNTS = [
    # scale 9, rounds UP: the ledger ends up holding MORE than the participant signed.
    ("0.123456789", "0.12345679"),
    # scale 9, half-up tie: also rounds up, and SQLite's processor rounds the other way.
    ("0.123456785", "0.12345679"),
    # scale 9, rounds to zero: chk_debt_amount_positive fires, and the sender gets HTTP 500.
    ("0.000000001", "0"),
    # scale 18 - the largest scale the door accepts today.
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
        f"(app/core/payments/service.py:504 -> parse_amount_decimal, "
        f"DEFAULT_MAX_AMOUNT_SCALE={DEFAULT_MAX_AMOUNT_SCALE}) admitted a scale the column "
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


async def test_rt_012_1_counter_check_the_door_admits_more_scale_than_the_column_stores() -> None:
    """The counter-check the spec mandates, made executable rather than promised.

    The reproducer above is a disjunction, so it must be shown to be sensitive to the single
    constant T1201 will change - otherwise it would be indistinguishable from a test that merely
    records today's behaviour. The storage scale is read out of the live PostgreSQL catalog, not
    written here as a literal: the finding IS the disagreement between that number and
    `DEFAULT_MAX_AMOUNT_SCALE`, so hard-coding both sides would assert nothing.

    Direction of the gate: while `DEFAULT_MAX_AMOUNT_SCALE` exceeds the column scale, the
    reproducer above is red. Lower it to the column scale and the same amounts are refused at
    `parse_amount_decimal`, which is the green branch of the disjunction. Raise it back to 18 and
    they are red again.
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

    # 2026-08-24, T1201 landed. This used to assert `DEFAULT_MAX_AMOUNT_SCALE > STORAGE_SCALE`,
    # written as a self-obsoleting marker whose own message read "that is the T1201 fix". It was
    # an `assert`, so the fix turned this module red for the one reason that is not a defect.
    # Rewritten to state the post-fix invariant; the demonstrative flip below is unchanged in
    # substance and is still the point of the test.
    assert DEFAULT_MAX_AMOUNT_SCALE == STORAGE_SCALE, (
        f"the door defaults to scale {DEFAULT_MAX_AMOUNT_SCALE} and the column holds "
        f"{STORAGE_SCALE}. Any gap is `F-012-1` again: an amount admitted at the door and "
        f"rounded by the column, with the signature still covering the original string. If the "
        f"column grew, move this constant with it deliberately - do not widen the door to match "
        f"a capacity nobody verified."
    )

    # The one-constant flip, demonstrated rather than described - now in the direction that
    # re-opens the hole rather than the one that has it open.
    for amount, _ in UNSTORABLE_AMOUNTS:
        with pytest.raises(BadRequestException):
            parse_amount_decimal(amount, require_positive=True)
        assert parse_amount_decimal(
            amount, max_scale=18, require_positive=True
        ) == Decimal(amount), (
            f"widening the door back to scale 18 no longer admits {amount!r}, so this "
            f"counter-check has stopped demonstrating the flip it exists to demonstrate, and "
            f"the reproducer above can no longer be trusted to react to the door at all."
        )

    # And the control amount must survive the narrowed door, or the fix would break valid money.
    assert parse_amount_decimal(
        "0.12345678", max_scale=STORAGE_SCALE, require_positive=True
    ) == Decimal("0.12345678"), (
        "narrowing the door to the storage scale must keep every exactly-representable amount "
        "acceptable; a fix that rejects 0.12345678 would be a regression, not a fix."
    )
