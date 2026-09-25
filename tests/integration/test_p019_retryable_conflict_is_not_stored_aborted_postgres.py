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
  refuses the subject with `40001`. Since 019 stage 4 no reservation is written: the cycle closes over the
  competitor's committed debt against the subject's reads of Alice's positions in its money phase -
  still a real `40001` of the same schedule, recorded wherever in the payment operation it surfaces. Since
  stage 5 (`T1909`) the equivalent lock is SHARED and the subject no longer queues behind the competitor:
  the competitor runs its whole payment while the subject holds the lock (asserted from `pg_locks`), after
  the subject's snapshot and before its first position read - the same read-write cycle, built without a
  lock wait. One competitor per attempt keeps the contention up until the
  retry budget (`COMMIT_RETRY_ATTEMPTS`, default) is spent. Since stage 3 (`T1904`) that budget is
  `PaymentService.pay`'s: every attempt is the WHOLE payment on a fresh session and snapshot, so the
  subject's prepare - and the conflict - happen once per attempt.
* `commit` - the stop guard's `FOR SHARE` on the equivalent row (`engine.py:1442`) after a concurrent
  UPDATE of that row (its `description`, so the stop never becomes true) committed behind the commit
  phase's snapshot: `40001` on every attempt, as in the T1544 boundedness stand.

CONTROLS, asserted normally: the database error behind every attempt's failure carries SQLSTATE
`40001`; `pay()` logged a `40001` retry of the whole attempt for each attempt but the last
(`event=payment.attempt_retry`; before stage 3 the engine retried its own phase,
`event=payment.uow_retry`); every competitor committed (producer progress); the client got `409 E008`
with `retryable: true`; a fresh `tx_id` afterwards pays, so the path is not simply broken.

THE TARGET (FORK-4), PASSING SINCE `T1905`: no row after the exhausted conflict, and the resubmission
executes and commits. `TargetMismatch` after the controls. Until `T1905` a characterization pinned the
hypothesis - the row `ABORTED` with the retryable error, replayed without executing - on prepare AND on
commit (confirmed by `T1902`); it was removed with the behaviour it pinned, and the mutation "record an
exhausted conflict `ABORTED` again" turns this target red (`T1905` changelog).
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
from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
from app.core.payments.service import PaymentService, _payment_db_sqlstate
from app.db.models.equivalent import Equivalent
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    ApiWorld,
    api,
    build_api_world,
    debts,
    factory,
    finish,
    payment_body,
    tx_row,
)
from tests.p019_support import require_target

PHASES = ["prepare", "commit"]


@dataclass
class _Stand:
    phase: str
    subject_tx: str
    sqlstates: list[str | None] = field(default_factory=list)
    competitors: list[asyncio.Task] = field(default_factory=list)
    touches: int = 0
    subject_held_shared: list[bool] = field(default_factory=list)


async def backend_holds_shared_equivalent_lock(factory, pid: int, equivalent_id) -> bool:  # noqa: F811
    """Backend `pid` holds THIS equivalent's lock, granted, in shared mode. Observed on its own connection."""

    async with factory() as observer:
        return bool(
            await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
                    "AND mode = 'ShareLock' AND pid = :pid AND objsubid = 2 "
                    "AND classid::bigint = :namespace AND objid::bigint = :key)"
                ),
                {
                    "pid": pid,
                    "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE & 0xFFFFFFFF,
                    "key": MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF,
                },
            )
        )


def _install_prepare_conflict(monkeypatch, factory, world: ApiWorld, stand: _Stand) -> None:  # noqa: F811
    """One competing payment Alice -> Carol per attempt of the subject's prepare (see docstring)."""

    # 019 stage 4: the binding phase of the direct execution (the engine's prepare before); the flag
    # goes on the service's money boundary, whose lock primitive the stand wraps.
    original_prepare = PaymentService._bind_payment
    original_shared = MoneyBoundary._acquire_shared_equivalent_locks_in_order

    async def prepare(self, tx_id, *args, **kwargs):
        if tx_id != stand.subject_tx:
            return await original_prepare(self, tx_id, *args, **kwargs)
        self._boundary._p019_subject_prepare = True
        try:
            return await original_prepare(self, tx_id, *args, **kwargs)
        finally:
            self._boundary._p019_subject_prepare = False

    # Since 019 stage 4 the subject's binding phase writes no reservation, so the read-write cycle no
    # longer closes over `prepare_locks` inside it: it closes over the DEBTS - the competitor's
    # committed Alice -> Carol debt against the subject's reads of Alice's positions from its older
    # snapshot (the net-position snapshot and the integrity checkpoint of its money phase). The
    # conflict is recorded where it now surfaces: anywhere in the subject's payment operation.
    original_operation = PaymentService._run_payment_operation

    async def operation(self, attempt, **kwargs):
        try:
            return await original_operation(self, attempt, **kwargs)
        except BaseException as exc:
            if attempt.tx_id == stand.subject_tx:
                stand.sqlstates.append(_payment_db_sqlstate(exc))
            raise

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

    async def shared_lock(self, equivalent_ids):
        await original_shared(self, equivalent_ids)
        if not getattr(self, "_p019_subject_prepare", False):
            return
        # 019 stage 5 (`T1909`): the payment's equivalent lock is SHARED, so the competitor no longer
        # holds the subject off - both are inside it at once. The subject's snapshot was fixed by its first
        # statement (routing, admission) and is older than anything the competitor writes; the whole
        # competitor runs to its commit HERE, while the subject holds its shared lock and has read no
        # position yet. One competitor at a time, each run to its end before the next starts:
        # competitors that overlap each other conflict AMONG THEMSELVES (measured 2026-09-25: a
        # competitor's routing transaction was cancelled as an SSI pivot against the previous
        # competitor's commit phase), which is contention too, but not the one this stand is about.
        for previous in stand.competitors:
            await asyncio.wait_for(asyncio.shield(previous), timeout=30)
        pid = int(await self.session.scalar(text("SELECT pg_backend_pid()")))
        competitor = asyncio.create_task(competitor_pays(str(uuid.uuid4())))
        stand.competitors.append(competitor)
        await asyncio.wait_for(asyncio.shield(competitor), timeout=30)
        # Premise (it replaces "the subject queued on the competitor's owner lock"): the competitor ran
        # its whole payment while the subject held the same equivalent lock, shared and granted.
        stand.subject_held_shared.append(
            await backend_holds_shared_equivalent_lock(factory, pid, world.equivalent_id)
        )

    monkeypatch.setattr(PaymentService, "_bind_payment", prepare)
    monkeypatch.setattr(PaymentService, "_run_payment_operation", operation)
    monkeypatch.setattr(MoneyBoundary, "_acquire_shared_equivalent_locks_in_order", shared_lock)


def _install_commit_conflict(monkeypatch, factory, world: ApiWorld, stand: _Stand) -> None:  # noqa: F811
    """A concurrent UPDATE of the equivalent row behind each commit attempt's snapshot."""

    original_commit = PaymentService._apply_payment  # the money phase (019 stage 4)
    original_guard = MoneyBoundary.refuse_inactive_equivalents

    async def commit(self, declaration, *args, **kwargs):
        if declaration.tx_id != stand.subject_tx:
            return await original_commit(self, declaration, *args, **kwargs)
        self._boundary._p019_subject_commit = True
        try:
            return await original_commit(self, declaration, *args, **kwargs)
        except BaseException as exc:
            stand.sqlstates.append(_payment_db_sqlstate(exc))
            raise
        finally:
            self._boundary._p019_subject_commit = False

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

    monkeypatch.setattr(PaymentService, "_apply_payment", commit)
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
            with caplog.at_level(logging.WARNING, logger="app.core.payments.service"):
                first = await asyncio.wait_for(
                    api.post("/api/v1/payments", json=body, headers=world.alice["headers"]),
                    timeout=60,
                )
            competitor_results = [await asyncio.wait_for(t, 30) for t in stand.competitors]
        finally:
            for t in stand.competitors:
                await finish(t)

    # ── controls: the conflict was the one this stand builds, and pay() spent its budget on it ──
    budget = int(settings.COMMIT_RETRY_ATTEMPTS)
    assert budget >= 2, f"premise: pay() retries nothing ({budget})"
    assert stand.sqlstates == ["40001"] * budget, stand.sqlstates
    retries = [
        r.getMessage()
        for r in caplog.records
        if "event=payment.attempt_retry " in r.getMessage()
    ]
    assert len(retries) == budget - 1 and all(
        "pgcode=40001" in m and "where=execute" in m for m in retries
    ), retries
    if phase == "prepare":
        assert competitor_results == ["COMMITTED"] * budget, competitor_results
        assert stand.subject_held_shared == [True] * budget, (
            "premise: each competitor committed while the subject held the same equivalent lock, shared",
            stand.subject_held_shared,
        )
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
