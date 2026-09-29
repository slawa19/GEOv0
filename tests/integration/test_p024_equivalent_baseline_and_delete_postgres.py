"""Programme 024, step Sh2, slices `T2412.2` and `T2412.3`, accepted together (Sh2 consultation 2026-09-29).

`T2412.2`. `POST /admin/equivalents` created an equivalent without a reconciliation baseline, so criterion (a)
of the reconciliation was `UNVERIFIABLE` for it forever, silently (F-024-2). The baseline is taken in the
creating transaction: an equivalent that has never had a debt or a journal entry has zero offsets by
construction, so this baseline adopts nothing.

`T2412.3` (R-024-5, corrected 2026-09-28). `DELETE /admin/equivalents/{code}` answered 409 "in use" after the
first integrity run, because integrity checkpoints - reports about the equivalent, `ON DELETE CASCADE` -
were counted as usage. And once `.2` takes a baseline at creation, its `RESTRICT` key would refuse the
delete of even an empty equivalent. The acceptance is the WHOLE chain through the routes: create (with the
baseline of `.2`) -> the scheduled integrity run -> deactivate -> delete -> 200 `AdminDeleteResponse`.

What must NOT weaken: an equivalent with financial data stays undeletable. A baseline that recorded offsets
is such data (`RESTRICT`, fail-closed; tested here), and so are journal entries (covered by
`test_p015_b4_entries_and_money_postgres.py::test_c17_p_...`), debts and trust lines (T1524 tests, usage).

TIER. PostgreSQL, `MODE_B`: the routes and the stand share one clone.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select

from app.config import settings
from app.core.ledger.reconciliation import PASSED, verify_journal_equals_change
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.models.participant import Participant
from app.db.reconciliation_tables import debt_reconciliation_baseline_offsets, debt_reconciliation_baselines
from tests.conftest import MODE_B, sessionmaker_of
from tests.p019_support import TargetMismatch, require_target

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


def target_xfail_024(task: str, what: str):
    return pytest.mark.xfail(raises=TargetMismatch, strict=True, reason=f"024 target, delivered by {task}: {what}")


async def _count(factory, stmt) -> int:
    async with factory() as session:
        return int((await session.execute(stmt)).scalar_one())


async def _equivalent_id(factory, code: str):
    async with factory() as session:
        return (await session.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one_or_none()


def _baselines(equivalent_id):
    columns = debt_reconciliation_baselines.c
    return select(func.count()).select_from(debt_reconciliation_baselines).where(columns.equivalent_id == equivalent_id)


async def _create(client, code: str) -> None:
    response = await client.post(
        "/api/v1/admin/equivalents",
        json={"code": code, "precision": 2, "reason": "p024 T2412"},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


async def _deactivate(client, code: str) -> None:
    response = await client.patch(
        f"/api/v1/admin/equivalents/{code}", json={"is_active": False, "reason": "p024 T2412"}, headers=ADMIN
    )
    assert response.status_code == 200, response.text


async def _delete(client, code: str):
    return await client.request(
        "DELETE", f"/api/v1/admin/equivalents/{code}", json={"reason": "p024 T2412"}, headers=ADMIN
    )


async def _scheduled_integrity_run(monkeypatch, factory) -> None:
    import app.db.session as app_db_session
    import app.core.maintenance_jobs as main_module

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    await main_module._run_integrity_checkpoints_once(app, reason="periodic")


@MODE_B
@pytest.mark.asyncio
async def test_t2412_2_a_created_equivalent_has_a_baseline_and_is_verifiable(client, db_session) -> None:
    """MUTATION: drop the `take_baseline` call from `admin_create_equivalent` - UNVERIFIABLE, red."""

    factory = sessionmaker_of(db_session)
    await _create(client, "P24BA")
    equivalent_id = await _equivalent_id(factory, "P24BA")
    assert equivalent_id is not None

    async with factory() as session:
        outcome = await verify_journal_equals_change(session, equivalent_id)
    baselines = await _count(factory, _baselines(equivalent_id))
    offsets = await _count(
        factory,
        select(func.count())
        .select_from(debt_reconciliation_baseline_offsets)
        .where(debt_reconciliation_baseline_offsets.c.equivalent_id == equivalent_id),
    )

    require_target(
        baselines == 1 and offsets == 0 and outcome.status == PASSED,
        f"baselines={baselines} offsets={offsets} verdict={outcome.status} missing={outcome.missing_evidence}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_r024_5_create_integrity_run_deactivate_delete_is_200(client, db_session, monkeypatch) -> None:
    """R-024-5, the whole chain. MUTATIONS: count checkpoints as usage again - 409 "in use", red; do not
    remove the empty baseline - 409 `referenced_by_existing_rows` once `.2` takes one, red."""

    factory = sessionmaker_of(db_session)
    await _create(client, "P24BB")
    equivalent_id = await _equivalent_id(factory, "P24BB")
    await _scheduled_integrity_run(monkeypatch, factory)
    checkpoints = select(func.count()).select_from(IntegrityCheckpoint).where(
        IntegrityCheckpoint.equivalent_id == equivalent_id
    )
    # Control: the integrity run did write what used to count as usage.
    assert await _count(factory, checkpoints) >= 1
    await _deactivate(client, "P24BB")

    response = await _delete(client, "P24BB")

    gone = await _equivalent_id(factory, "P24BB") is None
    require_target(
        response.status_code == 200 and response.json() == {"deleted": "P24BB"} and gone,
        f"DELETE answered {response.status_code} {response.text}; equivalent gone={gone}",
    )
    assert await _count(factory, _baselines(equivalent_id)) == 0
    assert await _count(factory, checkpoints) == 0


def test_t2412_3_the_canon_and_the_route_declare_the_409() -> None:
    """The `409` the delete always raised is declared on both sides (F-024-16, contract half for this API).

    The drift ratchet in `tests/contract/test_openapi_contract.py` records that the two halves agree; this
    names the status itself. MUTATION: drop either declaration - red.
    """

    from tests.contract.test_openapi_contract import _load_fastapi_openapi, _load_openapi_yaml

    canon = _load_openapi_yaml()["paths"]["/admin/equivalents/{code}"]["delete"]["responses"]
    generated = _load_fastapi_openapi()["paths"]["/api/v1/admin/equivalents/{code}"]["delete"]["responses"]
    assert canon["409"] == {"$ref": "#/components/responses/Conflict"}, canon.get("409")
    assert "409" in generated, sorted(generated)


@MODE_B
@pytest.mark.asyncio
async def test_t2412_3_a_baseline_with_offsets_still_refuses_the_delete(client, db_session) -> None:
    """Financial data stays undeletable: a baseline that ADOPTED debts is evidence, `RESTRICT`, fail-closed.

    Counter-check of the removal of an EMPTY baseline: if the delete removed any baseline, this goes red.
    """

    factory = sessionmaker_of(db_session)
    tag = uuid.uuid4().hex[:8]
    async with factory() as session:
        eq = Equivalent(code="P24BC", precision=2, is_active=False)
        people = [
            Participant(pid=f"p024_{i}_{tag}", display_name=f"p024 {i}", public_key=f"pk_p024_{i}_{tag}",
                        type="person", status="active")
            for i in range(2)
        ]
        session.add_all([eq, *people])
        await session.flush()
        await session.execute(insert(debt_reconciliation_baselines).values(equivalent_id=eq.id))
        await session.execute(
            insert(debt_reconciliation_baseline_offsets).values(
                equivalent_id=eq.id,
                debtor_id=people[0].id,
                creditor_id=people[1].id,
                offset_amount=Decimal("5"),
            )
        )
        await session.commit()
        equivalent_id = eq.id

    response = await _delete(client, "P24BC")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "referenced_by_existing_rows", response.text
    assert await _equivalent_id(factory, "P24BC") == equivalent_id
    assert await _count(factory, _baselines(equivalent_id)) == 1
