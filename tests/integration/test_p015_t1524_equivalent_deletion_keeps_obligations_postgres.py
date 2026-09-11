"""T1524: deleting an equivalent must never delete obligations.

WHERE THE DEBT GOES TODAY. `app/api/v1/admin.py` `admin_delete_equivalent` refuses an active
equivalent, counts its usage (trustlines, debts, integrity checkpoints) and refuses if any count is
non-zero - then calls `db.delete(eq)`. The debts are not removed by that code. They are removed by
the DATABASE: `debts.equivalent_id` is declared `ForeignKey('equivalents.id', ondelete='CASCADE')`
on the model and as `fk_debts_equivalent_id ... ondelete="CASCADE"` in migration 005. No `Debt`
instance is ever loaded, so no application code, no audit row and no future journal hook sees a
single obligation disappear - which is also why this is the one production path the phase B
flush-listener journal cannot observe, and why it must be closed rather than instrumented.

The usage count makes it a race rather than a certainty: a debt created between the count and the
commit is deleted with the equivalent. The race is modelled EXACTLY - by making the count miss a debt
that exists - rather than by timing two requests and hoping they interleave.

WHY BOTH TIERS. This module is the PostgreSQL half; `tests/unit/test_p015_t1524_equivalent_
deletion_keeps_obligations.py` is the SQLite half. Writing it exposed that the SQLite test engine had
never enforced foreign keys at all - the application engine sets `PRAGMA foreign_keys=ON`
(`app/db/session.py:48`), the test engine did not - so until that was fixed a SQLite version would
have passed under CASCADE, RESTRICT or no constraint at all. PostgreSQL always enforced them, and its
RESTRICT and advisory-lock semantics are the ones production runs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

import app.api.v1.admin as admin_api
from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.integration.p012_pg_http import make_pg_client_fixture

pytestmark = pytest.mark.postgres

pg_client = make_pg_client_fixture()


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _seed(*, with_debt: bool):
    """An INACTIVE equivalent (deletion requires it) and, optionally, one debt in it."""
    from tests.conftest import TestingSessionLocal, _ensure_schema_initialized

    await _ensure_schema_initialized()
    nonce = uuid.uuid4().hex[:8]
    async with TestingSessionLocal() as s:
        eq = Equivalent(
            code=("R" + nonce).upper()[:16], description="T1524", precision=2, is_active=False
        )
        debtor = Participant(pid="rd" + nonce, display_name="D", public_key="pkrd-" + nonce)
        creditor = Participant(pid="rc" + nonce, display_name="C", public_key="pkrc-" + nonce)
        s.add_all([eq, debtor, creditor])
        await s.flush()
        debt_id = None
        if with_debt:
            debt = Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal("42.00000000"),
            )
            s.add(debt)
            await s.flush()
            debt_id = debt.id
        await s.commit()
        return eq.id, eq.code, debt_id, (debtor.id, creditor.id)


async def _cleanup(eq_id, participant_ids) -> None:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        await s.execute(delete(Debt).where(Debt.equivalent_id == eq_id))
        await s.execute(delete(Equivalent).where(Equivalent.id == eq_id))
        await s.execute(delete(Participant).where(Participant.id.in_(participant_ids)))
        await s.commit()


async def _debt_exists(debt_id) -> bool:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s:
        return (await s.execute(select(Debt.id).where(Debt.id == debt_id))).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_an_equivalent_that_carries_debt() -> None:
    """The last line of defence, below every application check. RED before T1524.

    Today the FK is CASCADE, so deleting the row succeeds and the 42.00000000 obligation disappears
    with it - no ORM instance, no audit, nothing to observe. The constraint must refuse instead.
    """
    from tests.conftest import TestingSessionLocal

    eq_id, _code, debt_id, participants = await _seed(with_debt=True)
    try:
        refused = False
        async with TestingSessionLocal() as s:
            eq = (await s.execute(select(Equivalent).where(Equivalent.id == eq_id))).scalar_one()
            await s.delete(eq)
            try:
                await s.commit()
            except IntegrityError:
                refused = True
                await s.rollback()

        assert await _debt_exists(debt_id), (
            "deleting the equivalent destroyed the debt it carried: the foreign key cascaded an "
            "obligation away without loading a single Debt row"
        )
        assert refused, "the database accepted deleting an equivalent that still carries debt"
    finally:
        await _cleanup(eq_id, participants)


@pytest.mark.asyncio
async def test_the_route_refuses_when_its_usage_count_misses_a_debt(pg_client, monkeypatch) -> None:
    """The reachable race, modelled exactly. RED before T1524.

    The count is patched to report zero while a debt exists - precisely the state produced when a
    debt is created after the count and before the commit. The route must refuse with the same 409
    it already returns for an equivalent in use, and both rows must survive.
    """
    eq_id, code, debt_id, participants = await _seed(with_debt=True)

    async def _count_that_missed_the_debt(db, *, equivalent_id):
        return {"trustlines": 0, "debts": 0, "integrity_checkpoints": 0}

    monkeypatch.setattr(admin_api, "_equivalent_usage_counts", _count_that_missed_the_debt)
    try:
        resp = await pg_client.request(
            "DELETE",
            f"/api/v1/admin/equivalents/{code}",
            json={"reason": "T1524 race reproducer"},
            headers=_admin_headers(),
        )
        assert await _debt_exists(debt_id), (
            f"the route answered {resp.status_code} and the debt is gone: a count that missed one "
            f"obligation let the database cascade it away"
        )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(eq_id, participants)


@pytest.mark.asyncio
async def test_an_unused_equivalent_still_deletes(pg_client) -> None:
    """Control. RESTRICT must not turn every deletion into a refusal."""
    eq_id, code, _debt_id, participants = await _seed(with_debt=False)
    try:
        resp = await pg_client.request(
            "DELETE",
            f"/api/v1/admin/equivalents/{code}",
            json={"reason": "T1524 control"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(eq_id, participants)
