"""F-015-6 containment: the repair endpoints refuse by default, and touch nothing.

WHY THIS MODULE EXISTS. `POST /integrity/repair/cap-debts-to-trust-limits` builds its limit map
from `TrustLine.status == "active"` only, treats a missing edge as a limit of zero, and DELETES the
debt. A FROZEN line is not active - and freezing a line over its limit *without settling the debt*
is what `docs/ru/02-protocol-spec.md:2040-2044` prescribes and what
`app/core/simulator/inject_executor.py:732-745` does. So the reachable case is not exotic: one
admin click destroys an obligation the system created deliberately, and the audit row keeps only
`scanned/updated/deleted` counters, so the amount destroyed cannot be recovered.

`POST /integrity/repair/net-mutual-debts` does not delete on a frozen line, but it shares the other
half of `F-015-6`: both read `select(Debt)` with no advisory lock, no `FOR UPDATE` and no filter on
active `PrepareLock`, so a repair can land between a payment's PREPARE and COMMIT. That is why both
are closed, not only the one that deletes.

WHAT THIS IS NOT. It is not the fix. `T1511` of programme 015 owns that - a frozen line counted at
its real limit, exact deltas written to `after_state`, and the repairs brought under the locking
discipline the clearing service already uses. This is containment, authorized by the owner
2026-09-11 on the external direction review, and it is written so that reopening the door requires
deleting an assertion rather than forgetting one.

WHAT THE ASSERTIONS ARE FOR. Two things, and the second is the one that matters: the endpoints must
REFUSE, and they must refuse BEFORE reading anything. A repair that reported what it "would" have
changed while refusing to do it would hand an operator a plan to approve - which is the same
destruction one step removed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B, sessionmaker_of

from tests.debt_setup import debt_fixture_setup

_REPAIRS = (
    "/api/v1/integrity/repair/cap-debts-to-trust-limits",
    "/api/v1/integrity/repair/net-mutual-debts",
)


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _seed_debt_behind_a_frozen_line(db_session) -> tuple[Equivalent, Debt]:
    """Exactly the state the protocol prescribes: a line frozen over its limit, debt outstanding."""
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("F" + nonce[:15]).upper(), description="F-015-6", precision=2)
    debtor = Participant(pid="fd" + nonce, display_name="D", public_key="pkfd-" + nonce)
    creditor = Participant(pid="fc" + nonce, display_name="C", public_key="pkfc-" + nonce)
    db_session.add_all([eq, debtor, creditor])
    await db_session.flush()

    db_session.add(
        TrustLine(
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
            equivalent_id=eq.id,
            limit=Decimal("100"),
            # The freeze is the whole point. `status != "active"` is what makes the repair treat
            # the limit as zero and delete the debt entirely.
            status="frozen",
        )
    )
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


# MODE B (017 stage 2b, T1702): the witness is a SEPARATE session, and in mode A on PostgreSQL it
# could not see the uncommitted seed - "the debt behind a frozen line was destroyed" was reported of
# a debt that was never visible to it (stage-2 catalogue, class VIS).
@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("path", _REPAIRS)
async def test_repair_endpoints_refuse_while_the_finding_is_open(
    client: AsyncClient, db_session, path: str
) -> None:
    _eq, debt = await _seed_debt_behind_a_frozen_line(db_session)
    debt_id = debt.id
    amount_before = Decimal(str(debt.amount))

    resp = await client.post(path, headers=_admin_headers())

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "E008", body
    # The refusal names what it is waiting for, so an operator reading the response can find the
    # decision rather than guessing that the service is broken.
    assert body["error"]["details"]["finding"] == "F-015-6"
    assert body["error"]["details"]["task"] == "T1511"
    assert body["error"]["details"]["setting"] == "INTEGRITY_REPAIRS_ENABLED"

    # Read back through a SEPARATE durable session, the way the repair atomicity tests do. The
    # request's own session is not a witness to what survived it.
    async with sessionmaker_of(db_session)() as durable:
        after = (
            await durable.execute(select(Debt).where(Debt.id == debt_id))
        ).scalar_one_or_none()
    assert after is not None, "the debt behind a frozen line was destroyed"
    assert Decimal(str(after.amount)) == amount_before


@pytest.mark.asyncio
async def test_the_default_is_closed_and_not_merely_the_test_environment(
    client: AsyncClient,
) -> None:
    """The counter-proof for the containment: the CLASS default is false, not this run's value.

    Asserting `settings.INTEGRITY_REPAIRS_ENABLED is False` would pass equally if the field
    defaulted to true and something in the test setup happened to switch it off - and would then
    go on passing in production with the door wide open. This reads the model field's declared
    default instead.
    """
    from app.config import Settings

    field = Settings.model_fields["INTEGRITY_REPAIRS_ENABLED"]
    assert field.default is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _REPAIRS)
async def test_the_refusal_happens_before_any_debt_is_read(
    client: AsyncClient, db_session, monkeypatch, path: str
) -> None:
    """A repair that is closed must not report what it would have changed.

    Placing the guard after the scan would produce a refusal carrying `scanned`/`deleted` counts -
    a plan for destroying the same money, one approval away. This asserts the ordering by making
    any read of `Debt` inside the handler fail loudly.
    """
    await _seed_debt_behind_a_frozen_line(db_session)

    import app.api.v1.integrity as integrity_api

    class _Tripwire(Exception):
        pass

    original = integrity_api.select

    def _tripwire(entity, *args, **kwargs):
        if entity is Debt:
            raise _Tripwire("the handler read Debt before refusing")
        return original(entity, *args, **kwargs)

    monkeypatch.setattr(integrity_api, "select", _tripwire)

    resp = await client.post(path, headers=_admin_headers())
    assert resp.status_code == 409, resp.text
