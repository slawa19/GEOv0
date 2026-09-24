"""Programme 015, step 5c: the reaction to a confirmed FAILED (`T1516`) and the integrity hold (`T1546`).

WHAT IS UNDER TEST.

* `app/core/ledger/reconciliation.py` `react_to_failed`, called by `run_scheduled_reconciliation` after a
  scheduled `FAILED`: a fresh transaction per equivalent, the owner lock before the authoritative
  snapshot, the full verifier re-run, the evidence and the hold in one transaction, the commit - and only
  then the structured log and the metric.
* The hold at the T1544 refusal points: payment prepare, payment commit (before the envelope), clearing,
  and the `clearing-real` simulator route's declared 409. One reason per refusal; `equivalent_inactive`
  wins.
* `POST /admin/equivalents/{code}/integrity-hold/clear`: only after a later `PASSED`, audited.

The SQLite dev-file compatibility column added at startup was tested here too; that test left with
SQLite (017 stage 3, slice S3) - it drove a SQLite-only startup probe of `app/main.py`.

TIER. PostgreSQL. The race guarantees - the hold racing a payment commit and a clearing,
the owner lock before the snapshot, the clear under the owner lock - are PostgreSQL's and live in
`tests/integration/test_p015_step5c_hold_races_postgres.py`. The simulator tick lifecycle lives in
`tests/integration/test_p015_step5c_hold_through_the_tick_sqlite.py`.

MUTATIONS. Each test names the mutation that must turn it red.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, insert, select, update

from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import (
    FAILED,
    HOLD_ALREADY_HELD,
    HOLD_METRIC_EVENT,
    HOLD_NOT_CONFIRMED,
    HOLD_SET,
    PASSED,
    UNVERIFIABLE,
    run_scheduled_reconciliation,
)
from app.core.payments.engine import PaymentEngine
from app.core.payments.service import PaymentService
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.reconciliation_tables import debt_reconciliation_results
from app.utils.exceptions import ConflictException, RetryablePaymentConflictException
from tests.conftest import MODE_B, sessionmaker_of

# Every test commits through sessions of its own, so it runs on a disposable clone of the migrated
# template and leaves its rows to the clone's drop (018 B0b; see `tests/tier_on_a_clone.py`): the two
# `MODE_B` tests through `db_session`'s clone, every other test through `tier_on_a_clone`, which it
# opts into by name - never both in one test, since both clone under one name.
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture
from tests.debt_setup import debt_fixture_setup
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import (
    _edges,
    _prepare_payment,
    _seed_triangle,
    _tx_state,
)
from tests.unit.test_p015_step5a_reconciliation import (
    _around_the_application,
    _baseline,
    _driver_statement,
    _fixture_debts,
    _literal,
    _result_rows,
    _scheduled_run,
)

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
HOLD_LOG = "debt_reconciliation.integrity_hold_set"


# ==============================================================================================
# Stand helpers
# ==============================================================================================


async def _hold_of(factory, equivalent_id) -> uuid.UUID | None:
    async with factory() as session:
        return (
            await session.execute(
                select(Equivalent.integrity_hold_result_id).where(Equivalent.id == equivalent_id)
            )
        ).scalar_one()


def _hold_metric() -> float:
    from app.utils.metrics import RECOVERY_EVENTS_TOTAL

    return RECOVERY_EVENTS_TOTAL.labels(event=HOLD_METRIC_EVENT, result=HOLD_SET)._value.get()


def _hold_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if HOLD_LOG in r.getMessage()]


async def hold_directly(factory, equivalent_id) -> uuid.UUID:
    """STAND ONLY: a FAILED result row and a hold pointing at it, without running the verifier.

    For the tests whose subject is the REFUSAL of a held equivalent, not how the hold came about. The
    reaction itself is exercised end to end by the tests of this module that use `_faulty_triangle`.
    """

    result_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        await session.execute(
            insert(debt_reconciliation_results).values(
                id=result_id,
                equivalent_id=equivalent_id,
                status=FAILED,
                fingerprint="f" * 64,
                detail={"stand": "hold set directly"},
                checked_at=now,
                last_checked_at=now,
                is_latest=True,
            )
        )
        await session.execute(
            update(Equivalent)
            .where(Equivalent.id == equivalent_id)
            .values(integrity_hold_result_id=result_id)
        )
        await session.commit()
    return result_id


async def _faulty_triangle(factory):
    """A baselined equivalent whose debt `a -> b` was then moved by ONE ATOM around the application."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    await _set_debt(factory, debt, "10.00000001")
    return triangle, debt


async def _set_debt(factory, debt, amount: str) -> None:
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '{amount}' WHERE id = '{_literal(d, debt.id)}'"
    )


class _Factory:
    """The test session factory, with the reaction's work session observable and, on request, failing.

    `reconciliation._set_integrity_hold` is wrapped (`_tag_the_hold`) to mark the session that carries a
    hold. Its `commit` then records `committed`, or raises for the equivalents named in `fail_for` - a
    commit that fails AFTER the evidence and the hold were written into the transaction.
    """

    def __init__(self, inner, *, fail_for=()) -> None:
        self.inner = inner
        self.fail_for = set(fail_for)
        self.events: list[tuple[str, uuid.UUID]] = []

    def __call__(self):
        session = self.inner()
        original_commit = session.commit

        async def commit():
            target = session.info.get("hold_for")
            if target is not None and target in self.fail_for:
                self.events.append(("commit_failed", target))
                raise RuntimeError("stand: the hold's transaction failed to commit")
            await original_commit()
            if target is not None:
                self.events.append(("committed", target))

        session.commit = commit
        return session


def _tag_the_hold(monkeypatch, factory: _Factory) -> None:
    original_set = reconciliation._set_integrity_hold
    original_announce = reconciliation._announce_hold

    async def _set(session, equivalent_id, result_id):
        session.info["hold_for"] = equivalent_id
        await original_set(session, equivalent_id, result_id)

    def _announce(equivalent_id, decision):
        factory.events.append(("announced", equivalent_id))
        original_announce(equivalent_id, decision)

    monkeypatch.setattr(reconciliation, "_set_integrity_hold", _set)
    monkeypatch.setattr(reconciliation, "_announce_hold", _announce)


def _spy_reactions(monkeypatch) -> list[uuid.UUID]:
    called: list[uuid.UUID] = []
    original = reconciliation.react_to_failed

    async def _spy(session_factory, equivalent_id):
        called.append(equivalent_id)
        return await original(session_factory, equivalent_id)

    monkeypatch.setattr(reconciliation, "react_to_failed", _spy)
    return called


# ==============================================================================================
# The reaction
# ==============================================================================================


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_one_atom_around_the_application_holds_the_equivalent_through_the_scheduled_host(
    db_session, monkeypatch, caplog
) -> None:
    """The acceptance control, end to end through `app.main._run_integrity_checkpoints_once`.

    One atom added to a debt around the application -> the scheduled run -> the equivalent is held on
    the FAILED row that carries the evidence; the structured log and the metric are emitted once, and
    AFTER the hold's transaction committed.

    MUTATIONS: (1) remove the `react_to_failed` call from `run_scheduled_reconciliation` - no hold, red;
    (2) call `_announce_hold` inside the work transaction before its commit - `announced` precedes
    `committed`, red.
    """
    from tests.conftest import TestingSessionLocal

    factory = _Factory(TestingSessionLocal)
    _tag_the_hold(monkeypatch, factory)
    triangle, _debt = await _faulty_triangle(TestingSessionLocal)
    metric_before = _hold_metric()
    with caplog.at_level(logging.INFO):
        await _scheduled_run(monkeypatch, factory)

    hold = await _hold_of(TestingSessionLocal, triangle.equivalent.id)
    rows = await _result_rows(TestingSessionLocal, triangle.equivalent.id)
    latest_ids = await _latest_result_ids(TestingSessionLocal, triangle.equivalent.id)
    assert hold is not None, f"a confirmed FAILED did not hold the equivalent: {rows}"
    assert [row.status for row in rows] == [FAILED], rows
    assert latest_ids == [hold], "the hold does not point at the latest FAILED row"
    findings = rows[0].detail if isinstance(rows[0].detail, dict) else json.loads(rows[0].detail)
    residuals = [f for f in findings["findings"] if f["kind"] == "edge_residual"]
    assert [f["unexplained"] for f in residuals] == ["0.00000001"], findings

    logs = _hold_logs(caplog)
    assert len(logs) == 1 and str(triangle.equivalent.id) in logs[0] and str(hold) in logs[0], logs
    assert _hold_metric() == metric_before + 1
    eq = triangle.equivalent.id
    assert factory.events == [("committed", eq), ("announced", eq)], (
        f"the log and metric were not emitted strictly after the hold committed: {factory.events}"
    )


async def _latest_result_ids(factory, equivalent_id) -> list[uuid.UUID]:
    columns = debt_reconciliation_results.c
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(columns.id).where(
                        columns.equivalent_id == equivalent_id, columns.is_latest.is_(True)
                    )
                )
            ).scalars()
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_a_hold_whose_commit_fails_persists_nothing_and_emits_no_log_and_no_metric(
    db_session, monkeypatch, caplog
) -> None:
    """The evidence and the hold are ONE transaction, and nothing is announced for one that rolled back.

    The fault changes between the scheduled verdict and the reaction (a second atom), so the reaction's
    re-run is a DIFFERENT FAILED and its `record_outcome` inserts a row - which must go down with the hold.

    MUTATIONS: (1) emit the log and the metric before `work.commit()` - a log and a metric for a hold that
    does not exist, red; (2) commit the evidence in its own transaction before setting the hold - the
    reaction's row survives, red.
    """
    from tests.conftest import TestingSessionLocal

    triangle, debt = await _faulty_triangle(TestingSessionLocal)
    factory = _Factory(TestingSessionLocal, fail_for={triangle.equivalent.id})
    _tag_the_hold(monkeypatch, factory)
    original_react = reconciliation.react_to_failed

    async def _second_atom_then_react(session_factory, equivalent_id):
        await _set_debt(TestingSessionLocal, debt, "10.00000002")
        return await original_react(session_factory, equivalent_id)

    monkeypatch.setattr(reconciliation, "react_to_failed", _second_atom_then_react)
    metric_before = _hold_metric()
    with caplog.at_level(logging.INFO):
        counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])

    assert factory.events == [("commit_failed", triangle.equivalent.id)], (
        f"premise: the reaction did not reach its commit with the hold written: {factory.events}"
    )
    assert (counts[FAILED], counts["hold_errors"], counts[f"hold_{HOLD_SET}"]) == (1, 1, 0), counts
    assert await _hold_of(TestingSessionLocal, triangle.equivalent.id) is None
    rows = await _result_rows(TestingSessionLocal, triangle.equivalent.id)
    unexplained = [
        [f["unexplained"] for f in row.detail["findings"] if f["kind"] == "edge_residual"] for row in rows
    ]
    assert unexplained == [["0.00000001"]], (
        f"the reaction's evidence outlived its failed hold transaction: {unexplained}"
    )
    assert _hold_logs(caplog) == []
    assert _hold_metric() == metric_before


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_one_equivalent_failing_to_hold_does_not_roll_back_anothers(
    db_session, monkeypatch, caplog
) -> None:
    """Two faulty equivalents; the first one's hold fails to commit; the second is still held.

    MUTATIONS: (1) run the reactions of all equivalents in one session and commit once - both lost, red;
    (2) `break` out of the loop on a reaction error - the second is never held, red.
    """
    from tests.conftest import TestingSessionLocal

    first, _ = await _faulty_triangle(TestingSessionLocal)
    second, _ = await _faulty_triangle(TestingSessionLocal)
    factory = _Factory(TestingSessionLocal, fail_for={first.equivalent.id})
    _tag_the_hold(monkeypatch, factory)
    with caplog.at_level(logging.INFO):
        counts = await run_scheduled_reconciliation(
            factory, equivalent_ids=[first.equivalent.id, second.equivalent.id]
        )

    assert (counts[FAILED], counts["hold_errors"], counts[f"hold_{HOLD_SET}"]) == (2, 1, 1), counts
    assert await _hold_of(TestingSessionLocal, first.equivalent.id) is None
    assert await _hold_of(TestingSessionLocal, second.equivalent.id) is not None
    logs = _hold_logs(caplog)
    assert len(logs) == 1 and str(second.equivalent.id) in logs[0], logs
    assert ("commit_failed", first.equivalent.id) in factory.events, factory.events


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_the_re_run_is_the_confirmation_a_fault_gone_by_the_reaction_is_not_held(
    db_session, monkeypatch, caplog
) -> None:
    """The scheduled verdict is FAILED; the fault is repaired before the reaction re-verifies; no hold.

    This is the property the stale-publication bound in `record_outcome` leans on.

    MUTATION: hold on the scheduled outcome instead of re-running the verifier in `_confirm_and_hold` -
    held on a fault that no longer exists, red.
    """
    from tests.conftest import TestingSessionLocal

    triangle, debt = await _faulty_triangle(TestingSessionLocal)
    original_react = reconciliation.react_to_failed

    async def _repair_then_react(session_factory, equivalent_id):
        await _set_debt(TestingSessionLocal, debt, "10")
        return await original_react(session_factory, equivalent_id)

    monkeypatch.setattr(reconciliation, "react_to_failed", _repair_then_react)
    metric_before = _hold_metric()
    with caplog.at_level(logging.INFO):
        counts = await run_scheduled_reconciliation(
            TestingSessionLocal, equivalent_ids=[triangle.equivalent.id]
        )
    assert counts[FAILED] == 1, f"premise: the scheduled verdict was not FAILED: {counts}"
    assert counts[f"hold_{HOLD_NOT_CONFIRMED}"] == 1, counts
    assert await _hold_of(TestingSessionLocal, triangle.equivalent.id) is None
    assert _hold_logs(caplog) == [] and _hold_metric() == metric_before


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_a_repeated_failed_is_idempotent(db_session, caplog) -> None:
    """Three scheduled runs over a persisting fault, the third with a DIFFERENT fault: one hold, never
    re-pointed; one log, one metric; the result rows are only the verdict transitions.

    MUTATION: drop the `already held` return in `_confirm_and_hold` - the second run announces again, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, debt = await _faulty_triangle(factory)
    metric_before = _hold_metric()
    with caplog.at_level(logging.INFO):
        first = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
        hold = await _hold_of(factory, triangle.equivalent.id)
        second = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
        await _set_debt(factory, debt, "10.00000003")
        third = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])

    assert first[f"hold_{HOLD_SET}"] == 1 and hold is not None, first
    assert second[f"hold_{HOLD_ALREADY_HELD}"] == 1 and second["rows_unchanged"] == 1, second
    assert third[f"hold_{HOLD_ALREADY_HELD}"] == 1 and third["rows_inserted"] == 1, third
    assert await _hold_of(factory, triangle.equivalent.id) == hold, "the hold was re-pointed"
    assert len(await _result_rows(factory, triangle.equivalent.id)) == 2
    assert len(_hold_logs(caplog)) == 1
    assert _hold_metric() == metric_before + 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_no_hold_on_unverifiable(db_session, monkeypatch) -> None:
    """No baseline: the same one-atom change is UNVERIFIABLE. No reaction runs, and one called directly
    does not hold either - both gates.

    MUTATIONS: (1) react on `status != PASSED` in the loop - the reaction runs, red; (2) hold on
    `status != PASSED` in `_confirm_and_hold` - the direct call holds, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _set_debt(factory, debt, "10.00000001")
    reactions = _spy_reactions(monkeypatch)

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert counts[UNVERIFIABLE] == 1, f"premise: {counts}"
    assert reactions == []

    decision = await reconciliation.react_to_failed(factory, triangle.equivalent.id)
    assert decision.decision == HOLD_NOT_CONFIRMED, decision
    assert await _hold_of(factory, triangle.equivalent.id) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_no_hold_on_a_verifier_error_either_before_or_inside_the_reaction(
    db_session, monkeypatch, caplog
) -> None:
    """An error is not a verdict. (1) The scheduled verification raises: no result, no reaction, no
    hold. (2) The scheduled verdict is FAILED and the RE-RUN raises: `hold_errors`, no hold, no log.

    MUTATION: swallow an exception of `_confirm_and_hold` in `react_to_failed` and report `set` - the
    reaction is counted as a hold and announced, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    original_verify = reconciliation.verify_journal_equals_change
    calls = {"n": 0}
    async def _always_raises(session, equivalent_id):
        raise RuntimeError("stand: the verifier failed")

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", _always_raises)
    reactions = _spy_reactions(monkeypatch)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert counts["error"] == 1 and reactions == [], (counts, reactions)
    assert await _hold_of(factory, triangle.equivalent.id) is None

    async def _raises_on_the_re_run(session, equivalent_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return await original_verify(session, equivalent_id)
        raise RuntimeError("stand: the verifier failed inside the reaction")

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", _raises_on_the_re_run)
    with caplog.at_level(logging.INFO):
        counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert (counts[FAILED], counts["hold_errors"], calls["n"]) == (1, 1, 2), (counts, calls)
    assert await _hold_of(factory, triangle.equivalent.id) is None
    assert _hold_logs(caplog) == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_no_hold_on_an_inherited_critical_checkpoint(db_session, monkeypatch) -> None:
    """A debt with no trust line makes the integrity checkpoint `critical`; the baseline adopts it and the
    reconciliation is PASSED. The scheduled host must not hold: the hold reads only a confirmed FAILED.

    MUTATION: react to every scheduled status, not only FAILED (`if False: continue` in the loop) - a
    reaction runs on a PASSED equivalent whose checkpoint is critical, red. No code path reads the
    checkpoint's severity for the hold; that absence is what this negative control pins.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    reactions = _spy_reactions(monkeypatch)

    await _scheduled_run(monkeypatch, factory)

    async with factory() as session:
        (status,) = (
            await session.execute(
                select(IntegrityCheckpoint.invariants_status).where(
                    IntegrityCheckpoint.equivalent_id == triangle.equivalent.id
                )
            )
        ).scalars().all()
    assert status["passed"] is False and status["status"] == "critical", (
        f"premise: the checkpoint is not critical: {status}"
    )
    assert [row.status for row in await _result_rows(factory, triangle.equivalent.id)] == [PASSED]
    assert reactions == []
    assert await _hold_of(factory, triangle.equivalent.id) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_the_evidence_of_a_hold_cannot_be_deleted_while_held(db_session) -> None:
    """Deleting the FAILED row a hold points at is refused by the database, and the hold remains.

    A delete of the evidence - accidental or maintenance - must not release containment without a later
    PASSED, a reason and an audit. The PostgreSQL twin is in the races module.

    MUTATION: `ondelete="SET NULL"` on `Equivalent.integrity_hold_result_id` - the delete succeeds and
    the hold is released, red.
    """
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    hold = await _hold_of(factory, triangle.equivalent.id)
    assert counts[f"hold_{HOLD_SET}"] == 1 and hold is not None, f"premise: {counts}"

    # WITH THE FOREIGN KEYS ON (018 B1): the one-atom fault comes through the corruption helper,
    # whose `replica` setting also switches foreign keys off - so the DELETE under test must not.
    with pytest.raises(IntegrityError) as refused:
        await _driver_statement(
            factory,
            lambda d: f"DELETE FROM debt_reconciliation_results WHERE id = '{_literal(d, hold)}'",
        )
    # A FOREIGN KEY refusal, by SQLSTATE 23503 (foreign_key_violation). Until 017 stage 3 this
    # also accepted SQLite's "FOREIGN KEY constraint failed", which carries no code.
    orig = refused.value.orig
    assert (getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)) == "23503", (
        refused.value
    )
    assert await _hold_of(factory, triangle.equivalent.id) == hold
    assert await _latest_result_ids(factory, triangle.equivalent.id) == [hold]


# ==============================================================================================
# The refusal points
# ==============================================================================================


def _assert_hold_refusal(exc: BaseException, code: str) -> None:
    assert isinstance(exc, ConflictException), repr(exc)
    assert not isinstance(exc, RetryablePaymentConflictException), "the hold was raised as retryable"
    assert exc.code == "E008", exc.code
    assert exc.details.get("reason") == PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON, exc.details
    assert exc.details.get("equivalents") == [code], exc.details
    assert "retryable" not in exc.details, exc.details


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_a_held_equivalent_refuses_a_new_payment_before_any_transaction_exists(
    db_session,
) -> None:
    """The prepare-time check. A payment in ANOTHER equivalent still commits.

    MUTATION: remove the hold check from `PaymentService._create_payment_impl` - the refusal then comes
    from the commit, after a transaction row exists, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    held = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    free = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await hold_directly(factory, held.equivalent.id)
    tx_id = str(uuid.uuid4())
    async with factory() as session:
        with pytest.raises(ConflictException) as refused:
            await PaymentService(session).create_payment_internal(
                held.a.id, to_pid=held.b.pid, equivalent=held.equivalent.code,
                amount="1.00", idempotency_key=tx_id,
            )
    _assert_hold_refusal(refused.value, held.equivalent.code)
    assert await _tx_state(factory, tx_id) is None, "the refusal came after a transaction row was written"
    assert await _edges(factory, held) == {}

    async with factory() as session:
        result = await PaymentService(session).create_payment_internal(
            free.a.id, to_pid=free.b.pid, equivalent=free.equivalent.code, amount="1.00",
        )
    assert result.status == "COMMITTED", result


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_inactive_and_held_is_refused_as_inactive(db_session) -> None:
    """One reason per refusal, at prepare and at the shared helper; the operator stop wins.

    MUTATION: check the hold before `is_active` in `refuse_inactive_equivalents` or in the prepare path -
    the reason becomes `equivalent_integrity_hold`, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await hold_directly(factory, triangle.equivalent.id)
    async with factory() as session:
        await session.execute(
            update(Equivalent).where(Equivalent.id == triangle.equivalent.id).values(is_active=False)
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(ConflictException) as at_prepare:
            await PaymentService(session).create_payment_internal(
                triangle.a.id, to_pid=triangle.b.pid, equivalent=triangle.equivalent.code, amount="1.00",
            )
    async with factory() as session:
        with pytest.raises(ConflictException) as at_helper:
            await PaymentEngine(session).refuse_inactive_equivalents(
                {triangle.equivalent.id}, row_lock=True
            )
    for refused in (at_prepare, at_helper):
        assert refused.value.details["reason"] == PaymentEngine.EQUIVALENT_INACTIVE_REASON, (
            refused.value.details
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_a_payment_prepared_before_the_hold_is_refused_at_commit_before_the_envelope(
    db_session, committed_database
) -> None:
    """The binding check, ANCHORED: prepared, then held by the real scheduled reaction, then committed.

    The refusal reads the hold in the SAME statement as the operator stop (one `SELECT equivalents.code,
    equivalents.is_active, equivalents.integrity_hold_result_id`), after the TTL read, and nothing of the
    envelope or of `debts` is written before it.

    MUTATIONS: (1) a separate hold check placed after `Book.operation` opens the envelope - an
    `INSERT INTO debt_operations` precedes the refusal, red; (2) the hold read as its own statement
    instead of in the stop statement - the anchor statement no longer carries the hold, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    engine = committed_database.engine
    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(" ".join(str(statement).split()).upper())

    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5"))
    assert await _tx_state(factory, tx_id) == "PREPARED", "premise: the payment is not prepared"
    await _set_debt(factory, debt, "10.00000001")
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert counts[f"hold_{HOLD_SET}"] == 1, f"premise: the reaction did not hold: {counts}"

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with factory() as session:
            with pytest.raises(ConflictException) as refused:
                await PaymentEngine(session).commit(tx_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    _assert_hold_refusal(refused.value, triangle.equivalent.code)
    stop = "SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE, EQUIVALENTS.INTEGRITY_HOLD_RESULT_ID"
    stops = [i for i, s in enumerate(statements) if s.startswith(stop)]
    ttl = [i for i, s in enumerate(statements) if "PREPARE_LOCKS.EXPIRES_AT <=" in s]
    assert len(stops) == 1 and len(ttl) == 1 and ttl[0] < stops[0], (stops, ttl, statements)
    written = [
        s for s in statements[: stops[0] + 1]
        if s.startswith(("INSERT INTO DEBT_OPERATIONS", "INSERT INTO DEBTS", "UPDATE DEBTS", "DELETE FROM DEBTS"))
    ]
    assert written == [], f"money or its envelope was written before the hold refused: {written}"
    assert not [s for s in statements if s.startswith("INSERT INTO DEBT_OPERATIONS")]
    assert await _tx_state(factory, tx_id) == "ABORTED"
    assert await _edges(factory, triangle) == {("a", "b"): Decimal("10.00000001")}


async def _seed_cycle(factory, tag: str):
    """Three participants owing 10 around a cycle, trust lines consenting to clearing, no baseline yet."""

    equivalent = Equivalent(code=f"S5C{tag}", precision=2, is_active=True, metadata_={})
    people = [
        Participant(pid=f"S5C_{n}_{tag}", display_name=n, public_key=f"pk_s5c_{n}_{tag}", type="person",
                    status="active", profile={})
        for n in ("A", "B", "C")
    ]
    async with factory() as session:
        session.add_all([equivalent, *people])
        await session.flush()
        ring = [(people[0], people[1]), (people[1], people[2]), (people[2], people[0])]  # (debtor, creditor)
        session.add_all(
            TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=equivalent.id,
                      limit=Decimal("1000"), policy={"auto_clearing": True}, status="active")
            for debtor, creditor in ring
        )
        debts = [
            Debt(id=uuid.uuid4(), debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=equivalent.id,
                 amount=Decimal("10"), version=0)
            for debtor, creditor in ring
        ]
        async with debt_fixture_setup(session, label="step5c-cycle"):
            session.add_all(debts)
        await session.commit()
    return SimpleNamespace(equivalent=equivalent, people=people, debts=debts)


async def _cycle_amounts(factory, cycle) -> list[Decimal]:
    async with factory() as session:
        return sorted(
            Decimal(str(a))
            for a in (
                await session.execute(select(Debt.amount).where(Debt.equivalent_id == cycle.equivalent.id))
            ).scalars()
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_step5c_a_held_equivalent_refuses_clearing_and_another_equivalent_still_clears(
    db_session,
) -> None:
    """Clearing reads the hold through the same helper, before any debt is changed.

    MUTATION: make `refuse_inactive_equivalents` ignore the hold - the held cycle clears, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    tag = uuid.uuid4().hex[:6].upper()
    held = await _seed_cycle(factory, f"H{tag}")
    free = await _seed_cycle(factory, f"F{tag}")
    await _baseline(factory, held.equivalent.id)
    await _set_debt(factory, held.debts[0], "10.00000001")
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[held.equivalent.id])
    assert counts[f"hold_{HOLD_SET}"] == 1, f"premise: {counts}"

    async with factory() as session:
        with pytest.raises(ConflictException) as refused:
            await ClearingService(session).execute_clearing_with_amount(
                [{"debt_id": str(d.id)} for d in held.debts]
            )
    _assert_hold_refusal(refused.value, held.equivalent.code)
    assert await _cycle_amounts(factory, held) == [Decimal("10"), Decimal("10"), Decimal("10.00000001")]

    async with factory() as session:
        cleared = await ClearingService(session).execute_clearing_with_amount(
            [{"debt_id": str(d.id)} for d in free.debts]
        )
    assert cleared == Decimal("10"), cleared
    assert await _cycle_amounts(factory, free) == []


# MODE B (017 stage 2b, T1702): the world is seeded on `db_session` and the hold is written on a
# session of its own. In mode A on PostgreSQL that session could not see the uncommitted equivalent -
# `ForeignKeyViolationError` on `debt_reconciliation_results` - and clearing refuses a connection-bound
# session besides (stage-2 catalogue, class VIS).
@MODE_B
@pytest.mark.asyncio
async def test_step5c_clearing_real_reports_the_hold_as_its_declared_409(client, db_session, monkeypatch) -> None:
    """The simulator's `clearing-real` route maps the hold to its declared 409, not `500 CLEARING_FAILED`.

    MUTATION: match only `equivalent_inactive` in `app/api/v1/simulator.py` - 500, red.
    """
    factory = sessionmaker_of(db_session)
    from tests.integration.test_p015_t1544_operator_stop_refuses_money import run_owning_the_cycle  # noqa: F401

    import app.api.v1.simulator as simulator_module
    from app.core.simulator.models import RunRecord

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(
            run_id=str(run_id), state="running", owner_id="", _real_seeded=True, _real_seeding_lock=None
        ),
    )
    run = RunRecord(run_id="run-s5c", scenario_id="scn-s5c", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": pid, "name": pid.upper(), "type": "person", "status": "active"} for pid in ("s1", "s2", "s3")
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, "run-s5c", run)

    eq = Equivalent(code="S5CSIM", precision=2, is_active=True)
    people = {
        pid: Participant(id=uuid.uuid4(), pid=pid, display_name=pid.upper(), public_key=pid * 32,
                         type="person", status="active", profile={})
        for pid in ("s1", "s2", "s3")
    }
    db_session.add(eq)
    db_session.add_all(people.values())
    await db_session.commit()
    for debtor, creditor in (("s1", "s2"), ("s2", "s3"), ("s3", "s1")):
        db_session.add(
            TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                      equivalent_id=eq.id, limit=Decimal("1000"), policy={"auto_clearing": True}, status="active")
        )
        debt = Debt(debtor_id=people[debtor].id, creditor_id=people[creditor].id, equivalent_id=eq.id,
                    amount=Decimal("100"))
        async with debt_fixture_setup(db_session, label="setup"):
            db_session.add(debt)
    await db_session.commit()
    # Captured now: the route's refusal rolls the shared session back, which expires `eq`.
    eq_id = eq.id
    await hold_directly(factory, eq_id)

    resp = await client.post(
        "/api/v1/simulator/runs/run-s5c/actions/clearing-real",
        headers=ADMIN,
        json={"equivalent": "S5CSIM", "max_depth": 6, "client_action_id": "s5c"},
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "CONFLICT", body
    assert body["details"]["reason"] == PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON, body
    assert "retryable" not in body["details"], body
    async with factory() as session:
        amounts = (await session.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))).scalars().all()
    assert sorted(Decimal(str(a)) for a in amounts) == [Decimal("100")] * 3


# ==============================================================================================
# The admin clear
# ==============================================================================================


async def _clear(client, code: str, reason: str | None = "reconciled after the incident"):
    body = {} if reason is None else {"reason": reason}
    return await client.post(
        f"/api/v1/admin/equivalents/{code}/integrity-hold/clear", json=body, headers=ADMIN
    )


# MODE B (017 stage 2b, T1702): the route runs on `db_session` and the world lives on sessions of its
# own. In mode A on PostgreSQL the route's session is the fixture's one long outer transaction, at the
# application's SERIALIZABLE level, and the other sessions' commits conflict with it: `SerializationError
# ... due to concurrent update` (stage-2 catalogue, class 40001). In the application every request is
# its own transaction, as it is in mode B.
@MODE_B
@pytest.mark.asyncio
async def test_step5c_the_hold_is_cleared_only_explicitly_after_a_later_passed_and_audited(
    client, db_session
) -> None:
    """The whole lifecycle through the real route.

    held on F1 -> clear refused (latest is the hold's own FAILED) -> a DIFFERENT fault F2 -> clear refused
    (latest FAILED, not the hold's) -> repaired -> scheduled run PASSED -> STILL HELD, money still refused
    -> clear without a reason refused -> clear with a reason: 200, the public projection without the hold,
    audited -> money moves -> a second clear refused (nothing held).

    MUTATIONS: (1) clear automatically on a PASSED in `run_scheduled_reconciliation` - still-held
    assertion red; (2) drop `latest.status != PASSED` from the predicate - the F2 clear succeeds, red;
    (3) drop `_add_audit_entry` - no audit row, red.
    NOT REDDENABLE, and said so: `latest.id == hold_result_id` alone. A hold only ever points at a FAILED
    row and a row's status never changes, so the status condition already implies it.
    """
    factory = sessionmaker_of(db_session)

    triangle, debt = await _faulty_triangle(factory)
    code = triangle.equivalent.code
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    hold = await _hold_of(factory, triangle.equivalent.id)
    assert counts[f"hold_{HOLD_SET}"] == 1 and hold is not None, f"premise: {counts}"

    refused = await _clear(client, code)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "no_later_passed_reconciliation_result"

    await _set_debt(factory, debt, "10.00000005")
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert counts[FAILED] == 1 and counts["rows_inserted"] == 1, f"premise: F2 is a new latest: {counts}"
    refused = await _clear(client, code)
    assert refused.status_code == 409, refused.text
    assert await _hold_of(factory, triangle.equivalent.id) == hold

    await _set_debt(factory, debt, "10")
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert counts[PASSED] == 1, f"premise: the repair did not verify: {counts}"
    assert await _hold_of(factory, triangle.equivalent.id) == hold, "a PASSED cleared the hold by itself"
    async with factory() as session:
        with pytest.raises(ConflictException) as still:
            await PaymentService(session).create_payment_internal(
                triangle.a.id, to_pid=triangle.b.pid, equivalent=code, amount="1.00",
            )
    _assert_hold_refusal(still.value, code)

    for missing in (None, ""):
        resp = await _clear(client, code, reason=missing)
        assert resp.status_code == 422, resp.text
    assert await _hold_of(factory, triangle.equivalent.id) == hold

    async with factory() as session:
        audit_before = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    resp = await _clear(client, code, reason="ledger repaired, verified PASSED")
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == code and "integrity_hold_result_id" not in resp.json(), resp.json()
    assert await _hold_of(factory, triangle.equivalent.id) is None
    async with factory() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "admin.equivalents.integrity_hold.clear")
            )
        ).scalars().all()
        audit_after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert audit_after == audit_before + 1 and len(audits) == 1, audits
    assert audits[0].object_id == code and audits[0].reason == "ledger repaired, verified PASSED"
    assert audits[0].before_state == {"integrity_hold_result_id": str(hold)}, audits[0].before_state
    assert audits[0].after_state["integrity_hold_result_id"] is None, audits[0].after_state

    async with factory() as session:
        paid = await PaymentService(session).create_payment_internal(
            triangle.a.id, to_pid=triangle.b.pid, equivalent=code, amount="1.00",
        )
    assert paid.status == "COMMITTED", paid

    again = await _clear(client, code)
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "no_integrity_hold"
    unknown = await _clear(client, "NOSUCH5C")
    assert unknown.status_code == 404, unknown.text
