from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from starlette.requests import Request

from app.api.v1.integrity import (
    repair_cap_debts_to_trust_limits,
    repair_net_mutual_debts,
)
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.ledger.journal import DebtJournalError, Reason
from tests.conftest import MODE_B, sessionmaker_of

from tests.debt_setup import debt_fixture_setup

# MODE B (017 stage 2b, T1702), for the whole module: both tests read the debts "on a session of
# their own", before and after the repair. In mode A on PostgreSQL that session could not see the
# uncommitted seed - the first test failed its non-vacuity check ("stand: no debts were seeded"), the
# second passed on two empty reads (stage-2 catalogue, class VIS).
pytestmark = MODE_B


def _repair_request(operation: str) -> Request:
    """The minimum ASGI scope the repair handlers read: `.client` and `.headers`."""

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/integrity/repair/{operation}",
            "headers": [],
        }
    )


async def _stored_debts(db_session, equivalent_id) -> list[tuple]:
    """The debts of one equivalent, read on a session of their own, as plain values.

    The session of their own is opened over the database `db_session` talks to (`sessionmaker_of`):
    on PostgreSQL in mode B that is a clone, and `TestingSessionLocal` would read the tier instead.
    """

    async with sessionmaker_of(db_session)() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount)
                .where(Debt.equivalent_id == equivalent_id)
                .order_by(Debt.amount.desc())
            )
        ).all()
    return [(str(d), str(c), str(a)) for d, c, a in rows]


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _cache_value():
    return (0.0, {}, {}, {}, {}, {})


async def _seed_participants_and_equivalent(db_session, *, suffix: str, code: str):
    debtor = Participant(
        pid=f"debtor-{suffix}",
        display_name="Debtor",
        public_key=(suffix[0].upper() or "D") * 64,
        type="person",
        status="active",
    )
    creditor = Participant(
        pid=f"creditor-{suffix}",
        display_name="Creditor",
        public_key=(suffix[-1].upper() or "C") * 64,
        type="person",
        status="active",
    )
    equivalent = Equivalent(code=code, description=code, precision=2)
    db_session.add_all([debtor, creditor, equivalent])
    await db_session.flush()
    return debtor, creditor, equivalent


async def _admin_audit(db_session, *, action: str):
    return (
        await db_session.execute(select(AuditLog).where(AuditLog.action == action))
    ).scalar_one_or_none()


@pytest.fixture
def repairs_opened(monkeypatch):
    """Open the repair door for one test, and say why that is not a contradiction.

    `INTEGRITY_REPAIRS_ENABLED` is false by default since 2026-09-11: the cap repair deletes debt
    whose trustline is FROZEN, and neither repair locks against a payment between PREPARE and
    COMMIT (`F-015-6`, P1, fixed by `T1511` of programme 015).

    The tests below are NOT about whether the repairs should run. They are about the mechanics -
    atomicity, audit, cache invalidation, rollback - and that evidence has to stay alive and
    passing, because `T1511` will reopen these endpoints and will need it. Deleting or skipping
    these tests to get green while the door is shut would remove the only executable description
    of how the repairs behave. So the door is opened HERE, explicitly, per test.
    """
    monkeypatch.setattr(settings, "INTEGRITY_REPAIRS_ENABLED", True, raising=False)
    yield



# =================================================================================================
# RE-SCOPED BY THE DEBT JOURNAL, 2026-09-12 (design v2 §7, "Repairs/measurement excluded")
# =================================================================================================
#
# WHAT THESE TESTS USED TO ASSERT. The success-path mechanics of both repairs: the netting and the
# capping land, an admin audit row is written in the same transaction, the topology cache is or is
# not invalidated, and a pre-commit failure rolls all of it back. That evidence was last green at
# `7de0a7c` and is recorded in the spec; `T1511` must restore it as acceptance once the repairs are
# instrumented under owner-lock discipline.
#
# WHY IT CANNOT BE ASSERTED NOW. Step 4 armed the debt journal, and design v2 §7 deliberately leaves
# `app/api/v1/integrity.py` OUT of the instrumented perimeter: the repairs hold no owner lock, take
# no `FOR UPDATE` and can land between a payment's PREPARE and COMMIT (`F-015-6`). So a repair is
# exactly what the journal exists to refuse - a writer moving money with no operation naming it -
# and it is refused at its first flush, before it writes a debt, an audit row or a cache
# invalidation.
#
# WHAT THEY ASSERT INSTEAD, and it is not a weaker statement: the refusal happens, it happens BEFORE
# anything else the repair would have done, and every rollback assertion the old tests made still
# holds - debts byte-identical on a fresh read, no admin audit row, the topology cache sentinel
# untouched. Nothing here is skipped or xfailed: a skipped test would stop saying anything about a
# door that is closed by two independent mechanisms.


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["net-mutual-debts", "cap-debts-to-trust-limits"])
async def test_an_uninstrumented_repair_is_refused_before_it_writes_anything(
    repairs_opened,
    db_session,
    operation: str,
) -> None:
    """The flag is open, the repair runs, and the journal refuses it at the first flush."""
    is_net = operation == "net-mutual-debts"
    code = "NET" if is_net else "CAP"
    debtor, creditor, equivalent = await _seed_participants_and_equivalent(
        db_session,
        suffix="net" if is_net else "cap",
        code=code,
    )
    rows = (
        [
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=equivalent.id,
                amount=Decimal("10"),
            ),
            Debt(
                debtor_id=creditor.id,
                creditor_id=debtor.id,
                equivalent_id=equivalent.id,
                amount=Decimal("3"),
            ),
        ]
        if is_net
        else [
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=equivalent.id,
                amount=Decimal("80"),
            ),
        ]
    )
    if not is_net:
        db_session.add(
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=equivalent.id,
                limit=Decimal("50"),
                status="active",
            )
        )
        await db_session.flush()
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(rows)
    await db_session.commit()
    equivalent_id = equivalent.id

    before = await _stored_debts(db_session, equivalent_id)
    action = f"admin.integrity.repair.{operation.replace('-', '_')}"

    PaymentRouter._graph_cache[code] = _cache_value()
    PaymentRouter._topology_cache[code] = {"sentinel": {"value"}}
    try:
        # NON-VACUITY: the repair really had something to repair. Without this the refusal below
        # could be a repair that found nothing and flushed nothing.
        assert before, "stand: no debts were seeded, so the repair would touch nothing"

        repair = (
            repair_net_mutual_debts if is_net else repair_cap_debts_to_trust_limits
        )
        with pytest.raises(DebtJournalError) as refusal:
            await repair(
                request=_repair_request(operation), db=db_session, _admin=None
            )
        await db_session.rollback()

        # VERDICT: refused by the JOURNAL, for the reason that names the defect - this writer
        # declared no operation - and not by a constraint that happens to fire.
        assert refusal.value.reason == Reason.NO_OPERATION, refusal.value

        # And it was refused before it did anything else.
        assert await _stored_debts(db_session, equivalent_id) == before
        assert await _admin_audit(db_session, action=action) is None
        assert PaymentRouter._topology_cache.get(code) == {"sentinel": {"value"}}
    finally:
        PaymentRouter.invalidate_cache(code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "failure_type"),
    [
        ("net-mutual-debts", RuntimeError),
        ("cap-debts-to-trust-limits", RuntimeError),
        ("net-mutual-debts", asyncio.CancelledError),
        ("cap-debts-to-trust-limits", asyncio.CancelledError),
    ],
)
async def test_the_refusal_precedes_the_injected_pre_commit_failure(
    repairs_opened,
    db_session,
    operation: str,
    failure_type: type[BaseException],
) -> None:
    """The journal refuses first, so the audit failure this test injects never gets to fire.

    The rollback assertions are the ones the success-path version made, unchanged: nothing about the
    debts moves, no admin audit row appears, and the topology cache keeps its sentinel. What the
    parametrisation now measures is ORDER - whichever failure was injected, the one that actually
    happens is the journal's, and the listener records that it was never reached.

    `RuntimeError` is kept in the list deliberately even though `DebtJournalError` IS a
    `RuntimeError`: the listener's counter is what separates "the injected failure fired" from "the
    journal refused", so the two cannot be confused by their type.
    """
    is_net = operation == "net-mutual-debts"
    code = "RNF" if is_net else "RCF"
    debtor, creditor, equivalent = await _seed_participants_and_equivalent(
        db_session,
        suffix="rollback-net" if is_net else "rollback-cap",
        code=code,
    )
    if is_net:
        async with debt_fixture_setup(db_session, label="setup-1"):
            db_session.add_all(
                [
                    Debt(
                        debtor_id=debtor.id,
                        creditor_id=creditor.id,
                        equivalent_id=equivalent.id,
                        amount=Decimal("10"),
                    ),
                    Debt(
                        debtor_id=creditor.id,
                        creditor_id=debtor.id,
                        equivalent_id=equivalent.id,
                        amount=Decimal("3"),
                    ),
                ]
            )
    else:
        async with debt_fixture_setup(db_session, label="setup-2"):
            db_session.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor.id,
                        to_participant_id=debtor.id,
                        equivalent_id=equivalent.id,
                        limit=Decimal("50"),
                        status="active",
                    ),
                    Debt(
                        debtor_id=debtor.id,
                        creditor_id=creditor.id,
                        equivalent_id=equivalent.id,
                        amount=Decimal("80"),
                    ),
                ]
            )
    await db_session.commit()
    equivalent_id = equivalent.id
    before = await _stored_debts(db_session, equivalent_id)
    # NON-VACUITY, as in the test above: without seeded debts "nothing moved" below is true of a
    # repair that found nothing. This test had no such check, and on PostgreSQL in mode A it passed
    # on exactly that: the second session saw no debts before and none after (017 stage 2b).
    assert before, "stand: no debts were seeded, so the repair would touch nothing"

    action = f"admin.integrity.repair.{operation.replace('-', '_')}"
    injected: list[int] = []

    def fail_when_admin_audit_is_flushed(session, _flush_context, _instances):
        if any(
            isinstance(item, AuditLog) and item.action == action
            for item in session.new
        ):
            injected.append(1)
            raise failure_type("required repair audit failed")

    PaymentRouter._graph_cache[code] = _cache_value()
    PaymentRouter._topology_cache[code] = {"sentinel": {"value"}}
    event.listen(
        db_session.sync_session,
        "before_flush",
        fail_when_admin_audit_is_flushed,
    )
    try:
        repair = (
            repair_net_mutual_debts if is_net else repair_cap_debts_to_trust_limits
        )
        with pytest.raises(DebtJournalError) as refusal:
            await repair(
                request=_repair_request(operation), db=db_session, _admin=None
            )
        await db_session.rollback()
    finally:
        event.remove(
            db_session.sync_session,
            "before_flush",
            fail_when_admin_audit_is_flushed,
        )

    # VERDICT: the journal's refusal, and it arrived first.
    assert refusal.value.reason == Reason.NO_OPERATION, refusal.value
    assert injected == [], (
        f"the injected {failure_type.__name__} fired, so the repair got as far as building its "
        f"admin audit row and this test is no longer measuring the order of the two failures"
    )

    # The rollback assertions, unchanged.
    assert await _stored_debts(db_session, equivalent_id) == before
    assert await _admin_audit(db_session, action=action) is None
    assert code in PaymentRouter._graph_cache
    assert PaymentRouter._topology_cache.get(code) == {"sentinel": {"value"}}
    PaymentRouter.invalidate_cache(code)
