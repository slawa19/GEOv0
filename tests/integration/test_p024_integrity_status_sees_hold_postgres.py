"""Programme 024, step Sh2, slice `T2412.1`: the verdict of the debt reconciliation is visible.

R-024-4 (spec `specs/024-core-hygiene/spec.md`, Verification plan 1). The scheduled integrity job hosts the
reconciliation (`app/main.py`, `_run_integrity_checkpoints_once`). Before this slice an ERROR while verifying
one equivalent - or while reacting to its FAILED - was only logged: `run_scheduled_reconciliation` counts it
and returns the counts, nobody read them, and the job recorded `<reason>_success`, so `/health` stayed `ok`.

THE PATH IS THE REAL ONE: the real host, the real `run_scheduled_reconciliation`, a real equivalent on a
disposable clone of the tier database. Only the one call whose failure is the subject is replaced, and each
test asserts that the replacement was actually reached (a stand that never raised proves nothing).

R-024-3. `GET /integrity/status` computed its own health from trust limits and debt symmetry and read
neither the integrity hold nor the stored reconciliation result: an equivalent under a hold was `healthy`.
The mapping is the binding decision of the Sh2 consultation (2026-09-29, recorded in the spec): the existing
`status` and `alerts` carry it, severity is the maximum, the latest row is read by `is_latest`, and nothing
is reconciled on a GET. The result rows and the hold are written directly - the subject is what the read
reports, not how a verdict arises (steps 5a-5c test that).

TIER. PostgreSQL: R-024-4 through `tier_on_a_clone` (the host commits through sessions of its own), R-024-3
through `MODE_B` (the route and the stand share one clone).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select, update

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import FAILED, ReconciliationOutcome
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.reconciliation_tables import debt_reconciliation_results
from app.config import settings
from app.core.ledger.reconciliation import PASSED, UNVERIFIABLE
from app.utils.background_jobs import background_health_status
from tests.conftest import MODE_B, sessionmaker_of
from tests.p019_support import TargetMismatch, require_target
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture


def target_xfail_024(task: str, what: str):
    """An expected `TargetMismatch`, strict: the task that delivers the target takes the marker off."""

    return pytest.mark.xfail(raises=TargetMismatch, strict=True, reason=f"024 target, delivered by {task}: {what}")


async def _equivalent(factory, code: str) -> uuid.UUID:
    async with factory() as session:
        eq = Equivalent(code=code, symbol=code, description="p024 T2412.1", precision=2, is_active=True)
        session.add(eq)
        await session.commit()
        return eq.id


async def _run_the_host(monkeypatch, factory) -> SimpleNamespace:
    """`app.main._run_integrity_checkpoints_once` on the clone, as the integrity loop calls it."""

    import app.db.session as app_db_session
    import app.main as main_module

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    app.completed = await main_module._run_integrity_checkpoints_once(app, reason="periodic")
    return app


async def _checkpoints(factory, equivalent_id) -> int:
    async with factory() as session:
        return len(
            (
                await session.execute(
                    select(IntegrityCheckpoint.id).where(IntegrityCheckpoint.equivalent_id == equivalent_id)
                )
            ).all()
        )


async def _latest_status(factory, equivalent_id) -> str | None:
    columns = debt_reconciliation_results.c
    async with factory() as session:
        return (
            await session.execute(
                select(columns.status).where(
                    columns.equivalent_id == equivalent_id, columns.is_latest.is_(True)
                )
            )
        ).scalar_one_or_none()


def _job(app) -> dict:
    return app.state.background_jobs["integrity"]


def _require_failed_job(app) -> None:
    job = _job(app)
    require_target(
        job.get("status") == "failed"
        and job.get("event") == "periodic_debt_reconciliation_error"
        and background_health_status(app) == "degraded"
        and app.completed is False,
        f"the job must be failed with `periodic_debt_reconciliation_error` and health degraded: "
        f"job={job} health={background_health_status(app)} completed={app.completed}",
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_r024_4_a_verifier_error_on_one_equivalent_is_not_a_success(monkeypatch) -> None:
    """The verifier raises for ONE equivalent: the job is failed and `/health` is degraded.

    MUTATION: in `app/main.py` drop the check of the returned counts - the job reads `periodic_success`
    and health `ok` again, red.
    """

    from tests.conftest import TestingSessionLocal as factory

    target = await _equivalent(factory, "P24RA")
    reached: list[uuid.UUID] = []
    original = reconciliation.verify_journal_equals_change

    async def verify(session, equivalent_id):
        if equivalent_id == target:
            reached.append(equivalent_id)
            raise RuntimeError("p024 R-024-4: the verifier fails on this equivalent")
        return await original(session, equivalent_id)

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", verify)

    app = await _run_the_host(monkeypatch, factory)

    # Controls: the error was really raised on the real path, the checkpoints committed, no result row.
    assert reached == [target], reached
    assert await _checkpoints(factory, target) == 1
    assert await _latest_status(factory, target) is None

    _require_failed_job(app)


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_r024_4_a_failed_reaction_is_not_a_success(monkeypatch) -> None:
    """A FAILED verdict whose hold reaction raises (`hold_errors`): the job is failed, not a success.

    The verdict is a fabricated FAILED outcome (the subject is what the host does with a reaction error,
    not how a FAILED arises - that is step 5a/5c's). MUTATION: count only `error` and not `hold_errors` in
    `app/main.py` - red.
    """

    from tests.conftest import TestingSessionLocal as factory

    target = await _equivalent(factory, "P24RB")
    original = reconciliation.verify_journal_equals_change
    reactions: list[uuid.UUID] = []

    async def verify(session, equivalent_id):
        if equivalent_id == target:
            return ReconciliationOutcome(
                equivalent_id=target,
                findings=({"kind": "p024_stand", "equivalent_id": str(target)},),
                missing_evidence=(),
                edges_checked=0,
                entries_read=0,
            )
        return await original(session, equivalent_id)

    async def react(session_factory, equivalent_id):
        reactions.append(equivalent_id)
        raise RuntimeError("p024 R-024-4: the hold reaction fails")

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", verify)
    monkeypatch.setattr(reconciliation, "react_to_failed", react)

    app = await _run_the_host(monkeypatch, factory)

    assert reactions == [target], reactions
    assert await _latest_status(factory, target) == FAILED

    _require_failed_job(app)


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_r024_4_a_later_clean_run_recovers_the_job(monkeypatch) -> None:
    """The degradation is a state of the LAST run, not a latch: the next clean run reads `periodic_success`.

    Counter-check of the two tests above - a host that fails every run would pass them.
    """

    from tests.conftest import TestingSessionLocal as factory

    target = await _equivalent(factory, "P24RC")
    original = reconciliation.verify_journal_equals_change

    async def verify(session, equivalent_id):
        if equivalent_id == target:
            raise RuntimeError("p024 R-024-4: the verifier fails once")
        return await original(session, equivalent_id)

    with monkeypatch.context() as scoped:
        scoped.setattr(reconciliation, "verify_journal_equals_change", verify)
        failed = await _run_the_host(monkeypatch, factory)
    assert _job(failed)["status"] == "failed", failed.state.background_jobs

    app = await _run_the_host(monkeypatch, factory)
    assert _job(app) == {"status": "running", "event": "periodic_success"}, app.state.background_jobs
    assert background_health_status(app) == "ok"
    assert app.completed is True
    assert await _latest_status(factory, target) is not None


# ==============================================================================================
# R-024-3: the stored verdict and the hold, as `GET /integrity/status` reports them
# ==============================================================================================

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _result(factory, equivalent_id, status: str, *, latest: bool = True, missing=()) -> uuid.UUID:
    """STAND: one stored result row. A new latest row demotes the previous one, as `record_outcome` does."""

    result_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    columns = debt_reconciliation_results.c
    async with factory() as session:
        if latest:
            await session.execute(
                update(debt_reconciliation_results)
                .where(columns.equivalent_id == equivalent_id, columns.is_latest.is_(True))
                .values(is_latest=False)
            )
        await session.execute(
            insert(debt_reconciliation_results).values(
                id=result_id,
                equivalent_id=equivalent_id,
                status=status,
                fingerprint=result_id.hex * 2,
                detail={"stand": "p024 R-024-3", "missing_evidence": list(missing)},
                checked_at=now,
                last_checked_at=now,
                is_latest=latest,
            )
        )
        await session.commit()
    return result_id


async def _hold(factory, equivalent_id, result_id) -> None:
    async with factory() as session:
        await session.execute(
            update(Equivalent).where(Equivalent.id == equivalent_id).values(integrity_hold_result_id=result_id)
        )
        await session.commit()


async def _status(client) -> dict:
    response = await client.get("/api/v1/integrity/status", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _alerts_of(payload: dict, code: str) -> list[str]:
    return [alert for alert in payload["alerts"] if code in alert]


def _require(payload: dict, code: str, *, status: str, needles: list[str], forbidden: tuple = ()) -> None:
    entry = payload["equivalents"][code]
    alerts = _alerts_of(payload, code)
    text = " | ".join(alerts)
    rank = {"healthy": 0, "warning": 1, "critical": 2}
    require_target(
        entry["status"] == status
        and rank[payload["status"]] >= rank[status]
        and all(needle in text for needle in needles)
        and not any(word in text for word in forbidden),
        f"{code}: expected status {status} with alerts naming {needles} and not {list(forbidden)}; "
        f"got entry status {entry['status']}, overall {payload['status']}, alerts {alerts}",
    )


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_a_held_equivalent_is_critical_and_names_the_hold(client, db_session) -> None:
    """R-024-3 as the spec defines it. MUTATION: skip the hold branch - `healthy`, red."""

    factory = sessionmaker_of(db_session)
    eq = await _equivalent(factory, "P24HA")
    failed = await _result(factory, eq, FAILED)
    await _hold(factory, eq, failed)

    payload = await _status(client)
    assert "P24HA" in payload["equivalents"], payload
    _require(payload, "P24HA", status="critical", needles=["hold", str(failed)])


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_no_stored_result_is_a_warning_and_not_a_verdict(client, db_session) -> None:
    """An equivalent with no result row: a gap of the check, not FAILED and not UNVERIFIABLE.

    MUTATION: treat a missing row as PASSED - `healthy`, red.
    """

    factory = sessionmaker_of(db_session)
    await _equivalent(factory, "P24HB")

    payload = await _status(client)
    _require(
        payload,
        "P24HB",
        status="warning",
        needles=["reconciliation result", "missing"],
        forbidden=(FAILED, UNVERIFIABLE, "hold"),
    )


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_a_failed_result_without_a_hold_is_critical(client, db_session) -> None:
    """MUTATION: map FAILED to `warning` - red."""

    factory = sessionmaker_of(db_session)
    eq = await _equivalent(factory, "P24HC")
    failed = await _result(factory, eq, FAILED)

    payload = await _status(client)
    _require(payload, "P24HC", status="critical", needles=[FAILED, str(failed)], forbidden=("hold", "refused"))


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_an_unverifiable_result_is_a_warning_with_its_missing_evidence(client, db_session) -> None:
    """MUTATION: drop `missing_evidence` from the alert - red."""

    factory = sessionmaker_of(db_session)
    eq = await _equivalent(factory, "P24HD")
    unverifiable = await _result(factory, eq, UNVERIFIABLE, missing=["baseline"])

    payload = await _status(client)
    _require(payload, "P24HD", status="warning", needles=[UNVERIFIABLE, str(unverifiable), "baseline"])


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_a_later_passed_does_not_hide_the_hold(client, db_session) -> None:
    """The hold stays until an admin clears it; the later PASSED is what permits that, and both are shown.

    MUTATION: let the latest verdict decide alone - `healthy` under a hold, red.
    """

    factory = sessionmaker_of(db_session)
    eq = await _equivalent(factory, "P24HE")
    failed = await _result(factory, eq, FAILED)
    await _hold(factory, eq, failed)
    passed = await _result(factory, eq, PASSED)

    payload = await _status(client)
    _require(payload, "P24HE", status="critical", needles=["hold", str(failed), PASSED, str(passed)])


@MODE_B
@pytest.mark.asyncio
async def test_r024_3_a_passed_result_without_a_hold_raises_nothing(client, db_session) -> None:
    """Counter-check: the mapping does not degrade every equivalent. A PASSED adds no alert and no severity."""

    factory = sessionmaker_of(db_session)
    eq = await _equivalent(factory, "P24HF")
    await _result(factory, eq, FAILED, latest=False)
    await _result(factory, eq, PASSED)

    payload = await _status(client)
    assert payload["equivalents"]["P24HF"]["status"] == "healthy", payload["equivalents"]["P24HF"]
    assert _alerts_of(payload, "P24HF") == [], payload["alerts"]
