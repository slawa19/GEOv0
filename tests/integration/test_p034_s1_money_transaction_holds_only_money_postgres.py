"""034 S1, reproducers of F-034-2 (`T3401`): the tick's money transaction also carries the planning reads and the
visual patch builders, on the money session, under its `FOR UPDATE` line locks, behind `except Exception`.

WHAT IS MEASURED, AND HOW. A listener on the engine the tick runs on records every transaction of the tick as a
span: `begin`, each statement (`after_cursor_execute`), `commit` or `rollback`. Each statement carries the ORIGIN it
was issued from - the payment service (`lock_staged_lines`, `create_payment_internal_staged`), a patch builder
(`VizPatchHelper.create`, `.maybe_refresh_quantiles`, `.compute_node_patches`,
`EdgePatchBuilder.build_edge_patch_for_pairs`) or the planning debt snapshot (`_load_debt_snapshot_by_pid`) - set by
wrappers that call straight through. Two reads are inline and so carry no origin: the precision read of planning
(`app/core/simulator/tick.py:541`) and the participants read of the patches (`real_payments_executor.py:772`).
The MONEY TRANSACTION is the span that holds the staged payment's statements.

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
        return ended

    @contextmanager
    def on(self, engine) -> Iterator["_Witness"]:
        listeners = [("begin", self._begin, {}), ("before_cursor_execute", self._before, {"retval": True}),
                     ("after_cursor_execute", self._after, {}), ("handle_error", self._error, {}),
                     ("commit", self._end("commit"), {}), ("rollback", self._end("rollback"), {})]
        for name, fn, kw in listeners:
            event.listen(engine, name, fn, **kw)
        try:
            yield self
        finally:
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

    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p034-s1-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
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
    assert len(plans) == 1 and len(plans[0]) == 1, plans
    return world, run, sse, Decimal(plans[0][0].amount), called, witness, debts, transactions


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
    assert edge_patch and node_patch, updated[0]    # The patches describe the debts as committed: the payer's net balance is minus what it owes after the tick.
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
