import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.payments.engine import PaymentEngine


pytestmark = pytest.mark.postgres


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    return (
        getattr(orig, "sqlstate", None)
        or getattr(orig, "pgcode", None)
        or getattr(orig, "code", None)
    )


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="P104: reverse directions still use distinct advisory identities",
    strict=True,
)
async def test_reverse_segments_contend_on_one_advisory_resource_postgres(
    db_session,
) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates reciprocal-pair advisory identity")

    from tests.conftest import TestingSessionLocal

    equivalent_id = uuid.uuid4()
    participant_a = uuid.uuid4()
    participant_b = uuid.uuid4()
    forward_map = {"A": participant_a, "B": participant_b}
    reverse_map = {"A": participant_b, "B": participant_a}

    async with TestingSessionLocal() as holder, TestingSessionLocal() as waiter:
        holder_engine = PaymentEngine(holder)
        waiter_engine = PaymentEngine(waiter)
        waiter_engine._advisory_lock_budget_s = 0.2

        try:
            await holder_engine._acquire_segment_advisory_locks(
                equivalent_id=equivalent_id,
                routes=[(["A", "B"], 1)],
                participant_map=forward_map,
            )

            with pytest.raises(DBAPIError) as raised:
                await waiter_engine._acquire_segment_advisory_locks(
                    equivalent_id=equivalent_id,
                    routes=[(["A", "B"], 1)],
                    participant_map=reverse_map,
                )

            assert _sqlstate(raised.value) == "55P03"
        finally:
            await waiter.rollback()
            await holder.rollback()
