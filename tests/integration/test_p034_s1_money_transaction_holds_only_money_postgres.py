"""034 S1, reproducers of F-034-2 (`T3401`): the tick's money transaction also carries the planning reads and the
visual patch builders, on the money session, under its `FOR UPDATE` line locks, behind `except Exception`.

WHAT IS MEASURED, AND HOW. A listener on the engine the tick runs on records every transaction of the tick as a
span: `begin`, each statement (`after_cursor_execute`), `commit` or `rollback`. Each statement carries the ORIGIN it
was issued from - the payment service (`lock_staged_lines`, `create_payment_internal_staged`), a patch builder
(`VizPatchHelper.create`, `.maybe_refresh_quantiles`, `.compute_node_patches`,
`EdgePatchBuilder.build_edge_patch_for_pairs`) or the planning debt snapshot (`_load_debt_snapshot_by_pid`) - set by
wrappers that call straight through. Two reads are inline and so carry no origin: the precision read of planning
and the participants read of the patches (on 75dafc82: `app/core/simulator/tick.py:541` and
`real_payments_executor.py:772`; since the fix: `RealTick.load_planning_inputs` and
`RealPaymentsExecutor.build_patches_after_commit`).
The MONEY TRANSACTION is the span that holds the line locks and the staged payment's statements.

SINCE THE FIX (034 S1a) the first two tests are green - measured on one payment: 52 statements in the money
transaction (46 of the payment service, 6 of savepoint control) and none of anything else, against 63 and 11 before -
and the tests below them hold what a failed read may cost: its own patch or the plan's inputs, never the money and
never the report.

POSITIVE WITNESS, NOT ABSENCE (spec, "Запрещено": proof by removed behaviour). Test 1 first shows that a payment
was made, that every patch builder WAS called, that both published patches are non-empty and that the node patch
carries the payer's committed net balance; only then does it ask where their statements ran. A tree with no payment
or no builder fails a control. (The VALUES of the edge patch are not a control here: on the tree this was written
on, 75dafc82, the patch of a payment S->R over the line R->S names the pair S->R with `used` 0.00 - recorded for
the orchestrator in the T3401 report, and not this module's subject.)

Test 2 is the edge the spec names: the LAST patch query of the phase fails in PostgreSQL, after which the phase
issues no more SQL. The failure is a real server error - the statement is replaced by `SELECT 1/0` on its way to the
driver, so PostgreSQL itself aborts the transaction; no exception is constructed. What the tick then reports must
agree with the `transactions` and `debts` rows.

WHAT THIS STAND DOES NOT SEE: statements issued outside the tick's engine, and the SQL of a patch builder this
module does not wrap (a new builder shows up as a statement without an origin, which test 1 also refuses).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import event

from app.core.payments.service import PaymentService
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.viz_patch_helper import VizPatchHelper
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _Sse,
    _debts,
    _forget_the_route_cache,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    _transactions,
    factory,
)
from tests.integration.test_p034_s1_restart_repeats_the_idempotency_key_postgres import _scenario_with_a_bounded_amount
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

_ORIGIN: ContextVar[str | None] = ContextVar("p034_statement_origin", default=None)

_MONEY = "money:"
_PATCH = "patch:"
_PLANNING = "planning:"
_STAGED = _MONEY + "create_payment_internal_staged"
_LOCKS = _MONEY + "lock_staged_lines"
_LAST_PATCH_BUILDER = _PATCH + "build_edge_patch_for_pairs"
_SAVEPOINT_CONTROL = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


@dataclass
class _Statement:
    origin: str | None
    sql: str

    @property
    def is_savepoint_control(self) -> bool:
        return self.sql.lstrip().upper().startswith(_SAVEPOINT_CONTROL)


@dataclass
class _Span:
    """One database transaction as SQLAlchemy drove it: its statements and how it was ended."""

    statements: list[_Statement] = field(default_factory=list)
    ended_by: str | None = None

    def of(self, prefix: str) -> list[_Statement]:
        return [s for s in self.statements if (s.origin or "").startswith(prefix)]

    @property
    def not_money(self) -> list[_Statement]:
        """Everything in the span the payment service did not issue, savepoint control aside."""
        return [s for s in self.statements
                if not s.is_savepoint_control and not (s.origin or "").startswith(_MONEY)]

    def by_origin(self) -> dict[str, int]:
        return dict(Counter(("savepoint-control" if s.is_savepoint_control else (s.origin or "inline (no origin)"))
                            for s in self.statements))


class _Witness:
    """Spans of every transaction on one engine; optionally fails one chosen statement IN PostgreSQL."""

    def __init__(self) -> None:
        self.spans: list[_Span] = []
        self._open: dict[int, _Span] = {}
        self.fail_when = None  # (origin, sql) -> bool; the first match is replaced by `SELECT 1/0`
        self.failed: list[_Statement] = []
        self.server_errors: list[str] = []
        # Pool checkouts against the life of the money transaction: ("checkout", origin), ("lines-locked", None)
        # when its `FOR UPDATE` on the lines has run, ("money-ended", None) at its commit or rollback.
        self.timeline: list[tuple[str, str | None]] = []
        self._locking_conn: int | None = None

    def _checkout(self, _dbapi_connection, _record, _proxy) -> None:
        self.timeline.append(("checkout", _ORIGIN.get()))

    def _span(self, conn) -> _Span:
        span = self._open.get(id(conn))
        if span is None:
            span = self._open[id(conn)] = _Span()
            self.spans.append(span)
        return span

    def _begin(self, conn) -> None:
        self._span(conn)

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        origin = _ORIGIN.get()
        if self.fail_when is not None and not self.failed and self.fail_when(origin, statement):
            self.failed.append(_Statement(origin, statement))
            return "SELECT 1/0", ()
        return statement, parameters

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self._span(conn).statements.append(_Statement(_ORIGIN.get(), statement))
        if _ORIGIN.get() == _LOCKS and "FOR UPDATE" in statement.upper() and self._locking_conn is None:
            self._locking_conn = id(conn)
            self.timeline.append(("lines-locked", None))

    def _error(self, context) -> None:
        self.server_errors.append(str(getattr(context.original_exception, "sqlstate", None)))
        if self.failed and context.connection is not None:
            # The failed statement never reaches `after_cursor_execute`; it still belongs to its transaction.
            self._span(context.connection).statements.append(self.failed[-1])

    def _end(self, how: str):
        def ended(conn) -> None:
            span = self._open.pop(id(conn), None)
            if span is not None:
                span.ended_by = how
            if self._locking_conn == id(conn):
                self._locking_conn = None
                self.timeline.append(("money-ended", None))
        return ended

    @contextmanager
    def on(self, engine) -> Iterator["_Witness"]:
        listeners = [("begin", self._begin, {}), ("before_cursor_execute", self._before, {"retval": True}),
                     ("after_cursor_execute", self._after, {}), ("handle_error", self._error, {}),
                     ("commit", self._end("commit"), {}), ("rollback", self._end("rollback"), {})]
        for name, fn, kw in listeners:
            event.listen(engine, name, fn, **kw)
        event.listen(engine.pool, "checkout", self._checkout)
        try:
            yield self
        finally:
            event.remove(engine.pool, "checkout", self._checkout)
            for name, fn, _kw in listeners:
                event.remove(engine, name, fn)

    def money_transaction(self) -> _Span:
        # The payment service also reads on a short transaction of its own; the money transaction is the one that
        # took the line locks AND staged the payment.
        spans = [s for s in self.spans if s.of(_LOCKS) and s.of(_STAGED)]
        assert len(spans) == 1, (
            f"expected one transaction with the line locks and the staged payment, got {len(spans)}: "
            f"{[(s.ended_by, s.by_origin()) for s in self.spans]}"
        )
        return spans[0]


def _name_the_origins(monkeypatch, runner) -> dict[str, int]:
    """Wrap the payment service entries, the patch builders and the planning snapshot so that every statement
    they issue carries their name. Each wrapper calls the original with the same arguments. Returns call counts."""

    called: Counter[str] = Counter()

    def named(origin: str, fn):
        async def wrapper(*args, **kwargs):
            called[origin] += 1
            token = _ORIGIN.set(origin)
            try:
                return await fn(*args, **kwargs)
            finally:
                _ORIGIN.reset(token)
        return wrapper

    for owner, name, prefix in (
        (PaymentService, "lock_staged_lines", _MONEY),
        (PaymentService, "create_payment_internal_staged", _MONEY),
        (VizPatchHelper, "maybe_refresh_quantiles", _PATCH),
        (VizPatchHelper, "compute_node_patches", _PATCH),
        (EdgePatchBuilder, "build_edge_patch_for_pairs", _PATCH),
    ):
        monkeypatch.setattr(owner, name, named(prefix + name, getattr(owner, name)))
    create = VizPatchHelper.__dict__["create"].__func__
    monkeypatch.setattr(VizPatchHelper, "create", classmethod(named(_PATCH + "create", create)))
    monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid",
                        named(_PLANNING + "debt_snapshot", runner._load_debt_snapshot_by_pid))
    return called


async def _one_tick(factory, monkeypatch, *, fail_when=None):  # noqa: F811
    """One real tick of the p015 stand (one line, one planned payment) under the witness."""

    world, run, sse, plan, called, witness, debts, transactions = await _tick(factory, monkeypatch, fail_when=fail_when)
    assert len(plan) == 1, plan
    return world, run, sse, Decimal(plan[0].amount), called, witness, debts, transactions


async def _tick(factory, monkeypatch, *, fail_when=None, payments: int = 1, scenario=_scenario):  # noqa: F811
    """One real tick of the p015 stand under the witness, planning up to `payments` payments."""

    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p034-s1-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, scenario(world), sse, actions_per_tick_max=payments)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        called = _name_the_origins(monkeypatch, runner)
        witness = _Witness()
        witness.fail_when = fail_when
        with witness.on(factory.kw["bind"].sync_engine):
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
        debts = await _debts(factory, world)
        transactions = await _transactions(factory, world)
    finally:
        _forget_the_route_cache(world)
    assert len(plans) == 1, plans
    return world, run, sse, plans[0], called, witness, debts, transactions


@pytest.mark.asyncio
async def test_the_money_transaction_holds_no_planning_read_and_no_patch_builder(factory, monkeypatch) -> None:  # noqa: F811
    world, run, sse, amount, called, witness, debts, transactions = await _one_tick(factory, monkeypatch)
    pair = (world.sender.pid, world.receiver.pid)
    money = witness.money_transaction()

    # ── controls: a payment was made, the builders ran, and their patches are real ──────────────────
    assert amount > 0 and list(transactions.values()) == ["COMMITTED"], (amount, transactions)
    assert debts == {pair: _OPENING + amount}, debts
    assert run.last_error is None and witness.server_errors == [], (run.last_error, witness.server_errors)
    for origin in (_STAGED, _LOCKS, _PLANNING + "debt_snapshot", _PATCH + "create",
                   _PATCH + "maybe_refresh_quantiles", _PATCH + "compute_node_patches", _LAST_PATCH_BUILDER):
        assert called[origin] >= 1, f"{origin} was not called: {dict(called)}"
    assert money.ended_by == "commit", money.ended_by
    first = money.statements[0]
    locking = [s for s in money.of(_LOCKS) if "FOR UPDATE" in s.sql.upper()]
    assert first.origin == _LOCKS and locking, money.by_origin()
    under_the_locks = money.statements[money.statements.index(locking[0]) + 1:]
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert len(updated) == 1, sse.events
    edge_patch, node_patch = updated[0].get("edge_patch"), updated[0].get("node_patch")
    assert edge_patch and node_patch, updated[0]
    # The patches describe the debts as committed: the payer's net balance is minus what it owes after the tick.
    payer = [n for n in node_patch if n["id"] == world.sender.pid]
    assert [(n["net_balance"], n["net_sign"]) for n in payer] == [(to_money_str(-(_OPENING + amount), 2), -1)], node_patch
    everywhere = [s for span in witness.spans for s in span.statements]
    builders_and_planning = [s for s in everywhere if (s.origin or "").startswith((_PATCH, _PLANNING))]
    assert builders_and_planning, "the builders and the planning snapshot issued no SQL at all"

    # ── target ────────────────────────────────────────────────────────────────────────────────────
    inside = money.not_money
    assert not inside, (
        f"the money transaction (BEGIN..COMMIT, {len(money.statements)} statements, {len(under_the_locks)} of them "
        f"after the FOR UPDATE on the lines) holds {len(inside)} statement(s) the payment service did not issue, "
        f"{len([s for s in inside if s in under_the_locks])} of them under the line locks: "
        f"{len(money.of(_PATCH))} of the patch builders, {len(money.of(_PLANNING))} of the planning debt snapshot, "
        f"{len([s for s in inside if s.origin is None])} inline; by origin: {money.by_origin()}; "
        f"inline: {[' '.join(s.sql.split())[:90] for s in inside if s.origin is None]}. Expected: 0"
    )


@pytest.mark.asyncio
async def test_a_failed_last_patch_query_leaves_the_report_equal_to_the_rows(factory, monkeypatch) -> None:  # noqa: F811
    def the_line_read_of_the_edge_patch(origin, sql) -> bool:
        return origin == _LAST_PATCH_BUILDER and "trust_lines" in sql.lower()

    world, run, sse, amount, called, witness, debts, transactions = await _one_tick(
        factory, monkeypatch, fail_when=the_line_read_of_the_edge_patch
    )
    pair = (world.sender.pid, world.receiver.pid)

    # ── controls: the payment was staged, and PostgreSQL itself refused the last patch query ─────────
    assert called[_STAGED] == 1 and called[_LAST_PATCH_BUILDER] == 1, dict(called)
    assert len(witness.failed) == 1 and witness.server_errors[:1] == ["22012"], (witness.failed, witness.server_errors)
    span = next(s for s in witness.spans if witness.failed[0] in s.statements)
    assert span.statements[-1] is witness.failed[0], (
        f"SQL followed the failed patch query in its transaction: {span.by_origin()}"
    )

    # ── target: what is reported agrees with what is stored ─────────────────────────────────────────
    reported = sse.published("tx.updated")
    stored = [state for state in transactions.values() if state == "COMMITTED"]
    moved = debts[pair] - _OPENING
    assert reported == len(stored) == run.committed_total and moved == amount * reported, (
        f"after the last patch query failed in PostgreSQL (22012) the tick reported {reported} payment(s) of "
        f"{amount} as made (tx.updated), committed_total {run.committed_total}, committed money ticks "
        f"{run._real_money_committed_ticks_total}, last_error {run.last_error!r}; stored: transactions "
        f"{transactions}, debt moved by {moved}; the transaction of the failed query was ended by "
        f"{span.ended_by!r} and held the staged payment: {bool(span.of(_STAGED))}; tx.failed published "
        f"{sse.published('tx.failed')}. Expected: reported == stored COMMITTED rows and debt moved == amount * reported"
    )


# ── after the fix (034 S1a): what a failed read costs, and what it may not cost ─────────────────────────


def _warnings(caplog, event: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and event in r.getMessage()]


@pytest.mark.asyncio
async def test_a_patch_failure_after_payment_k_costs_that_patch_and_nothing_else(factory, monkeypatch, caplog) -> None:  # noqa: F811
    """Three payments in one tick; the edge patch of the SECOND fails in PostgreSQL.

    Before the fix the patch was read in the money transaction between the payments: the third payment met an
    aborted transaction and the tick's `COMMIT` rolled all three back. Now every payment is stored and reported,
    the second `tx.updated` is published WITHOUT a patch, the other two carry theirs, and the failure is logged.
    """

    line_reads: list[str] = []

    def the_line_read_of_the_second_edge_patch(origin, sql) -> bool:
        if origin == _LAST_PATCH_BUILDER and "trust_lines" in sql.lower():
            line_reads.append(sql)
            return len(line_reads) == 2
        return False

    with caplog.at_level(logging.WARNING):
        world, run, sse, plan, called, witness, debts, transactions = await _tick(
            factory, monkeypatch, fail_when=the_line_read_of_the_second_edge_patch, payments=3,
            scenario=_scenario_with_a_bounded_amount,
        )
    pair = (world.sender.pid, world.receiver.pid)
    amounts = [Decimal(a.amount) for a in plan]
    money = witness.money_transaction()

    # Controls: three payments were planned and staged, and PostgreSQL refused exactly one patch query - outside
    # the money transaction, which holds nothing but the payment service's SQL and was committed.
    assert len(amounts) == 3 and all(a > 0 for a in amounts) and called[_STAGED] == 3, (plan, dict(called))
    assert len(witness.failed) == 1 and witness.server_errors == ["22012"], (witness.failed, witness.server_errors)
    assert witness.failed[0] not in money.statements and money.not_money == [] and money.ended_by == "commit", (
        money.by_origin(), money.ended_by)

    # The money: all three stored, the debt moved by their sum, and the report says exactly that.
    assert sorted(transactions.values()) == ["COMMITTED"] * 3, transactions
    assert debts == {pair: _OPENING + sum(amounts)}, debts
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert [Decimal(e["amount"]) for e in updated] == amounts, updated  # one per payment, in plan order
    assert (run.committed_total, run.errors_total, run.last_error, sse.published("tx.failed")) == (3, 0, None, 0)
    assert run._real_money_committed_ticks_total == 1

    # The patches: the failed one is absent from its event, not invented; the others are read after the commit,
    # so each carries the payer's net balance after ALL three payments.
    assert [("edge_patch" in e, "node_patch" in e) for e in updated] == [(True, True), (False, False), (True, True)], updated
    final = to_money_str(-(_OPENING + sum(amounts)), 2)
    for event in (updated[0], updated[2]):
        assert [n["net_balance"] for n in event["node_patch"] if n["id"] == world.sender.pid] == [final], event
    assert len(_warnings(caplog, "simulator.real.edge_patch_failed")) == 1, [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_a_failed_node_patch_leaves_the_edge_patch_and_the_payment(factory, monkeypatch, caplog) -> None:  # noqa: F811
    """The node patch fails in PostgreSQL; its read transaction is ended by its owner and the edge patch of the same
    payment is still read (as before the fix, where the node patch had a handler of its own)."""

    def the_first_read_of_the_node_patch(origin, _sql) -> bool:
        return origin == _PATCH + "compute_node_patches"

    with caplog.at_level(logging.WARNING):
        world, run, sse, amount, called, witness, debts, transactions = await _one_tick(
            factory, monkeypatch, fail_when=the_first_read_of_the_node_patch
        )
    assert witness.server_errors == ["22012"], witness.server_errors
    assert list(transactions.values()) == ["COMMITTED"] and run.committed_total == 1, (transactions, run.committed_total)
    assert debts == {(world.sender.pid, world.receiver.pid): _OPENING + amount}, debts
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert [("edge_patch" in e, "node_patch" in e) for e in updated] == [(True, False)], updated
    assert len(_warnings(caplog, "simulator.real.node_patch_failed")) == 1, [r.getMessage() for r in caplog.records]
    assert _warnings(caplog, "simulator.real.edge_patch_failed") == []


@pytest.mark.asyncio
async def test_a_failed_planning_read_costs_the_plan_its_inputs_and_never_the_money(factory, monkeypatch, caplog) -> None:  # noqa: F811
    """The debt snapshot of planning fails in PostgreSQL. The planner falls back to the static limits (as it always
    could), the step of the equivalent is still read, and the payment goes through the payment service untouched."""

    def the_debt_read_of_planning(origin, sql) -> bool:
        return origin == _PLANNING + "debt_snapshot" and "debts" in sql.lower()

    with caplog.at_level(logging.WARNING):
        world, run, sse, amount, called, witness, debts, transactions = await _one_tick(
            factory, monkeypatch, fail_when=the_debt_read_of_planning
        )
    pair = (world.sender.pid, world.receiver.pid)
    money = witness.money_transaction()

    assert len(witness.failed) == 1 and witness.server_errors == ["22012"], (witness.failed, witness.server_errors)
    assert witness.failed[0] not in money.statements and money.not_money == [] and money.ended_by == "commit", (
        money.by_origin(), money.ended_by)
    everywhere = [s for span in witness.spans for s in span.statements]
    after_the_failure = everywhere[everywhere.index(witness.failed[0]) + 1:]
    assert any(s.origin is None and "equivalents.precision" in s.sql for s in after_the_failure), (
        "the step of the equivalent was not read after the failed debt snapshot")
    assert len(_warnings(caplog, "simulator.real.planning_debt_snapshot_failed")) == 1, [r.getMessage() for r in caplog.records]

    # Whatever the payment service decided about the planned amount, the report equals the rows.
    reported = sse.published("tx.updated")
    stored = [state for state in transactions.values() if state == "COMMITTED"]
    assert amount > 0 and called[_STAGED] == 1, (amount, dict(called))
    assert reported == len(stored) == run.committed_total and debts[pair] - _OPENING == amount * reported, (
        reported, transactions, debts, run.last_error)
    assert reported + sse.published("tx.failed") == 1, sse.events


# ── §15 review of `62cce627` (2026-10-08): one connection at a time, and a patch read that can be stopped ────────


@pytest.mark.asyncio
async def test_the_tick_takes_no_connection_of_its_own_while_it_holds_the_line_locks(factory, monkeypatch) -> None:  # noqa: F811
    """Finding A. On `62cce627` the planning reads ran on a second session AFTER the money transaction had taken
    `FOR UPDATE` on the lines: with a small pool the tick waited for a connection while holding the locks. The
    planning inputs are read, and their session is closed, BEFORE the money transaction takes anything; from the
    line locks to the end of the money transaction the only connections taken are the payment service's own."""

    world, run, sse, amount, called, witness, debts, transactions = await _one_tick(factory, monkeypatch)
    events = [name for name, _origin in witness.timeline]

    # Controls: the payment was made, the line locks and the end of the money transaction were both seen, and the
    # planning inputs WERE read - on a connection taken before the locks.
    assert list(transactions.values()) == ["COMMITTED"] and called[_PLANNING + "debt_snapshot"] == 1, (transactions, dict(called))
    assert events.count("lines-locked") == 1 and events.count("money-ended") == 1, witness.timeline
    locked, ended = events.index("lines-locked"), events.index("money-ended")
    assert locked < ended and "checkout" in events[:locked], witness.timeline

    foreign = [origin for name, origin in witness.timeline[locked:ended]
               if name == "checkout" and not (origin or "").startswith(_MONEY)]
    assert foreign == [], (
        f"between its `FOR UPDATE` on the lines and the end of its money transaction the tick took {len(foreign)} "
        f"pooled connection(s) outside the payment service, for: {foreign}; timeline: {witness.timeline}. Expected: 0"
    )


@pytest.mark.asyncio
async def test_a_stop_during_a_blocked_patch_read_ends_at_once_and_costs_only_the_patches(factory, monkeypatch) -> None:  # noqa: F811
    """Findings B and C. The money commit is confirmed and the reading of the visual patches never returns (a pool
    that has no connection, a server that does not answer). Stopping the run cancels the tick: it must end at once,
    its payment must be published - without patches - and the counters of the committed money phase must stand.
    On `62cce627` the reading was drained through the cancellation, so the tick could not be stopped, and the
    counters were skipped by the cancellation."""

    world = await _seed(factory)
    pair = (world.sender.pid, world.receiver.pid)
    reading, release = asyncio.Event(), asyncio.Event()

    async def _never_returns(*_args, **_kwargs):
        reading.set()
        await release.wait()
        raise AssertionError("the blocked patch read was resumed instead of being cancelled")

    try:
        sse = _Sse()
        run = _run_record(world, f"p034-s1-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        monkeypatch.setattr(VizPatchHelper, "create", classmethod(lambda _cls, *a, **kw: _never_returns(*a, **kw)))

        tick = asyncio.create_task(runner.tick_real_mode(run.run_id))
        try:
            await asyncio.wait_for(reading.wait(), timeout=60.0)
            # Control: the patches are being read AFTER the commit - the payment is already stored.
            assert list((await _transactions(factory, world)).values()) == ["COMMITTED"]
            assert sse.published("tx.updated") == 0, "the payment was published before its patches were read"

            tick.cancel()  # what `RunLifecycle.stop` does to the heartbeat that runs the tick
            done, _pending = await asyncio.wait({tick}, timeout=10.0)
            stopped = tick in done
        finally:
            release.set()
            await asyncio.gather(tick, return_exceptions=True)
        debts = await _debts(factory, world)
    finally:
        _forget_the_route_cache(world)

    amount = Decimal(plans[0][0].amount)
    assert stopped and tick.cancelled(), (
        f"the cancelled tick did not end while its patch read was blocked (ended: {stopped}, "
        f"cancelled: {tick.cancelled()}): an optional visual read must not hold a stop"
    )
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert [(Decimal(e["amount"]), "edge_patch" in e, "node_patch" in e) for e in updated] == [(amount, False, False)], updated
    assert debts == {pair: _OPENING + amount}, debts
    counters = (run.committed_total, run._real_money_committed_ticks_total, run._real_money_committed_payments_total,
                run._real_money_attempts_total, run._real_consec_money_no_progress_ticks)
    assert counters == (1, 1, 1, 1, 0), (
        f"after a stop during the patch read: committed_total, committed money ticks, committed money payments, "
        f"money attempts, ticks without money progress = {counters}; expected (1, 1, 1, 1, 0)"
    )
    assert factory.kw["bind"].sync_engine.pool.checkedout() == 0, "the patch session kept its connection"
