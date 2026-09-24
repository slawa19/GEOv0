"""Programme 019, `T1902`, hypothesis (a): a RETRYABLE conflict of the API path is stored as ABORTED.

THE HYPOTHESIS (spec, Problem; read from `app/core/payments/service.py:1003-1031`): when the engine's
unit of work gives up on a serialization failure, the service classifies it as
`RetryablePaymentConflictException` (`409 E008`, `retryable: true`) - and then still terminalizes the
payment with `engine.abort(..., commit=True)`. A client that does what `retryable: true` tells it and
resubmits the same signed `tx_id` is then answered from the stored `ABORTED` row instead of being
executed (`docs/ru/09-decisions-and-defaults.md:343-350` promises a retry).

THE CONFLICTS ARE REAL, one per phase, and nothing is injected into the driver:

* `prepare` - the most ordinary production shape: the SAME sender pays someone else at the same time.
  Each attempt of the subject's prepare takes its SERIALIZABLE snapshot, then queues on the equivalent
  owner lock behind a competing payment (Alice -> Carol) that holds it; the competitor's prepare reads
  and inserts Alice's reservations and commits; the subject then reads Alice's reservations from its
  older snapshot and inserts its own. That is a read-write cycle over `prepare_locks`, and PostgreSQL
  refuses the subject with `40001`. One competitor per attempt keeps the contention up until the
  engine's own retry budget (`COMMIT_RETRY_ATTEMPTS`, default) is spent.
* `commit` - the stop guard's `FOR SHARE` on the equivalent row (`engine.py:1442`) after a concurrent
  UPDATE of that row (its `description`, so the stop never becomes true) committed behind the commit
  phase's snapshot: `40001` on every attempt, as in the T1544 boundedness stand.

CONTROLS, asserted normally: the database error behind the engine's failure carries SQLSTATE `40001`;
the engine logged a `40001` retry for each attempt but the last; every competitor committed (producer
progress); the client got `409 E008` with `retryable: true`; a fresh `tx_id` afterwards pays, so the
path is not simply broken.

TWO TESTS PER PHASE.
* `test_today_...` - CHARACTERIZATION of the hypothesis: the row is `ABORTED` with the retryable error,
  and the resubmission returns it without executing. Stage 3 rewrites it.
* `test_a_retryable_...` - TARGET: no row after the conflict, the resubmission executes and commits.
  `TargetMismatch` after the controls; `xfail(strict)` until stage 3.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from sqlalchemy import text, update

from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.engine import PaymentEngine
from app.core.payments.service import PaymentService, _payment_db_sqlstate
from app.db.models.equivalent import Equivalent
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    ApiWorld,
    api,
    backend_waits_on_advisory,
    build_api_world,
    debts,
    factory,
    finish,
    payment_body,
    tx_row,
)
from tests.p019_support import require_target, target_xfail

PHASES = ["prepare", "commit"]


@dataclass
class _Stand:
    phase: str
    subject_tx: str
    sqlstates: list[str | None] = field(default_factory=list)
    competitors: list[asyncio.Task] = field(default_factory=list)
    touches: int = 0


def _install_prepare_conflict(monkeypatch, factory, world: ApiWorld, stand: _Stand) -> None:  # noqa: F811
    """One competing payment Alice -> Carol per attempt of the subject's prepare (see docstring)."""

    gates: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}
    original_prepare = PaymentEngine.prepare
    original_owner = MoneyBoundary._acquire_equivalent_owner_locks
    original_tx_lock = MoneyBoundary._acquire_tx_advisory_lock

    async def prepare(self, tx_id, *args, **kwargs):
        if tx_id != stand.subject_tx:
            return await original_prepare(self, tx_id, *args, **kwargs)
        self._p019_subject_prepare = True
        try:
            return await original_prepare(self, tx_id, *args, **kwargs)
        except BaseException as exc:
            stand.sqlstates.append(_payment_db_sqlstate(exc))
            raise
        finally:
            self._p019_subject_prepare = False

    async def competitor_pays(tx_id: str) -> str:
        async with factory() as session:
            result = await PaymentService(session).create_payment_internal(
                world.ids[world.alice["pid"]],
                to_pid=world.carol["pid"],
                equivalent=world.code,
                amount="1.00",
                idempotency_key=tx_id,
            )
        return result.status

    async def owner_locks(self, equivalent_ids):
        if not getattr(self, "_p019_subject_prepare", False):
            return await original_owner(self, equivalent_ids)
        # One competitor at a time, each run to its end before the next starts: competitors that
        # overlap each other conflict AMONG THEMSELVES (measured 2026-09-25: a competitor's routing
        # transaction was cancelled as an SSI pivot against the previous competitor's commit phase),
        # which is contention too, but not the one this stand is about.
        for previous in stand.competitors:
            await asyncio.wait_for(asyncio.shield(previous), timeout=30)
        competitor_tx = str(uuid.uuid4())
        holding, release = asyncio.Event(), asyncio.Event()
        gates[competitor_tx] = (holding, release)
        stand.competitors.append(asyncio.create_task(competitor_pays(competitor_tx)))
        await asyncio.wait_for(holding.wait(), timeout=20)
        # The subject's snapshot is taken HERE, while the competitor holds the owner lock and has
        # written nothing yet; the owner-lock wait below then outlasts the competitor's prepare commit.
        pid = await self.session.scalar(text("SELECT pg_backend_pid()"))

        async def release_once_queued():
            assert await backend_waits_on_advisory(factory, pid), (
                "premise: the subject did not queue on the competitor's owner lock"
            )
            release.set()

        releaser = asyncio.create_task(release_once_queued())
        try:
            return await original_owner(self, equivalent_ids)
        finally:
            await releaser

    async def tx_lock(self, tx_id):
        gate = gates.pop(tx_id, None)
        if gate is not None:
            holding, release = gate
            holding.set()
            await asyncio.wait_for(release.wait(), timeout=20)
        return await original_tx_lock(self, tx_id)

    monkeypatch.setattr(PaymentEngine, "prepare", prepare)
    monkeypatch.setattr(MoneyBoundary, "_acquire_equivalent_owner_locks", owner_locks)
    monkeypatch.setattr(MoneyBoundary, "_acquire_tx_advisory_lock", tx_lock)


def _install_commit_conflict(monkeypatch, factory, world: ApiWorld, stand: _Stand) -> None:  # noqa: F811
    """A concurrent UPDATE of the equivalent row behind each commit attempt's snapshot."""

    original_commit = PaymentEngine.commit
    original_guard = MoneyBoundary.refuse_inactive_equivalents

    async def commit(self, tx_id, *args, **kwargs):
        if tx_id != stand.subject_tx:
            return await original_commit(self, tx_id, *args, **kwargs)
        self._p019_subject_commit = True
        try:
            return await original_commit(self, tx_id, *args, **kwargs)
        except BaseException as exc:
            stand.sqlstates.append(_payment_db_sqlstate(exc))
            raise
        finally:
            self._p019_subject_commit = False

    async def guard(self, equivalent_ids, *, row_lock):
        if row_lock and getattr(self, "_p019_subject_commit", False):
            stand.touches += 1
            async with factory() as other:
                await other.execute(
                    update(Equivalent)
                    .where(Equivalent.id == world.equivalent_id)
                    .values(description=f"p019-touch-{stand.touches}")
                )
                await other.commit()
        return await original_guard(self, equivalent_ids, row_lock=row_lock)

    monkeypatch.setattr(PaymentEngine, "commit", commit)
    monkeypatch.setattr(MoneyBoundary, "refuse_inactive_equivalents", guard)


@dataclass
class _Outcome:
    world: ApiWorld
    body: dict
    first: object
    row_after_conflict: tuple[str, dict | None] | None
    resubmission: object


async def _conflict_then_resubmit(api, factory, monkeypatch, caplog, phase: str) -> _Outcome:  # noqa: F811
    world = await build_api_world(api, factory)
    body = payment_body(world, world.alice, world.bob, "10.00")
    stand = _Stand(phase, body["tx_id"])
    with monkeypatch.context() as patched:
        if phase == "prepare":
            _install_prepare_conflict(patched, factory, world, stand)
        else:
            _install_commit_conflict(patched, factory, world, stand)
        try:
            with caplog.at_level(logging.WARNING, logger="app.core.payments.engine"):
                first = await asyncio.wait_for(
                    api.post("/api/v1/payments", json=body, headers=world.alice["headers"]),
                    timeout=60,
                )
            competitor_results = [await asyncio.wait_for(t, 30) for t in stand.competitors]
        finally:
            for t in stand.competitors:
                await finish(t)

    # ── controls: the conflict was the one this stand builds, and it was spent in the engine ────
    budget = int(settings.COMMIT_RETRY_ATTEMPTS)
    assert budget >= 2, f"premise: the engine retries nothing ({budget})"
    assert stand.sqlstates == ["40001"], stand.sqlstates
    retries = [
        r.getMessage()
        for r in caplog.records
        if f"event=payment.uow_retry op={phase} " in r.getMessage()
    ]
    assert len(retries) == budget - 1 and all("pgcode=40001" in m for m in retries), retries
    if phase == "prepare":
        assert competitor_results == ["COMMITTED"] * budget, competitor_results
    else:
        assert stand.touches == budget, stand.touches
    assert first.status_code == 409, first.text
    error = first.json()["error"]
    assert error["code"] == "E008", error
    assert error["details"] == {"retryable": True, "conflict_kind": "database_concurrency"}, error
    subject_debt_before_resubmission = (await debts(factory, world)).get(
        (world.alice["pid"], world.bob["pid"])
    )
    assert subject_debt_before_resubmission is None, "the conflicted attempt moved money"

    row_after_conflict = await tx_row(factory, body["tx_id"])

    # The resubmission the 409 invites: the same signed body, no interference any more.
    resubmission = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
    assert resubmission.status_code == 200, resubmission.text

    # The path is not simply broken: a fresh tx_id pays.
    fresh = await api.post(
        "/api/v1/payments",
        json=payment_body(world, world.alice, world.bob, "1.00"),
        headers=world.alice["headers"],
    )
    assert fresh.status_code == 200 and fresh.json()["status"] == "COMMITTED", fresh.text
    return _Outcome(world, body, first, row_after_conflict, resubmission)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
async def test_today_a_retryable_conflict_is_stored_aborted_and_replayed(
    api, factory, monkeypatch, caplog, phase: str  # noqa: F811
) -> None:
    """CHARACTERIZATION - hypothesis (a) as the current tree behaves. Stage 3 rewrites it."""

    out = await _conflict_then_resubmit(api, factory, monkeypatch, caplog, phase)
    retryable_error = {
        "code": "E008",
        "message": "State conflict",
        "details": {"retryable": True, "conflict_kind": "database_concurrency"},
    }
    assert out.row_after_conflict == ("ABORTED", retryable_error), out.row_after_conflict
    stored = out.resubmission.json()
    assert stored["status"] == "ABORTED", stored
    assert stored["error"] == retryable_error, stored
    # The resubmission executed nothing: Alice owes Bob only the fresh control payment.
    assert (await debts(factory, out.world)).get(
        (out.world.alice["pid"], out.world.bob["pid"])
    ) == Decimal("1.00")


@target_xfail("stage 3 (T1905)", "a retryable conflict is stored ABORTED (service.py:1003-1031)")
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
async def test_a_retryable_conflict_leaves_no_row_and_the_resubmission_executes(
    api, factory, monkeypatch, caplog, phase: str  # noqa: F811
) -> None:
    """TARGET (FORK-4): retryable -> nothing stored; the same tx_id is executed on resubmission."""

    out = await _conflict_then_resubmit(api, factory, monkeypatch, caplog, phase)
    owed = (await debts(factory, out.world)).get((out.world.alice["pid"], out.world.bob["pid"]))
    require_target(
        out.row_after_conflict is None
        and out.resubmission.json()["status"] == "COMMITTED"
        and owed == Decimal("11.00"),
        f"after a retryable conflict the row was {out.row_after_conflict!r}; the resubmission "
        f"answered {out.resubmission.json()['status']} and Alice owes Bob {owed}",
    )
