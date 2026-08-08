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
from app.utils.request_id import validate_request_id
from tests.conftest import TestingSessionLocal


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


@pytest.mark.asyncio
async def test_net_mutual_debts_commits_admin_audit_without_cache_invalidation(
    client,
    db_session,
) -> None:
    debtor, creditor, equivalent = await _seed_participants_and_equivalent(
        db_session,
        suffix="net",
        code="NET",
    )
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
    await db_session.commit()
    equivalent_id = equivalent.id
    debtor_id = debtor.id

    PaymentRouter._graph_cache["NET"] = _cache_value()
    PaymentRouter._topology_cache["NET"] = {"sentinel": {"value"}}
    try:
        incoming_request_id = "malformed request id!"
        response = await client.post(
            "/api/v1/integrity/repair/net-mutual-debts",
            headers={**_admin_headers(), "X-Request-ID": incoming_request_id},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "ok": True,
            "action": "net-mutual-debts",
            "netted_pairs": 1,
            "updated": 1,
            "deleted": 1,
        }
        response_request_id = response.headers["X-Request-ID"]
        assert response_request_id != incoming_request_id
        assert validate_request_id(response_request_id) == response_request_id
        assert len(response_request_id) <= 64

        async with TestingSessionLocal() as durable_session:
            debts = (
                await durable_session.execute(
                    select(Debt).where(Debt.equivalent_id == equivalent_id)
                )
            ).scalars().all()
            audit = await _admin_audit(
                durable_session,
                action="admin.integrity.repair.net_mutual_debts",
            )

        assert len(debts) == 1
        assert debts[0].debtor_id == debtor_id
        assert debts[0].amount == Decimal("7")
        assert audit is not None
        assert audit.actor_role == "admin"
        assert audit.object_type == "integrity"
        assert audit.object_id is None
        assert audit.request_id == response_request_id
        assert audit.after_state == {
            "affected_equivalents": ["NET"],
            "netted_pairs": 1,
            "updated": 1,
            "deleted": 1,
        }
        assert "NET" in PaymentRouter._graph_cache
        assert "NET" in PaymentRouter._topology_cache
    finally:
        PaymentRouter.invalidate_cache("NET")


@pytest.mark.asyncio
async def test_cap_debts_commits_admin_audit_and_invalidates_only_affected_codes(
    client,
    db_session,
) -> None:
    debtor, creditor, equivalent = await _seed_participants_and_equivalent(
        db_session,
        suffix="cap",
        code="CAP",
    )
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

    PaymentRouter._graph_cache["CAP"] = _cache_value()
    PaymentRouter._topology_cache["CAP"] = {"sentinel": {"value"}}
    PaymentRouter._graph_cache["SAFE"] = _cache_value()
    PaymentRouter._topology_cache["SAFE"] = {"sentinel": {"value"}}
    try:
        incoming_request_id = "X" * 65
        response = await client.post(
            "/api/v1/integrity/repair/cap-debts-to-trust-limits",
            headers={**_admin_headers(), "X-Request-ID": incoming_request_id},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "ok": True,
            "action": "cap-debts-to-trust-limits",
            "scanned": 1,
            "updated": 1,
            "deleted": 0,
        }
        response_request_id = response.headers["X-Request-ID"]
        assert response_request_id != incoming_request_id
        assert validate_request_id(response_request_id) == response_request_id
        assert len(response_request_id) <= 64

        async with TestingSessionLocal() as durable_session:
            debt = (
                await durable_session.execute(
                    select(Debt).where(Debt.equivalent_id == equivalent_id)
                )
            ).scalar_one()
            audit = await _admin_audit(
                durable_session,
                action="admin.integrity.repair.cap_debts_to_trust_limits",
            )

        assert debt.amount == Decimal("50")
        assert audit is not None
        assert audit.request_id == response_request_id
        assert audit.after_state == {
            "affected_equivalents": ["CAP"],
            "scanned": 1,
            "updated": 1,
            "deleted": 0,
        }
        assert "CAP" not in PaymentRouter._graph_cache
        assert "CAP" not in PaymentRouter._topology_cache
        assert "SAFE" in PaymentRouter._graph_cache
        assert "SAFE" in PaymentRouter._topology_cache
    finally:
        PaymentRouter.invalidate_cache("CAP")
        PaymentRouter.invalidate_cache("SAFE")


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
async def test_repair_rolls_back_debts_and_audit_on_precommit_failure_or_cancellation(
    client,
    db_session,
    operation: str,
    failure_type: type[BaseException],
) -> None:
    is_net = operation == "net-mutual-debts"
    code = "RNF" if is_net else "RCF"
    debtor, creditor, equivalent = await _seed_participants_and_equivalent(
        db_session,
        suffix="rollback-net" if is_net else "rollback-cap",
        code=code,
    )
    if is_net:
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

    action = f"admin.integrity.repair.{operation.replace('-', '_')}"

    def fail_when_admin_audit_is_flushed(session, _flush_context, _instances):
        if any(
            isinstance(item, AuditLog) and item.action == action
            for item in session.new
        ):
            raise failure_type("required repair audit failed")

    PaymentRouter._graph_cache[code] = _cache_value()
    PaymentRouter._topology_cache[code] = {"sentinel": {"value"}}
    event.listen(
        db_session.sync_session,
        "before_flush",
        fail_when_admin_audit_is_flushed,
    )
    try:
        with pytest.raises(failure_type):
            if failure_type is asyncio.CancelledError:
                request = Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": f"/api/v1/integrity/repair/{operation}",
                        "headers": [],
                    }
                )
                repair = (
                    repair_net_mutual_debts
                    if is_net
                    else repair_cap_debts_to_trust_limits
                )
                await repair(request=request, db=db_session, _admin=None)
            else:
                await client.post(
                    f"/api/v1/integrity/repair/{operation}",
                    headers=_admin_headers(),
                )
    finally:
        event.remove(
            db_session.sync_session,
            "before_flush",
            fail_when_admin_audit_is_flushed,
        )

    debts = (
        await db_session.execute(
            select(Debt)
            .where(Debt.equivalent_id == equivalent_id)
            .order_by(Debt.amount.desc())
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert [debt.amount for debt in debts] == (
        [Decimal("10"), Decimal("3")] if is_net else [Decimal("80")]
    )
    assert await _admin_audit(db_session, action=action) is None
    assert code in PaymentRouter._graph_cache
    assert code in PaymentRouter._topology_cache
    PaymentRouter.invalidate_cache(code)
