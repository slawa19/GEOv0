"""T1524, the SQLite half: deleting an equivalent must never delete obligations.

The full account is in the PostgreSQL half,
`tests/integration/test_p015_t1524_equivalent_deletion_keeps_obligations_postgres.py`. In short:
`debts.equivalent_id` was `ondelete='CASCADE'`, so deleting an equivalent removed its debts inside
the database, with no Debt row ever loaded - the one production path the phase B flush-listener
journal cannot observe. The route guards it with a usage count, which turns it into a race: a debt
created after the count and before the commit is destroyed.

WHY THIS HALF EXISTS AT ALL. Until 2026-09-11 it could not: the SQLite test engine did not enforce
foreign keys, so every assertion below would have passed under CASCADE, RESTRICT or no constraint.
The default tier is the one that runs on every change, and the phase B contract requires functional
coverage there as well as PostgreSQL acceptance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.api.v1.admin as admin_api
from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

from tests.debt_setup import debt_fixture_setup


async def _seed(db_session, *, with_debt: bool):
    nonce = uuid.uuid4().hex[:8]
    eq = Equivalent(code=("S" + nonce).upper()[:16], description="T1524", precision=2, is_active=False)
    debtor = Participant(pid="sd" + nonce, display_name="D", public_key="pksd-" + nonce)
    creditor = Participant(pid="sc" + nonce, display_name="C", public_key="pksc-" + nonce)
    db_session.add_all([eq, debtor, creditor])
    await db_session.flush()
    debt = None
    if with_debt:
        async with debt_fixture_setup(db_session, label="setup"):
            debt = Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal("42.00000000"),
            )
            db_session.add(debt)
    await db_session.commit()
    return eq, debt


async def _debt_count(db_session, eq_id) -> int:
    rows = (await db_session.execute(select(Debt.id).where(Debt.equivalent_id == eq_id))).all()
    return len(rows)


@pytest.mark.asyncio
async def test_the_database_refuses_to_delete_an_equivalent_that_carries_debt(db_session) -> None:
    """RED before T1524: the cascade removes the debt and nothing refuses."""
    eq, _debt = await _seed(db_session, with_debt=True)
    eq_id = eq.id

    await db_session.delete(eq)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    assert await _debt_count(db_session, eq_id) == 1, (
        "the equivalent's deletion cascaded its debt away inside the database"
    )


@pytest.mark.asyncio
async def test_the_route_refuses_when_its_usage_count_misses_a_debt(
    client, db_session, monkeypatch
) -> None:
    """RED before T1524: the race, with the count made to miss a debt that exists."""
    eq, _debt = await _seed(db_session, with_debt=True)
    eq_id, code = eq.id, eq.code

    async def _count_that_missed_the_debt(db, *, equivalent_id):
        return {"trustlines": 0, "debts": 0, "integrity_checkpoints": 0}

    monkeypatch.setattr(admin_api, "_equivalent_usage_counts", _count_that_missed_the_debt)

    resp = await client.request(
        "DELETE",
        f"/api/v1/admin/equivalents/{code}",
        json={"reason": "T1524 race reproducer"},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )

    assert await _debt_count(db_session, eq_id) == 1, (
        f"the route answered {resp.status_code} and the debt is gone"
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_an_unused_equivalent_still_deletes(client, db_session) -> None:
    """Control: RESTRICT must not turn every deletion into a refusal."""
    eq, _debt = await _seed(db_session, with_debt=False)
    resp = await client.request(
        "DELETE",
        f"/api/v1/admin/equivalents/{eq.code}",
        json={"reason": "T1524 control"},
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )
    assert resp.status_code == 200, resp.text
