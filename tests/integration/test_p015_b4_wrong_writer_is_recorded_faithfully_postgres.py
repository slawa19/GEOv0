"""Programme 015, `B4` step 2: `C5` and `C6` on PostgreSQL - the tier where every barrier runs.

WHAT THIS MODULE ADDS over `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py`, and
why it is not a copy of it. Design v2 §9 marks `C5` and `C6` `S+P`, and the PostgreSQL half is the
one that settles the claim, because on SQLite three of the payment path's protections are not
merely unexercised - they RETURN EARLY:

* `_acquire_equivalent_owner_locks` and `_acquire_segment_advisory_locks`
  (`app/core/payments/engine.py:148-155`, `:240-241`) begin with `if not self._is_postgres():
  return`. On SQLite the wrong writer of `C6` passes them by not meeting them.
* `ClearingService.execute_clearing_with_amount` (`app/core/clearing/service.py:1487-1492`)
  delegates straight to the inner implementation on any non-PostgreSQL dialect; the one-connection
  interlock that guards a real clearing exists only here.

A counterexample that only ran on SQLite could therefore be answered with "the real backend would
have caught it". This module is the answer to that: it runs the SAME two wrong writers with every
lock, every interlock and `SERIALIZABLE` isolation in force, and they still commit, are still
audited, and are still recorded as verified.

It also runs them at FULL MONEY SIZE. PostgreSQL is the money-acceptance tier (design v2 §4 rule 1),
so the amounts here are `999999999999.99999999` - the largest scale-8 value `NUMERIC(20,8)` holds -
and every comparison is made in INTEGER ATOMS with no tolerance at all. SQLite cannot host this
half: above `2^26` its float binding silently changes the value, which would make the criteria
measure the driver rather than the writer.

THE TWO CRITERIA, unchanged from the SQLite module and from design v2 §9 `C5`:

* criterion (a) - the sum of the journal's deltas per edge equals the edge's final minus initial
  amount, both read independently of the writer;
* criterion (b) - replaying the operation's recorded intent through an independent integer-atom
  implementation of the documented rule reproduces the final state.

`C6` is the case where (a) holds and (b) fails. That gap is the product of step 4.

THE STAND. Its own engine with `isolation_level="SERIALIZABLE"` and a real pool, never the
`db_session` fixture, whose outer transaction would hide the very commit boundaries these
counterexamples are about. `geov0_test_ci` is SHARED with other sessions, so every test cleans up
in a `finally` scoped to the ids it created, and an autouse fixture asserts afterwards that they
are gone.

MARKER, HISTORICAL. This module carried `b4_counterexample` alongside `postgres` and was deselected
from every tier while the debt journal did not exist. Step 4 slice C built it and REMOVED THE
MARKER, not the assertions: every test below still asserts exactly what it asserted while it was
red, and each one names in its docstring the mutation that must turn it red again. `postgres`
stays - this tier is about PostgreSQL semantics, not about the journal's absence.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.core.payments.engine import PaymentEngine
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup, purge_test_ledger
from tests.p015_b4_support import ENTRIES_TABLE, OPERATIONS_TABLE, missing_journal_tables, stored_rows

#: One scale-8 atom. The clearing half of `C6` is wrong by exactly this much on every edge.
ATOM = Decimal("0.00000001")

#: The largest scale-8 value `NUMERIC(20, 8)` can hold. Using it everywhere is deliberate: a
#: counterexample about money that runs at "10.00" proves nothing about the encoding, and design v2
#: §4 rule 1 puts money ACCEPTANCE on this tier precisely so the boundary gets exercised.
FULL_SIZE = Decimal("999999999999.99999999")


@pytest_asyncio.fixture
async def serializable_factory():
    """A sessionmaker over this module's own SERIALIZABLE engine with a real pool.

    Never `db_session`: that fixture wraps each PostgreSQL test in an outer transaction that is
    rolled back, so `PaymentEngine.commit`'s own `session.commit()` would become a savepoint
    release and "the wrong state is COMMITTED and durable" - the whole point of `C6` - could not be
    observed at all.
    """
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=4,
        max_overflow=0,
        pool_timeout=15,
        isolation_level="SERIALIZABLE",
    )
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


# ==============================================================================================
# The stand
# ==============================================================================================


class _Triangle:
    def __init__(self, equivalent, participants: dict[str, Participant]) -> None:
        self.equivalent_id = equivalent.id
        self.equivalent_code = equivalent.code
        self.a = participants["a"]
        self.b = participants["b"]
        self.c = participants["c"]
        self.by_id = {p.id: name for name, p in participants.items()}
        self.participant_ids = [p.id for p in participants.values()]

    def name(self, participant_id) -> str:
        return self.by_id.get(participant_id, str(participant_id))


#: Every triangle THIS PROCESS seeded. The leak check after each test is scoped to these ids and
#: never to the `B4C6` prefix: `geov0_test_ci` is shared, and a check that read the prefix would be
#: asserting about a neighbouring session's rows.
_SEEDED: list[_Triangle] = []


async def _seed_triangle(factory, *, trustlines: list[tuple[str, str, Decimal]]) -> _Triangle:
    """Three participants, one equivalent and the named trustlines, committed before the test.

    `trustlines` entries are `(creditor, debtor, limit)` - the repository's direction convention,
    `from -> to` meaning creditor -> debtor and bounding the CREDITOR's risk (`AGENTS.md` §8, and
    `app/core/invariants.py:95-97`).
    """
    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code=f"B4C6{tag}"[:16], precision=2, is_active=True, metadata_={})
        people = {
            name: Participant(
                pid=f"B4C6_{name.upper()}_{tag}",
                display_name=name.upper(),
                public_key=f"pk_b4c6_{name}_{tag}",
                type="person",
                status="active",
                profile={},
            )
            for name in ("a", "b", "c")
        }
        session.add_all([equivalent, *people.values()])
        await session.flush()
        for creditor, debtor, limit in trustlines:
            session.add(
                TrustLine(
                    from_participant_id=people[creditor].id,
                    to_participant_id=people[debtor].id,
                    equivalent_id=equivalent.id,
                    limit=limit,
                    status="active",
                )
            )
        await session.commit()
    triangle = _Triangle(equivalent, people)
    _SEEDED.append(triangle)
    return triangle


async def _drop_triangle(factory, triangle: _Triangle) -> None:
    """Remove exactly what this module created, by id, in foreign-key order.

    `transactions.initiator_id` is RESTRICT (`app/db/models/transaction.py:13`) and
    `prepare_locks.tx_id` references `transactions`, so both go before the participants;
    `debts.equivalent_id` is RESTRICT since T1524, so the debts go before the equivalent.
    `debts.debtor_id` is still CASCADE, so deleting participants first WOULD take the debts with
    them silently - exactly the removal T1524 exists to make impossible - and this teardown
    therefore never leans on a cascade to do its work.
    """
    async with factory() as session:
        # Both kinds are found by initiator: a CLEARING transaction's `initiator_id` is one of the
        # cycle's own participants (`app/core/clearing/service.py:1993`), so this one query covers
        # the payments this module prepared and the clearings it ran.
        tx_ids = list(
            (
                await session.execute(
                    select(Transaction.tx_id).where(
                        Transaction.initiator_id.in_(triangle.participant_ids)
                    )
                )
            ).scalars().all()
        )
        # The debts and the journal go through the driver, and BEFORE the transactions: once the
        # journal is armed, `session.execute(delete(Debt))` is Core DML the write guard refuses, and
        # `debt_operations.tx_id` is a RESTRICT reference to `transactions.tx_id`, so an envelope
        # still standing would block the delete above it. See `tests/debt_setup.purge_test_ledger`.
        await purge_test_ledger(
            session, equivalent_ids=[triangle.equivalent_id], tx_ids=tx_ids
        )
        if tx_ids:
            await session.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
            await session.execute(
                delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
            )
            await session.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await session.execute(
            delete(IntegrityAuditLog).where(
                IntegrityAuditLog.equivalent_code == triangle.equivalent_code
            )
        )
        await session.execute(
            delete(TrustLine).where(TrustLine.equivalent_id == triangle.equivalent_id)
        )
        await session.execute(
            delete(Participant).where(Participant.id.in_(triangle.participant_ids))
        )
        await session.execute(delete(Equivalent).where(Equivalent.id == triangle.equivalent_id))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def every_seeded_row_is_gone_when_the_test_ends():
    """The CHECK, not the cleanup - the cleanup is each test's own `finally`.

    A `finally` per test is only as good as the next author remembering to write one, so this
    asserts the outcome instead of trusting the habit. This session leaked three rows a run from a
    module that had no such check, which is why it exists here from the first commit rather than
    after the leak.

    It reads through `TestingSessionLocal` rather than this module's own engine, because that
    engine's fixture may already have been disposed by the time the teardown runs.
    """
    yield

    if not _SEEDED:
        return
    from tests.conftest import TestingSessionLocal

    equivalent_ids = [triangle.equivalent_id for triangle in _SEEDED]
    participant_ids = [pid for triangle in _SEEDED for pid in triangle.participant_ids]
    async with TestingSessionLocal() as session:
        debts = await session.scalar(
            select(func.count()).select_from(Debt).where(Debt.equivalent_id.in_(equivalent_ids))
        )
        equivalents = await session.scalar(
            select(func.count()).select_from(Equivalent).where(Equivalent.id.in_(equivalent_ids))
        )
        participants = await session.scalar(
            select(func.count()).select_from(Participant).where(Participant.id.in_(participant_ids))
        )
        transactions = await session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.initiator_id.in_(participant_ids))
        )
    assert (debts, equivalents, participants, transactions) == (0, 0, 0, 0), (
        f"this module left rows behind in a SHARED database: {debts} debt(s), {equivalents} "
        f"equivalent(s), {participants} participant(s), {transactions} transaction(s) of the "
        f"{len(_SEEDED)} triangle(s) it seeded. Every test that calls `_seed_triangle` must call "
        f"`_drop_triangle` from a `finally`."
    )


async def _edges(factory, triangle: _Triangle) -> dict[tuple[str, str], int]:
    """Every live edge of this equivalent IN INTEGER ATOMS, read on a NEW session.

    Atoms, not `Decimal`, because this is the money-acceptance tier and design v2 §4 rule 1 says
    expectations here are integer atoms compared exactly. A `Decimal` comparison would still be
    exact on PostgreSQL, but it would not FAIL if someone reintroduced a tolerance.
    """
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == triangle.equivalent_id
                )
            )
        ).all()
    return {
        (triangle.name(debtor), triangle.name(creditor)): _atoms(amount)
        for debtor, creditor, amount in rows
    }


def _atoms(value) -> int:
    return int((Decimal(str(value)) / ATOM).to_integral_value())


# ==============================================================================================
# The independent algebra - criterion (b)
# ==============================================================================================


def _apply_flow_in_atoms(state: dict[tuple[str, str], int], sender: str, receiver: str, amount: int):
    """The documented payment rule, in integers, written here and NOT imported.

    "Independent" is the load-bearing word: importing `PaymentEngine._apply_flow` would make
    criterion (b) a tautology, and a wrong writer checked against itself always agrees. This is the
    rule as `app/core/payments/engine.py:1468-1472` DESCRIBES it - reduce the receiver's existing
    debt to the sender first, put the remainder on the sender, then net any mutual pair.
    """
    remaining = amount
    reverse = state.get((receiver, sender), 0)
    if reverse > 0:
        reduction = min(remaining, reverse)
        state[(receiver, sender)] = reverse - reduction
        remaining -= reduction
    if remaining > 0:
        state[(sender, receiver)] = state.get((sender, receiver), 0) + remaining
    forward = state.get((sender, receiver), 0)
    reverse = state.get((receiver, sender), 0)
    if forward > 0 and reverse > 0:
        net = min(forward, reverse)
        state[(sender, receiver)] = forward - net
        state[(receiver, sender)] = reverse - net
    for key in [key for key, value in state.items() if value == 0]:
        del state[key]


def _payment_implied_by_intent(before: dict, flows: list[tuple[str, str, int]]) -> dict:
    state = dict(before)
    for sender, receiver, amount in flows:
        _apply_flow_in_atoms(state, sender, receiver, amount)
    return {edge: value for edge, value in state.items() if value != 0}


def _clearing_implied_by_intent(before: dict, cycle: list[tuple[str, str]]) -> dict:
    state = dict(before)
    clear = min(state[edge] for edge in cycle)
    for edge in cycle:
        state[edge] -= clear
    return {edge: value for edge, value in state.items() if value != 0}


# ==============================================================================================
# The intent AS THE ENVELOPE STORED IT - what criterion (b) is computed from
# ==============================================================================================
#
# SAME CORRECTION AS THE SQLITE SIBLING (external review, 2026-09-13), and it was needed here too.
# Criterion (b) was computed from `_intent_flows` - this module's own read of `PrepareLock.effects` -
# and from a literal list of cycle edges. Neither touches `debt_operations.intent`, so a journal that
# stored the writer's own RESULT as the operation's intent left both halves of `C6-P` green. `C14` on
# this tier does compare the stored intent against an independent capture, but `C6-P` is the test that
# CLAIMS to refute a wrong writer from the record, and it has to read the record to do it.


def _decoded_json(value):
    """JSON as Python. Raw `text()` SQL carries no type information, so asyncpg may hand a `json`
    column back as a string; a counterexample must not depend on which."""

    return json.loads(value) if isinstance(value, (str, bytes)) else value


async def _envelope_intents_for_tx(factory, tx_id: str):
    """The envelopes owning `tx_id`, intent decoded. `None` when `debt_operations` is absent."""

    rows = await stored_rows(
        factory,
        f"SELECT kind, identity, state, intent FROM {OPERATIONS_TABLE} "  # noqa: S608
        f"WHERE tx_id = :tx_id",
        {"tx_id": tx_id},
    )
    if rows is None:
        return None
    return [dict(row, intent=_decoded_json(row["intent"])) for row in rows]


def _recorded_payment_flows(triangle: _Triangle, intent) -> list[tuple[str, str, int]]:
    """Every `{from, to, amount}` flow inside a STORED payment intent, named, in atoms, sorted.

    Design v2 §7 fixes the CONTENT and not the nesting, so this walks whatever shape step 4 chose.
    The ids go through `uuid.UUID`: the intent is JSON and carries the dashed canonical form while
    the journal's own columns carry a native `uuid` here and 32 hex characters on SQLite, and a
    comparison written against either spelling directly matches nothing on the other tier.

    WHAT THIS DECODER PROJECTS AWAY, recorded because round 3 asked what the replay actually covers:
    each flow's `equivalent` and `lock_id`, and the grouping into locks
    (`app/core/payments/engine.py:1319-1336` stores all four). What comes out is `(from, to, atoms)`
    per flow. Every scenario in this module runs inside ONE equivalent, so the projection loses
    nothing here - and that is the limit: criterion (b) as checked here is evidence about
    single-equivalent routing, not about the whole intent. A payment that moved the right amounts
    between the right parties in the WRONG equivalent would pass it. That is out of `C6`'s scope
    (design v2 §9) and nothing in this module claims otherwise.
    """

    found: list[tuple[str, str, int]] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            if {"from", "to", "amount"} <= set(node):
                found.append(
                    (
                        triangle.name(uuid.UUID(str(node["from"]))),
                        triangle.name(uuid.UUID(str(node["to"]))),
                        _atoms(node["amount"]),
                    )
                )
                return
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(intent)
    return sorted(found)


def _recorded_clearing_pre_amounts(triangle: _Triangle, intent) -> dict[tuple[str, str], int]:
    """The cycle's PRE-AMOUNTS per named edge, in atoms, as the stored clearing intent recorded them.

    PROJECTS AWAY, like its payment counterpart: `debt_id`, `clear_amount` and `equivalent_id`.
    `clear_amount` is dropped on purpose - replaying the documented rule instead of the writer's own
    number is what makes criterion (b) independent - but the other two are dropped only because
    every cycle here lives in one equivalent, so this is evidence about a single-equivalent cycle.
    """

    return {
        (
            triangle.name(uuid.UUID(str(edge["debtor_id"]))),
            triangle.name(uuid.UUID(str(edge["creditor_id"]))),
        ): _atoms(edge["amount"])
        for edge in (intent or {}).get("cycle", [])
    }


async def _clearing_tx_id(factory, triangle: _Triangle) -> str:
    """The tx id of the one CLEARING transaction this triangle produced, scoped to its participants."""

    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Transaction.tx_id).where(
                    Transaction.type == "CLEARING",
                    Transaction.initiator_id.in_(triangle.participant_ids),
                )
            )
        ).scalars().all()
    assert len(rows) == 1, f"expected exactly one CLEARING transaction for this triangle: {rows}"
    return str(rows[0])


def _clearing_implied_by_recorded_cycle(pre_amounts: dict[tuple[str, str], int]) -> dict:
    """Criterion (b) replayed from the STORED pre-amounts: every edge drops by `min(amounts)`.

    `clear_amount` is also in the intent and is deliberately NOT used - replaying the documented rule
    rather than trusting the writer's own arithmetic is what makes (b) independent.
    """

    state = dict(pre_amounts)
    clear = min(state.values())
    for edge in state:
        state[edge] -= clear
    return {edge: value for edge, value in state.items() if value != 0}


def _observed_change(before: dict, after: dict) -> dict:
    change = {}
    for edge in set(before) | set(after):
        delta = after.get(edge, 0) - before.get(edge, 0)
        if delta != 0:
            change[edge] = delta
    return change


async def _entries_for_tx(factory, tx_id: str):
    """The journal's entries for one transaction's envelope, or None when there is no journal.

    Found through `tx_id` rather than through `identity`: design v2 §5 pins that column for exactly
    these two kinds (`UNIQUE(tx_id)`, `CHECK tx_id iff kind IN (PAYMENT, CLEARING)`), whereas the
    IDENTITY of a payment envelope is not fixed by the design at all, and a counterexample that
    guessed it would be red for the wrong reason once step 4 chose differently.
    """
    return await stored_rows(
        factory,
        f"SELECT e.flush_ordinal, e.effect, e.amount_before, e.amount_after, e.delta, "  # noqa: S608
        f"e.debtor_id, e.creditor_id FROM {ENTRIES_TABLE} e "
        f"JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.tx_id = :tx_id ORDER BY e.flush_ordinal",
        {"tx_id": tx_id},
    )


async def _journal_totals_per_edge(factory, triangle: _Triangle, tx_id: str):
    rows = await stored_rows(
        factory,
        f"SELECT e.debtor_id, e.creditor_id, SUM(e.delta) AS total "  # noqa: S608
        f"FROM {ENTRIES_TABLE} e JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.tx_id = :tx_id GROUP BY e.debtor_id, e.creditor_id",
        {"tx_id": tx_id},
    )
    if rows is None:
        return None
    return {
        (triangle.name(row["debtor_id"]), triangle.name(row["creditor_id"])): _atoms(row["total"])
        for row in rows
    }


# ==============================================================================================
# Driving the real owners
# ==============================================================================================


async def _prepare_payment(factory, triangle: _Triangle, path: list[str], amount: Decimal) -> str:
    """Create the transaction and run a REAL `PaymentEngine.prepare` over `path`.

    Nothing is hand-written into `prepare_locks`: those rows ARE the operation's intent, and
    criterion (b) checked against a hand-built fixture would be checking this test's opinion of the
    payment instead of the system's.
    """
    tx_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=getattr(triangle, path[0]).id,
                payload={
                    "routes": [
                        {
                            "path": [getattr(triangle, name).pid for name in path],
                            "amount": str(amount),
                        }
                    ]
                },
                state="NEW",
            )
        )
        await session.commit()
    async with factory() as session:
        await PaymentEngine(session).prepare(
            tx_id,
            [getattr(triangle, name).pid for name in path],
            amount,
            triangle.equivalent_id,
        )
    return tx_id


async def _intent_flows(factory, triangle: _Triangle, tx_id: str) -> list[tuple[str, str, int]]:
    """The payment's declared flows in atoms, read from `prepare_locks` BEFORE the commit deletes them.

    This is the snapshot design v2 §7 says the envelope's intent will carry ("validated flows per
    lock as exact scale-8 strings"). Until the envelope exists it has to be captured here.

    SORTED, and the limit is worth naming: the engine applies flows in the order its locks come
    back, which this tree does not fix, so criterion (b) is only order-independent for scenarios
    where it is. Both scenarios here are - each edge is touched by at most one flow and no flow's
    input depends on another's output - and the honest control below goes red if that changes.
    """
    async with factory() as fresh:
        locks = (
            await fresh.execute(select(PrepareLock.effects).where(PrepareLock.tx_id == tx_id))
        ).scalars().all()
    flows = [
        (
            triangle.name(uuid.UUID(flow["from"])),
            triangle.name(uuid.UUID(flow["to"])),
            _atoms(flow["amount"]),
        )
        for effects in locks
        for flow in (effects or {}).get("flows", [])
    ]
    return sorted(flows)


async def _audit(factory, tx_id: str) -> list[bool]:
    async with factory() as fresh:
        return list(
            (
                await fresh.execute(
                    select(IntegrityAuditLog.verification_passed).where(
                        IntegrityAuditLog.tx_id == tx_id
                    )
                )
            ).scalars().all()
        )


async def _tx_state(factory, tx_id: str) -> str | None:
    async with factory() as fresh:
        return (
            await fresh.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))
        ).scalar_one_or_none()


def _collapse_the_route(monkeypatch, triangle: _Triangle) -> list[tuple[str, str]]:
    """Make the engine write ONE `A -> C` obligation for a payment routed `A -> B -> C`.

    The smallest wrong writer that gets past every barrier: the first segment is dropped and the
    second writes `A -> C` for the same amount, so `A -> C` is written EXACTLY ONCE (binding
    condition 5). It is order-independent by construction, because the order the two locks come
    back in is not fixed by this tree.

    Deliberately NOT a bug in `_apply_flow`'s arithmetic: the point is a writer whose individual
    writes are all well-formed and whose TOTAL is right, because that is the writer no existing
    check can see.
    """
    original = PaymentEngine._apply_flow
    calls: list[tuple[str, str]] = []

    async def _wrapper(self, from_id, to_id, amount, equivalent_id):
        calls.append((triangle.name(from_id), triangle.name(to_id)))
        if len(calls) == 1:
            return None
        return await original(self, triangle.a.id, triangle.c.id, amount, equivalent_id)

    monkeypatch.setattr(PaymentEngine, "_apply_flow", _wrapper)
    return calls


def _under_clear_by_one_atom(monkeypatch):
    """Leave one atom on every edge, and ONLY while the clearing service is writing.

    A `set` listener on `Debt.amount` left armed would corrupt the seeding and the verification
    reads too, and the counterexample would be about a broken stand. It is armed around
    `_execute_clearing_with_amount` and disarmed on the way out, so the only assignments it touches
    are the service's own `debt.amount -= clear_amount` (`app/core/clearing/service.py:2019`).
    """
    armed = {"value": False, "hits": 0}
    original = ClearingService._execute_clearing_with_amount

    def _skim(_target, value, _oldvalue, _initiator):
        if not armed["value"]:
            return value
        armed["hits"] += 1
        return value + ATOM

    event.listen(Debt.amount, "set", _skim, retval=True)

    async def _wrapper(self, *args, **kwargs):
        armed["value"] = True
        try:
            return await original(self, *args, **kwargs)
        finally:
            armed["value"] = False

    monkeypatch.setattr(ClearingService, "_execute_clearing_with_amount", _wrapper)

    def _remove() -> None:
        event.remove(Debt.amount, "set", _skim)

    return armed, _remove


# ==============================================================================================
# C5 on PostgreSQL - an honest payment at full money size
# ==============================================================================================


@pytest.mark.asyncio
async def test_c5_p_the_journal_of_an_honest_payment_is_exact_at_full_money_size(
    serializable_factory,
) -> None:
    """C5, PostgreSQL, API-SHAPED. Both criteria, in integer atoms, at the top of the money domain.

    THE SCENARIO is design v2 §9 `C5`'s mutual pair, ten atoms below the encoding's ceiling: `A`
    owes `B` `999999999999.99999989`, `B` owes `A` two atoms less, and `A` pays `B` one atom more
    than `B` owes. The reverse debt is consumed exactly, one atom is left over, and the forward
    debt grows by it. Every value in the scenario is one or two atoms from its neighbour, so a
    journal that lost or gained a single atom anywhere would be visible.

    WHY TEN ATOMS BELOW THE CEILING AND NOT AT IT. `prepare` computes a segment's capacity as
    `limit - what the sender already owes + what the receiver owes back`
    (`app/core/payments/engine.py:719`), so seeding the forward edge AT the trustline limit leaves
    less capacity than the payment needs and `prepare` refuses with `RoutingException` (measured -
    "Available: 999999999999.99999997, Needed: 999999999999.99999998"). The headroom is the
    smallest that lets the honest payment through; the amounts are still at the top of the
    `NUMERIC(20, 8)` domain, which is what this tier is here to exercise.

    WHY NOT ON SQLITE. Above `2^26` the driver's float binding changes scale-8 values (design v2 §4,
    measured), so this arithmetic cannot be expressed there at all. That is the whole reason `C5`
    is marked `S+P` and not `S`.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so criterion (a) has nothing to sum.
    Criterion (b) is computable today and is asserted before it, so this test cannot pass having
    measured neither.
    MUTATION once step 4 exists: quantise `delta` through `float` anywhere on the write path - the
    atom-level equality below goes red while a `Decimal`-with-tolerance comparison would not.
    """
    triangle = await _seed_triangle(
        serializable_factory,
        trustlines=[("b", "a", FULL_SIZE), ("a", "b", FULL_SIZE)],
    )
    forward = FULL_SIZE - 10 * ATOM
    reverse = forward - 2 * ATOM
    payment = forward - ATOM
    try:
        async with serializable_factory() as session:
            async with debt_fixture_setup(session, label="mutual-edges"):
                session.add_all(
                    [
                        Debt(id=uuid.uuid4(), debtor_id=triangle.a.id, creditor_id=triangle.b.id,
                             equivalent_id=triangle.equivalent_id, amount=forward, version=0),
                        Debt(id=uuid.uuid4(), debtor_id=triangle.b.id, creditor_id=triangle.a.id,
                             equivalent_id=triangle.equivalent_id, amount=reverse, version=0),
                    ]
                )
            await session.commit()

        before = await _edges(serializable_factory, triangle)
        tx_id = await _prepare_payment(serializable_factory, triangle, ["a", "b"], payment)
        flows = await _intent_flows(serializable_factory, triangle, tx_id)

        async with serializable_factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(serializable_factory, triangle)

        # NON-VACUITY: the stand really holds the values it claims, to the atom, and the payment
        # really committed. `before` is read back from PostgreSQL, not assumed from the literals.
        assert before == {
            ("a", "b"): _atoms(forward),
            ("b", "a"): _atoms(reverse),
        }, f"stand: PostgreSQL did not store the seeded amounts exactly: {before}"
        assert await _tx_state(serializable_factory, tx_id) == "COMMITTED"
        assert flows == [("a", "b", _atoms(payment))], flows

        # CRITERION (b), computable today.
        implied = _payment_implied_by_intent(before, flows)
        assert implied == after, (
            f"criterion (b) fails on an HONEST payment: the intent {flows} implies {implied} and "
            f"the database holds {after}, in atoms. Either the independent algebra in this module "
            f"is not the rule the engine implements, or the engine has stopped implementing it."
        )
        # And the arithmetic is stated outright, so a silent change on BOTH sides cannot hide.
        assert after == {("a", "b"): _atoms(forward) + 1}, (
            f"stand: the payment did not consume the reverse debt exactly and leave one atom on "
            f"the forward edge: {after}"
        )

        # CRITERION (a). RED TODAY.
        entries = await _entries_for_tx(serializable_factory, tx_id)
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        totals = await _journal_totals_per_edge(serializable_factory, triangle, tx_id)
        assert totals == _observed_change(before, after), (
            f"criterion (a) fails in atoms: the journal's deltas per edge are {totals} and the "
            f"change the payment made is {_observed_change(before, after)}"
        )
    finally:
        await _drop_triangle(serializable_factory, triangle)


# ==============================================================================================
# C6 (i) on PostgreSQL - a payment whose route is a lie, with every lock in force
# ==============================================================================================


@pytest.mark.asyncio
async def test_c6_p_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today(
    serializable_factory, monkeypatch
) -> None:
    """C6 (i), PostgreSQL, the mandatory counterexample. DEFECT-SHAPED in (b), API-SHAPED in (a).

    THE WRITER. A payment prepared over `A -> B -> C` for `999999999999.99999999` writes a single
    `A -> C` obligation instead. `B`, whose capacity was reserved and whose trust was the reason the
    route existed, carries nothing.

    WHAT THIS TIER ADDS over the SQLite version, which is the reason design v2 marks `C6` `S+P`:
    here the payment really takes `pg_advisory_xact_lock` over the equivalent owner
    (`app/core/payments/engine.py:148-170`) and over each segment pair (`:240`), under
    `SERIALIZABLE`. On SQLite both helpers return at their first line. So this run answers the
    objection that the wrong writer only survives because the default tier is a toy.

    WHY NOTHING STOPS IT, checked in code and not assumed:

    * `check_payment_delta` (`engine.py:1596-1633`) compares PER-PARTICIPANT NET POSITIONS against
      the declared flows. Declared: `A: -x, B: 0, C: +x`. A single `A -> C` of `x` produces exactly
      that. It passes with the zero tolerance T1522 gave it.
    * `check_trust_limits` (`app/core/invariants.py:87-153`) is called from the payment path with
      `participant_pairs` built from the DECLARED flows (`engine.py:1300-1331`), so the pair
      `(A, C)` never enters the query.
    * the integrity checkpoint (`app/core/integrity.py:93-104`) DOES scan the whole equivalent, so
      the `C -> A` trustline in this stand is what makes `verification_passed` true - binding
      condition 5 requires it explicitly, because without it the audit row would be false for the
      wrong reason and the counterexample would prove something weaker.

    WHAT THE JOURNAL ADDS. Criterion (a) still holds: a faithful journal records `A -> C += x`,
    which is what happened. Criterion (b) fails, because the intent said `A -> B` and `B -> C`. The
    journal does not make the payment wrong; it makes the wrongness DECIDABLE.

    RED TODAY BECAUSE: there is no envelope and no entries, so criterion (a) cannot be evaluated.
    Everything before that assertion is computable today and is asserted first, so the failure
    quotes the committed wrong state, the passing audit row and the refutation (b) already produces.
    MUTATION once step 4 exists: journal the operation's INTENT as if it were its effects (write
    entries from the validated flows rather than from the flush plan) - criterion (a) then passes
    for a state that never existed, (b) passes too, and this counterexample goes silent, which is
    exactly the failure mode the two criteria exist to separate.
    """
    triangle = await _seed_triangle(
        serializable_factory,
        trustlines=[
            ("b", "a", FULL_SIZE),  # enables the declared flow A -> B
            ("c", "b", FULL_SIZE),  # enables the declared flow B -> C
            # Binding condition 5: without an ACTIVE C -> A line of at least the amount, the
            # whole-equivalent checkpoint would find the forged edge over its limit and write
            # verification_passed=false - and the counterexample would be about a barrier that
            # caught it, not about one that did not.
            ("c", "a", FULL_SIZE),
        ],
    )
    try:
        before = await _edges(serializable_factory, triangle)
        tx_id = await _prepare_payment(serializable_factory, triangle, ["a", "b", "c"], FULL_SIZE)
        flows = await _intent_flows(serializable_factory, triangle, tx_id)

        calls = _collapse_the_route(monkeypatch, triangle)
        async with serializable_factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(serializable_factory, triangle)
        audit = await _audit(serializable_factory, tx_id)
        state = await _tx_state(serializable_factory, tx_id)

        # NON-VACUITY: the wrong writer really ran, and `A -> C` was written exactly once.
        assert sorted(calls) == [("a", "b"), ("b", "c")], (
            f"stand: the engine did not apply the two declared segments exactly once each: {calls}"
        )
        assert before == {}, f"stand: the equivalent was not empty before the payment: {before}"

        # THE COMMITTED WRONG STATE, read on a session that is not the writer's, in atoms.
        assert after == {("a", "c"): _atoms(FULL_SIZE)}, (
            f"stand: the collapsed route did not produce a single A -> C obligation: {after}"
        )
        assert state == "COMMITTED", f"stand: the wrong payment did not commit: {state}"
        assert audit == [True], (
            f"stand: the integrity audit did not record this payment as verified ({audit}), so the "
            f"counterexample is no longer about a writer that passes every barrier. If this is a "
            f"real improvement, the barrier that caught it must be named and C6 rewritten around it."
        )

        # THE STAND, and it needs no journal: what the payment DECLARED - captured from the prepare
        # locks before the commit deleted them - disagrees with what it did. Asserted FIRST so this
        # test can never pass having measured neither criterion.
        declared = _payment_implied_by_intent(before, flows)
        assert flows == [
            ("a", "b", _atoms(FULL_SIZE)),
            ("b", "c", _atoms(FULL_SIZE)),
        ], flows
        assert declared == {
            ("a", "b"): _atoms(FULL_SIZE),
            ("b", "c"): _atoms(FULL_SIZE),
        }, declared
        assert declared != after, (
            "the declared route and the committed state agree, so this stand cannot tell a wrong "
            "writer from an honest one and nothing below it means anything"
        )

        # CRITERION (b), REPLAYED FROM THE INTENT AS THE ENVELOPE STORED IT.
        envelopes = await _envelope_intents_for_tx(serializable_factory, tx_id)
        assert envelopes is not None, (
            "a payment routed A -> B -> C committed a single A -> C obligation and was recorded as "
            "verified, and there is no envelope to read its declared intent out of. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "PAYMENT", (
            f"the committed payment left {envelopes} instead of exactly one PAYMENT envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", envelopes
        recorded = _recorded_payment_flows(triangle, envelopes[0]["intent"])
        assert recorded == flows, (
            f"the envelope's intent is not what the payment declared. Stored, in atoms: {recorded}. "
            f"The prepare locks, immediately before the commit deleted them: {flows}. An intent that "
            f"is the writer's own result cannot disagree with the result, and being able to disagree "
            f"is the entire reason it is recorded (design v2 §7)."
        )
        implied = _payment_implied_by_intent(before, recorded)
        assert implied == declared, (
            f"replaying the STORED intent gives {implied} while the prepared flows give {declared}; "
            f"criterion (b) is then not a check on the envelope at all"
        )
        assert implied != after, (
            "criterion (b) did not refute the collapsed route: the intent the envelope recorded "
            "implies exactly the state the wrong writer produced"
        )

        # CRITERION (a). This is the counterexample.
        entries = await _entries_for_tx(serializable_factory, tx_id)
        assert entries is not None, (
            f"on PostgreSQL, with the equivalent owner lock and both segment locks held under "
            f"SERIALIZABLE, a payment routed A -> B -> C committed a single A -> C obligation of "
            f"{FULL_SIZE}, was recorded as verified (verification_passed={audit}), left the "
            f"transaction {state} and passed check_payment_delta, check_trust_limits and "
            f"check_debt_symmetry - because the total is right and only the ROUTE is a lie. The "
            f"database holds {after} in atoms; the payment declared {implied}. "
            + missing_journal_tables(entries, ENTRIES_TABLE)
        )
        totals = await _journal_totals_per_edge(serializable_factory, triangle, tx_id)
        assert totals == _observed_change(before, after), (
            f"criterion (a) fails: the journal must record FAITHFULLY what the writer did, even - "
            f"especially - when what it did was wrong. Journal says {totals}, the database changed "
            f"by {_observed_change(before, after)}."
        )
    finally:
        await _drop_triangle(serializable_factory, triangle)


@pytest.mark.asyncio
async def test_c6_p_control_the_same_payment_without_the_wrapper_satisfies_criterion_b(
    serializable_factory,
) -> None:
    """C6, PostgreSQL, anti-vacuum control. GREEN today and after step 4.

    Design v2 §9 states it as the non-vacuity of `C6`: "без wrapper/event (b) PASS". Without it,
    criterion (b) could be failing for a reason with nothing to do with the collapsed route - a
    wrong direction convention in this module's algebra, a missed netting rule - and the
    counterexample above would look conclusive while measuring a bug in its own measuring stick.

    IT NOW RUNS THE MECHANISM IT IS A CONTROL FOR (round 3, 2026-09-13). The SQLite sibling was
    caught replaying `_intent_flows` - the test's own read of `PrepareLock.effects` - while the
    counterexample replays `_recorded_payment_flows` over the intent THE ENVELOPE STORED; this tier
    had the same defect at the same place. A positive control over the snapshot path says nothing
    about whether the stored-intent path recognises an honest payment, which is what "the refutation
    is not an artefact of the replay" has to mean. Both paths are asserted now, and the stored intent
    is required to agree with the prepare locks.

    COVERAGE LIMIT: see `_recorded_payment_flows` - the equivalent and the lock ids are projected
    away, so this is evidence for a single-equivalent route at full money size.

    MUTATION, MEASURED 2026-09-13 on this tier: store the payment intent with `"flows": []`
    (`app/core/payments/engine.py:1322`); the stored-intent half goes red and the `_intent_flows`
    half stays green.
    """
    triangle = await _seed_triangle(
        serializable_factory,
        trustlines=[("b", "a", FULL_SIZE), ("c", "b", FULL_SIZE), ("c", "a", FULL_SIZE)],
    )
    try:
        before = await _edges(serializable_factory, triangle)
        tx_id = await _prepare_payment(serializable_factory, triangle, ["a", "b", "c"], FULL_SIZE)
        flows = await _intent_flows(serializable_factory, triangle, tx_id)

        async with serializable_factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(serializable_factory, triangle)
        assert await _tx_state(serializable_factory, tx_id) == "COMMITTED"

        # NON-VACUITY, FIRST: the payment really committed the two-hop state at full money size.
        assert after == {
            ("a", "b"): _atoms(FULL_SIZE),
            ("b", "c"): _atoms(FULL_SIZE),
        }, after

        # HALF ONE, the snapshot path: this module's algebra is the rule the engine implements.
        assert _payment_implied_by_intent(before, flows) == after, (
            f"criterion (b) fails on an honest A -> B -> C payment: intent implies "
            f"{_payment_implied_by_intent(before, flows)}, database holds {after}. The algebra in "
            f"this module is not the rule the engine implements, and C6's refutation is worthless."
        )

        # HALF TWO, THE PATH C6-P REFUTES WITH: the envelope's own stored intent.
        envelopes = await _envelope_intents_for_tx(serializable_factory, tx_id)
        assert envelopes is not None, (
            "an honest A -> B -> C payment committed and left no envelope to read its intent out "
            "of. " + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "PAYMENT", (
            f"the honest payment left {envelopes} instead of exactly one PAYMENT envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", (
            f"the honest payment committed with its envelope {envelopes[0]['state']}: {envelopes}"
        )
        recorded = _recorded_payment_flows(triangle, envelopes[0]["intent"])
        assert recorded, (
            f"stand: the envelope's intent decoded to no flows at all, so the replay below would "
            f"trivially return the pre-state: {envelopes[0]['intent']}"
        )
        assert recorded == sorted(flows), (
            f"the envelope's stored intent {recorded} is not what the prepare locks authorised "
            f"{sorted(flows)} on an HONEST payment, so the two criteria are measured against two "
            f"different declarations."
        )
        assert _payment_implied_by_intent(before, recorded) == after, (
            f"criterion (b) fails on an honest payment replayed from the intent the ENVELOPE "
            f"stored: implied {_payment_implied_by_intent(before, recorded)}, database holds "
            f"{after}. C6-P's refutation of the collapsed route runs through this path."
        )
    finally:
        await _drop_triangle(serializable_factory, triangle)


# ==============================================================================================
# C6 (ii) on PostgreSQL - a clearing cycle that does not close, through the real interlock
# ==============================================================================================


@pytest.mark.asyncio
async def test_c6_p_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified(
    serializable_factory, monkeypatch
) -> None:
    """C6 (ii), PostgreSQL, the mandatory counterexample. DEFECT-SHAPED in (b), API-SHAPED in (a).

    THE WRITER. A cycle `A -> B -> C -> A` of `999999999999.99999999` on every edge is cleared. The
    service computes `clear_amount = min(amounts)` and subtracts it from every edge, which should
    delete all three. A listener adds one atom back to each subtraction, so every edge is left
    holding `0.00000001` and none is deleted.

    WHAT THIS TIER ADDS. `execute_clearing_with_amount` takes its one-connection interlock before
    delegating (`app/core/clearing/service.py:1487-1500`), and the cycle's debts are read
    `SELECT ... FOR UPDATE` (`:1750-1759`). On SQLite neither exists. So this is the under-clearing
    surviving the real concurrency machinery, not a version of it with the locks removed.

    WHY NOTHING STOPS IT:

    * `verify_clearing_neutrality` (`app/core/invariants.py:260-287`) compares each participant's
      NET POSITION before and after, exactly. Every participant owes one atom and is owed one atom,
      so every net position is unchanged and the check passes with no tolerance at all.
    * the whole-equivalent checkpoint finds three debts of `0.00000001` against limits far above
      them and writes `verification_passed=true` (`service.py:2052-2072`).
    * the clearing transaction reaches `COMMITTED` (`service.py:2088`).

    So the cycle is reported as cleared, the participants' positions are untouched, and three
    obligations that should not exist are now permanent - each too small to notice, with no record
    anywhere of what the operation intended.

    RED TODAY BECAUSE: there is no envelope carrying the intent (the cycle's debt ids and their
    pre-amounts under `FOR UPDATE`, design v2 §7) and no entries to sum, so criterion (a) cannot be
    evaluated. Everything before that is computable today and asserted first.
    MUTATION once step 4 exists: record the clearing's intent as the RESULT (the post-write
    amounts) instead of the pre-write amounts - criterion (b) then agrees with any outcome at all.
    """
    triangle = await _seed_triangle(
        serializable_factory,
        trustlines=[("b", "a", FULL_SIZE), ("c", "b", FULL_SIZE), ("a", "c", FULL_SIZE)],
    )
    cycle_edges = [("a", "b"), ("b", "c"), ("c", "a")]
    try:
        debt_ids: list[str] = []
        # Built outside the fixture block, added inside it: a loop is not fixture setup as far as
        # `fixture_block_violations` is concerned, and the rows and the single flush are unchanged.
        cycle_debts = [
            Debt(
                id=uuid.uuid4(),
                debtor_id=getattr(triangle, debtor).id,
                creditor_id=getattr(triangle, creditor).id,
                equivalent_id=triangle.equivalent_id,
                amount=FULL_SIZE,
                version=0,
            )
            for debtor, creditor in cycle_edges
        ]
        debt_ids.extend(str(debt.id) for debt in cycle_debts)
        async with serializable_factory() as session:
            async with debt_fixture_setup(session, label="cycle"):
                session.add_all(cycle_debts)
            await session.commit()

        before = await _edges(serializable_factory, triangle)
        armed, remove_listener = _under_clear_by_one_atom(monkeypatch)
        try:
            async with serializable_factory() as session:
                cleared = await ClearingService(session).execute_clearing_with_amount(
                    [{"debt_id": debt_id} for debt_id in debt_ids]
                )
        finally:
            remove_listener()

        after = await _edges(serializable_factory, triangle)
        async with serializable_factory() as fresh:
            clearing_tx = (
                await fresh.execute(
                    select(Transaction.tx_id, Transaction.state).where(
                        Transaction.type == "CLEARING",
                        # Scoped to this triangle: `geov0_test_ci` is shared, and an unscoped query
                        # would read a neighbouring session's clearing.
                        Transaction.initiator_id.in_(triangle.participant_ids),
                    )
                )
            ).all()
            audit = list(
                (
                    await fresh.execute(
                        select(IntegrityAuditLog.verification_passed).where(
                            IntegrityAuditLog.operation_type == "CLEARING",
                            IntegrityAuditLog.equivalent_code == triangle.equivalent_code,
                        )
                    )
                ).scalars().all()
            )

        # NON-VACUITY: the skim really fired once per edge, and the service really cleared.
        assert armed["hits"] == 3, (
            f"stand: the under-clearing listener fired {armed['hits']} times, not once per edge"
        )
        assert cleared == FULL_SIZE, f"stand: the service did not clear the cycle: {cleared!r}"
        assert before == {edge: _atoms(FULL_SIZE) for edge in cycle_edges}, before

        # THE COMMITTED WRONG STATE, read independently, in atoms.
        assert after == {edge: 1 for edge in cycle_edges}, (
            f"stand: the under-clearing did not leave exactly one atom on every edge: {after}"
        )
        assert [row.state for row in clearing_tx] == ["COMMITTED"], clearing_tx
        assert audit == [True], (
            f"stand: the clearing was not recorded as verified ({audit}), so this is no longer a "
            f"writer that passes every barrier"
        )

        # THE STAND, and it needs no journal: the documented rule applied to the independently
        # measured pre-state implies an empty equivalent, and the database is not empty.
        assert _clearing_implied_by_intent(before, cycle_edges) == {}, (
            f"stand: the documented clearing rule does not close this cycle in the module's own "
            f"algebra: {_clearing_implied_by_intent(before, cycle_edges)}"
        )

        clearing_tx_id = clearing_tx[0].tx_id

        # CRITERION (b), REPLAYED FROM THE INTENT AS THE ENVELOPE STORED IT.
        envelopes = await _envelope_intents_for_tx(serializable_factory, clearing_tx_id)
        assert envelopes is not None, (
            f"a cycle was reported as cleared while leaving {after} behind, and there is no envelope "
            f"to read the pre-amounts it acted on out of. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "CLEARING", (
            f"the clearing left {envelopes} instead of exactly one CLEARING envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", envelopes
        recorded_pre = _recorded_clearing_pre_amounts(triangle, envelopes[0]["intent"])
        assert recorded_pre == before, (
            f"the envelope recorded pre-amounts {recorded_pre} (atoms), and the cycle this clearing "
            f"acted on held {before}. The intent must be the state read under FOR UPDATE, not the "
            f"state the writer left behind - an intent taken from the outcome can only ever agree "
            f"with the outcome (design v2 §7, `C14`)."
        )
        implied = _clearing_implied_by_recorded_cycle(recorded_pre)
        assert implied == {}, (
            f"replaying the stored intent does not close the cycle: {implied}"
        )
        assert implied != after, (
            "criterion (b) did not refute the under-clearing: the intent the envelope recorded "
            "implies exactly the state the wrong writer left"
        )

        # CRITERION (a). This is the counterexample.
        entries = await _entries_for_tx(serializable_factory, clearing_tx_id)
        assert entries is not None, (
            f"on PostgreSQL, through the clearing interlock and with the cycle's debts held "
            f"FOR UPDATE, a cycle of {FULL_SIZE} on every edge was committed, reported as clearing "
            f"{cleared}, recorded as verified (verification_passed={audit}) and left every "
            f"participant's net position unchanged - while leaving one atom on every edge instead "
            f"of closing the cycle. verify_clearing_neutrality cannot see this, because one atom "
            f"owed and one atom owed to you net to nothing. "
            + missing_journal_tables(entries, ENTRIES_TABLE)
        )
        totals = await _journal_totals_per_edge(serializable_factory, triangle, clearing_tx_id)
        assert totals == _observed_change(before, after), (
            f"criterion (a) fails: journal says {totals}, the database changed by "
            f"{_observed_change(before, after)}"
        )
    finally:
        await _drop_triangle(serializable_factory, triangle)


@pytest.mark.asyncio
async def test_c6_p_control_the_same_cycle_without_the_listener_satisfies_criterion_b(
    serializable_factory,
) -> None:
    """C6 (ii), PostgreSQL, anti-vacuum control. GREEN today and after step 4.

    Without it, criterion (b) failing above could mean the clearing rule in this module's algebra is
    simply wrong - and the counterexample would be measuring its own arithmetic.

    IT NOW RUNS THE MECHANISM IT IS A CONTROL FOR (round 3, 2026-09-13), the same correction as the
    payment control above and as the SQLite sibling: it replayed `_clearing_implied_by_intent`, whose
    pre-state is the TEST's own read of `debts`, while the counterexample replays
    `_clearing_implied_by_recorded_cycle` over the pre-amounts THE ENVELOPE STORED. Both halves are
    asserted now, and the stored pre-amounts are required to equal the independently measured
    pre-state - the assertion that makes the intent a declaration instead of a copy of the outcome
    (design v2 §7, `C14`).

    COVERAGE LIMIT: see `_recorded_clearing_pre_amounts`.

    MUTATION, MEASURED 2026-09-13 on this tier: build the clearing intent's `cycle` amounts from the
    POST-state (`app/core/clearing/service.py:2037`); the stored-intent half goes red on the
    pre-amount comparison while the other half stays green.
    """
    triangle = await _seed_triangle(
        serializable_factory,
        trustlines=[("b", "a", FULL_SIZE), ("c", "b", FULL_SIZE), ("a", "c", FULL_SIZE)],
    )
    cycle_edges = [("a", "b"), ("b", "c"), ("c", "a")]
    try:
        debt_ids: list[str] = []
        # Built outside the fixture block, added inside it: a loop is not fixture setup as far as
        # `fixture_block_violations` is concerned, and the rows and the single flush are unchanged.
        cycle_debts = [
            Debt(
                id=uuid.uuid4(),
                debtor_id=getattr(triangle, debtor).id,
                creditor_id=getattr(triangle, creditor).id,
                equivalent_id=triangle.equivalent_id,
                amount=FULL_SIZE,
                version=0,
            )
            for debtor, creditor in cycle_edges
        ]
        debt_ids.extend(str(debt.id) for debt in cycle_debts)
        async with serializable_factory() as session:
            async with debt_fixture_setup(session, label="cycle"):
                session.add_all(cycle_debts)
            await session.commit()

        before = await _edges(serializable_factory, triangle)
        async with serializable_factory() as session:
            cleared = await ClearingService(session).execute_clearing_with_amount(
                [{"debt_id": debt_id} for debt_id in debt_ids]
            )

        after = await _edges(serializable_factory, triangle)

        # NON-VACUITY, FIRST: a full-size cycle really existed and really closed.
        assert cleared == FULL_SIZE, cleared
        assert before == {edge: _atoms(FULL_SIZE) for edge in cycle_edges}, (
            f"stand: the cycle this control clears is not full-size on every edge: {before}"
        )

        # HALF ONE, the test's own read of the pre-state.
        assert _clearing_implied_by_intent(before, cycle_edges) == after == {}, (
            f"criterion (b) fails on an honest clearing: implied "
            f"{_clearing_implied_by_intent(before, cycle_edges)}, database {after}"
        )

        # HALF TWO, THE PATH C6-P (ii) REFUTES WITH: the pre-amounts the envelope stored.
        clearing_tx_id = await _clearing_tx_id(serializable_factory, triangle)
        envelopes = await _envelope_intents_for_tx(serializable_factory, clearing_tx_id)
        assert envelopes is not None, (
            "an honest full-size cycle closed and left no envelope to read its pre-amounts out of. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "CLEARING", (
            f"the honest clearing left {envelopes} instead of exactly one CLEARING envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", (
            f"the honest clearing committed with its envelope {envelopes[0]['state']}: {envelopes}"
        )
        recorded_pre = _recorded_clearing_pre_amounts(triangle, envelopes[0]["intent"])
        assert recorded_pre == before, (
            f"the envelope recorded pre-amounts {recorded_pre} while the cycle held {before} on an "
            f"HONEST clearing. An intent taken from the outcome agrees with the outcome by "
            f"construction, and C6 (ii) turns on it being able to disagree."
        )
        assert _clearing_implied_by_recorded_cycle(recorded_pre) == after == {}, (
            f"criterion (b) fails on an honest clearing replayed from the pre-amounts the ENVELOPE "
            f"stored: implied {_clearing_implied_by_recorded_cycle(recorded_pre)}, database {after}"
        )
    finally:
        await _drop_triangle(serializable_factory, triangle)
