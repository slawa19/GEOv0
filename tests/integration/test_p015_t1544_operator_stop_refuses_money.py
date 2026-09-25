"""T1544: the operator's equivalent-level stop refuses money - the refusal itself.

WHAT WAS WRONG. `PATCH /admin/equivalents/{code}` with `is_active=false` is the operator's only
equivalent-level stop, and no money path read it: reproduced 2026-09-13 through the admin API - after
the stop three payments committed 30.00 and `POST /clearing/auto` cleared the cycle.

WHAT THIS MODULE HOLDS, one control per check:

* the prepare-time check, which refuses a new payment before any transaction row exists;
* the commit-time check, which refuses a payment prepared BEFORE the stop and committed after it;
* the clearing check, on the execution path;
* the `clearing-real` simulator route, which reports the refusal as its declared `409` instead of
  `500 CLEARING_FAILED`;
* that an accepted payment still replays its stored result, and that reactivation restores money.

The refusal is `409/E008` WITHOUT `details.retryable`: that flag belongs to the serialization-conflict
variant, and repeating a request against a deactivated equivalent cannot succeed.

WHAT IT DOES NOT HOLD. The race guarantees - a PATCH racing a payment commit, a PATCH racing a clearing
- need two concurrent transactions and are in `test_p015_t1544_operator_stop_races_postgres.py`. (This
module was written for SQLite, where the owner lock was a no-op; it runs on PostgreSQL since 017.)
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey
from sqlalchemy import func, select, update

from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)
from tests.conftest import MODE_B, sessionmaker_of

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


def _code() -> str:
    return "T44" + uuid.uuid4().hex[:8].upper()


async def _create_equivalent(client, code: str) -> None:
    resp = await client.post(
        "/api/v1/admin/equivalents",
        json={"code": code, "precision": 2, "reason": "t1544"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text


async def _set_active(client, code: str, active: bool) -> None:
    resp = await client.patch(
        f"/api/v1/admin/equivalents/{code}",
        json={"is_active": active, "reason": "t1544 operator"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is active


async def _trust(client, truster, trusted, code: str, limit: str = "100.00") -> None:
    key = SigningKey(base64.b64decode(truster["priv"]))
    resp = await client.post(
        "/api/v1/trustlines",
        json={
            "to": trusted["pid"],
            "equivalent": code,
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=key, to_pid=trusted["pid"], equivalent=code, limit=limit
            ),
        },
        headers=truster["headers"],
    )
    assert resp.status_code == 201, resp.text


def _payment_body(payer, payee, code: str, amount: str, tx_id: str | None = None) -> dict:
    tx_id = tx_id or str(uuid.uuid4())
    return {
        "tx_id": tx_id,
        "to": payee["pid"],
        "equivalent": code,
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=SigningKey(base64.b64decode(payer["priv"])),
            tx_id=tx_id,
            from_pid=payer["pid"],
            to_pid=payee["pid"],
            equivalent=code,
            amount=amount,
        ),
    }


async def _debt_totals(db_session, code: str) -> tuple[int, Decimal]:
    count, total = (
        await db_session.execute(
            select(func.count(Debt.id), func.coalesce(func.sum(Debt.amount), 0))
            .join(Equivalent, Equivalent.id == Debt.equivalent_id)
            .where(Equivalent.code == code)
        )
    ).one()
    return int(count), Decimal(str(total))


async def _replay_effects(db_session, code: str, tx_id: str) -> dict[str, object]:
    """T1523 cell 1, the operator-stop parameter: debts, transaction rows, journal - all three.

    Added 2026-09-21. The test below asserted the debt row and the replayed status; the journal was
    the evidence the T1523 inventory found almost never checked on a replay, and an envelope opened
    a second time is exactly what "the stored result was re-executed" would look like.
    """

    count, total = await _debt_totals(db_session, code)
    transactions = sorted(
        (str(state), repr(payload))
        for state, payload in (
            await db_session.execute(
                select(Transaction.state, Transaction.payload).where(Transaction.tx_id == tx_id)
            )
        ).all()
    )
    envelopes = (
        await db_session.execute(
            select(debt_operations.c.id, debt_operations.c.state, debt_operations.c.effect_count)
            .where(debt_operations.c.kind == "PAYMENT", debt_operations.c.identity == tx_id)
        )
    ).all()
    entries: list[tuple[str, str, str, str]] = []
    for operation_id, _state, _count in envelopes:
        rows = (
            await db_session.execute(
                select(
                    debt_journal_entries.c.amount_before,
                    debt_journal_entries.c.amount_after,
                    debt_journal_entries.c.delta,
                ).where(debt_journal_entries.c.operation_id == operation_id)
            )
        ).all()
        entries.extend(
            (str(operation_id), str(before), str(after), str(delta))
            for before, after, delta in rows
        )
    entries.sort()
    return {
        "debts": (count, total),
        "transactions": transactions,
        "envelopes": sorted((str(state), count) for _id, state, count in envelopes),
        "entries": entries,
    }


def _assert_stop_refusal(resp, code: str) -> None:
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "E008", error
    assert error["details"]["reason"] == MoneyBoundary.EQUIVALENT_INACTIVE_REASON, error
    assert error["details"]["equivalents"] == [code], error
    assert "retryable" not in error["details"], (
        f"the operator stop was labelled retryable; repeating it cannot succeed: {error}"
    )


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_in_a_deactivated_equivalent_is_refused_before_any_transaction_exists(
    client, db_session
) -> None:
    """RED before T1544: the payment committed 10.00 after the operator's stop."""
    code = _code()
    await _create_equivalent(client, code)
    alice = await register_and_login(client, "A_" + code)
    bob = await register_and_login(client, "B_" + code)
    await _trust(client, bob, alice, code)

    await _set_active(client, code, False)
    body = _payment_body(alice, bob, code, "10.00")
    resp = await client.post("/api/v1/payments", json=body, headers=alice["headers"])

    _assert_stop_refusal(resp, code)
    assert await _debt_totals(db_session, code) == (0, Decimal("0"))
    # The prepare-time check's own control: without it the commit check would still refuse, but only
    # after a transaction had been created, routed and prepared.
    stored = (
        await db_session.execute(select(Transaction.tx_id).where(Transaction.tx_id == body["tx_id"]))
    ).scalar_one_or_none()
    assert stored is None, "the refusal came after a transaction row was written"

    # Control: the stop is the operator's to lift, and lifting it restores money.
    await _set_active(client, code, True)
    resp = await client.post(
        "/api/v1/payments",
        json=_payment_body(alice, bob, code, "10.00"),
        headers=alice["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMMITTED"
    assert await _debt_totals(db_session, code) == (1, Decimal("10.00"))


@MODE_B
@pytest.mark.asyncio
async def test_an_accepted_payment_still_replays_its_result_after_the_stop(client, db_session) -> None:
    """The check sits after the idempotency decision: a stored result is not a new money operation."""
    code = _code()
    await _create_equivalent(client, code)
    alice = await register_and_login(client, "A_" + code)
    bob = await register_and_login(client, "B_" + code)
    await _trust(client, bob, alice, code)

    body = _payment_body(alice, bob, code, "10.00")
    first = await client.post("/api/v1/payments", json=body, headers=alice["headers"])
    assert first.status_code == 200 and first.json()["status"] == "COMMITTED", first.text

    # T1523 cell 1 premise, before the stop: exactly one COMPLETED envelope whose effect_count
    # matches its entry rows. Otherwise "nothing moved on replay" would also be true of a payment
    # that had moved nothing in the first place.
    before = await _replay_effects(db_session, code, body["tx_id"])
    assert before["debts"] == (1, Decimal("10.00")), before["debts"]
    assert before["envelopes"] == [("COMPLETED", len(before["entries"]))], before["envelopes"]
    assert len(before["entries"]) > 0, before

    await _set_active(client, code, False)
    replay = await client.post("/api/v1/payments", json=body, headers=alice["headers"])

    assert replay.status_code == 200, replay.text
    assert replay.json()["tx_id"] == body["tx_id"]
    assert replay.json()["status"] == "COMMITTED"
    assert await _debt_totals(db_session, code) == (1, Decimal("10.00"))
    assert await _replay_effects(db_session, code, body["tx_id"]) == before, (
        "the replay after the stop moved debts, wrote a transaction row or touched the journal"
    )

    # The stand can see the outcome it was built for (AGENTS.md §15): the stop really is in force,
    # so the 200 above came from the stored row and not from a stop that was never applied.
    fresh = await client.post(
        "/api/v1/payments",
        json=_payment_body(alice, bob, code, "10.00"),
        headers=alice["headers"],
    )
    _assert_stop_refusal(fresh, code)


@MODE_B
@pytest.mark.asyncio
async def test_a_payment_prepared_before_the_stop_is_refused_at_commit(
    client, db_session, monkeypatch
) -> None:
    """The commit-time check's own control. RED before T1544, and red if only prepare checked.

    The stop lands between prepare and commit: the payment passed the prepare-time check while the
    equivalent was active. Only the commit check can refuse it now. (The concurrent form of this - the
    stop committing while the commit waits - is PostgreSQL's, see the races module.)

    SINCE 019 STAGE 3 (`T1904`) the payment is one transaction, so the stop is committed by ANOTHER
    session (before, the stand committed it on the payment's own session, which would now commit the
    payment half-way). The commit guard's `FOR SHARE` then meets an equivalent row changed behind the
    payment's snapshot - `40001` - and `pay()` retries the whole attempt, whose pre-check refuses the
    stop before the payment's row exists. Outcome: the same stop refusal and no money. The first
    attempt reached the payment operation, so the request was ADMITTED, and `pay()` remembers that for
    the same identity (spec, FORK-5; `T1905`): the refusal the retry meets is definitive and stored
    `ABORTED` with the stop's error - as before stage 3; between `T1904` and `T1905` it left no row.
    """
    code = _code()
    await _create_equivalent(client, code)
    alice = await register_and_login(client, "A_" + code)
    bob = await register_and_login(client, "B_" + code)
    await _trust(client, bob, alice, code)

    seen: dict[str, str] = {}
    original_commit = PaymentService._apply_payment

    # 019 stage 4: "between prepare and commit" is the entry of the money phase of the direct
    # execution - after the binding phase, before the stop's `FOR SHARE`.
    async def _stop_between_prepare_and_commit(self, declaration, **kwargs):
        if "state_at_commit" not in seen:
            seen["state_at_commit"] = (
                await self.session.execute(
                    select(Transaction.state).where(Transaction.tx_id == declaration.tx_id)
                )
            ).scalar_one()
            async with sessionmaker_of(db_session)() as operator:
                await operator.execute(
                    update(Equivalent).where(Equivalent.code == code).values(is_active=False)
                )
                await operator.commit()
        return await original_commit(self, declaration, **kwargs)

    monkeypatch.setattr(PaymentService, "_apply_payment", _stop_between_prepare_and_commit)

    body = _payment_body(alice, bob, code, "10.00")
    resp = await client.post("/api/v1/payments", json=body, headers=alice["headers"])

    # Since 019 stage 4 the row is inserted `COMMITTED` inside the payment's operation (uncommitted,
    # invisible to others); before it, the engine had it `PREPARED` here.
    assert seen.get("state_at_commit") == "COMMITTED", (
        f"premise: the payment did not reach its money phase with its row inserted: {seen}"
    )
    _assert_stop_refusal(resp, code)
    assert await _debt_totals(db_session, code) == (0, Decimal("0"))
    stored = (
        await db_session.execute(
            select(Transaction.state, Transaction.error).where(Transaction.tx_id == body["tx_id"])
        )
    ).one_or_none()
    assert stored is not None and stored[0] == "ABORTED", stored
    assert (stored[1] or {}).get("details", {}).get("reason") == "equivalent_inactive", stored
    locks = (
        await db_session.execute(
            select(func.count(PrepareLock.id)).where(PrepareLock.tx_id == body["tx_id"])
        )
    ).scalar_one()
    assert locks == 0


@MODE_B
@pytest.mark.asyncio
async def test_clearing_in_a_deactivated_equivalent_is_refused_and_keeps_the_debts(
    client, db_session
) -> None:
    """RED before T1544: `POST /clearing/auto` cleared the cycle after the operator's stop.

    A refusal and not `200` with zero cycles: a zero result would read as "nothing to clear" and hide
    the stop.
    """
    code = _code()
    await _create_equivalent(client, code)
    a = await register_and_login(client, "A_" + code)
    b = await register_and_login(client, "B_" + code)
    c = await register_and_login(client, "C_" + code)
    for truster, trusted in ((b, a), (c, b), (a, c)):
        await _trust(client, truster, trusted, code)
    for payer, payee in ((a, b), (b, c), (c, a)):
        resp = await client.post(
            "/api/v1/payments",
            json=_payment_body(payer, payee, code, "10.00"),
            headers=payer["headers"],
        )
        assert resp.status_code == 200, resp.text
    assert await _debt_totals(db_session, code) == (3, Decimal("30.00")), "premise: no cycle to clear"

    await _set_active(client, code, False)
    resp = await client.post(f"/api/v1/clearing/auto?equivalent={code}", headers=a["headers"])

    _assert_stop_refusal(resp, code)
    db_session.expire_all()
    assert await _debt_totals(db_session, code) == (3, Decimal("30.00"))

    # Control: the same cycle clears once the stop is lifted.
    await _set_active(client, code, True)
    resp = await client.post(f"/api/v1/clearing/auto?equivalent={code}", headers=a["headers"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared_cycles"] == 1
    db_session.expire_all()
    assert await _debt_totals(db_session, code) == (0, Decimal("0"))


_SIM_EQ = "T44SIM"


@pytest.fixture
def run_owning_the_cycle(monkeypatch):
    """A simulator run whose scenario holds s1/s2/s3, the participants of the cycle below."""
    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(
            run_id=str(run_id),
            state="running",
            owner_id="",
            _real_seeded=True,
            _real_seeding_lock=None,
        ),
    )
    run = RunRecord(run_id="run-t1544", scenario_id="scn-t1544", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": pid, "name": pid.upper(), "type": "person", "status": "active"}
            for pid in ("s1", "s2", "s3")
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, "run-t1544", run)
    return simulator_module


@MODE_B
@pytest.mark.asyncio
async def test_clearing_real_reports_the_stop_as_its_declared_409(
    client, db_session, run_owning_the_cycle
) -> None:
    """Without the mapping the refusal fell into `500 CLEARING_FAILED`, a failure it is not."""
    eq = Equivalent(code=_SIM_EQ, precision=2, is_active=True)
    db_session.add(eq)
    people = {
        pid: Participant(
            id=uuid.uuid4(),
            pid=pid,
            display_name=pid.upper(),
            public_key=pid * 32,
            type="person",
            status="active",
            profile={},
        )
        for pid in ("s1", "s2", "s3")
    }
    db_session.add_all(people.values())
    await db_session.commit()
    for debtor, creditor in (("s1", "s2"), ("s2", "s3"), ("s3", "s1")):
        db_session.add(
            TrustLine(
                from_participant_id=people[creditor].id,
                to_participant_id=people[debtor].id,
                equivalent_id=eq.id,
                limit=Decimal("1000"),
                policy={"auto_clearing": True},
                status="active",
            )
        )
        async with debt_fixture_setup(db_session, label="setup"):
            db_session.add(
                Debt(
                    debtor_id=people[debtor].id,
                    creditor_id=people[creditor].id,
                    equivalent_id=eq.id,
                    amount=Decimal("100"),
                )
            )
    await db_session.commit()
    await db_session.execute(
        update(Equivalent).where(Equivalent.id == eq.id).values(is_active=False)
    )
    await db_session.commit()

    resp = await client.post(
        "/api/v1/simulator/runs/run-t1544/actions/clearing-real",
        headers=ADMIN,
        json={"equivalent": _SIM_EQ, "max_depth": 6, "client_action_id": "t1544"},
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "CONFLICT", body
    assert body["details"]["reason"] == MoneyBoundary.EQUIVALENT_INACTIVE_REASON, body
    assert "retryable" not in body["details"], body
    db_session.expire_all()
    assert await _debt_totals(db_session, _SIM_EQ) == (3, Decimal("300"))
