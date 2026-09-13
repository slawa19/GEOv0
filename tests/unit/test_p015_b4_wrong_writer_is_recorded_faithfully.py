"""Programme 015, `B4` step 2: `C5` and `C6` - the counterexample the whole journal is for.

WHAT THIS MODULE IS. Design v2 §9 calls `C6` the mandatory counterexample and the spec's binding
condition 5 spells out what it needs. It is the one that says why a debt journal is worth building
at all, so it is worth stating the claim plainly before the code:

    EVERY BARRIER THIS SYSTEM HAS TODAY CAN BE PASSED BY A WRITER THAT MOVES THE MONEY TO THE WRONG
    PLACE, as long as it moves the right TOTAL. A payment routed `A -> B -> C` may write a single
    `A -> C` obligation; a clearing cycle may leave one atom on every edge instead of closing it.
    Both commit. Both are audited. Both are recorded as verified.

The two criteria, named in design v2 §9 `C5` and reused by `C6`:

* criterion (a) - THE JOURNAL EQUALS THE CHANGE: for every edge, the sum of the journal's deltas
  equals the edge's final amount minus its initial amount, both read independently of the writer.
* criterion (b) - THE CHANGE EQUALS THE INTENT: replaying the operation's recorded intent through
  an independent integer-atom implementation of the documented rule reproduces the final state.

`C5` is the honest payment, where both criteria hold. `C6` is the wrong writer, where (a) still
holds - the journal records faithfully what the writer did - and (b) FAILS. That gap is the whole
product of step 4: without the journal there is no (a) to hold, and (b) has no recorded intent to
be checked against, so the wrong state is simply true.

TIER. SQLite, the default tier, which is also the application's default `DATABASE_URL`
(`app/config.py:55`). All money is inside `|v| < 2^26` (design v2 §4). Verdicts are read on a NEW
session; the payment and the clearing run on their own sessions, never on the fixture's.

READ `tests/p015_b4_support.py` for the import rule and the two kinds of red.

MARKER, HISTORICAL. This module carried `b4_counterexample` and was deselected from the canonical
gate while the debt journal did not exist. Step 4 slice C built it and REMOVED THE MARKER, not the
assertions: every test below still asserts exactly what it asserted while it was red, and each one
names in its docstring the mutation that must turn it red again.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, select

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
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    OPERATIONS_TABLE,
    missing_journal_tables,
    stored_rows,
)


#: One scale-8 atom. The clearing half of `C6` is wrong by exactly this much on every edge.
ATOM = Decimal("0.00000001")


# ==============================================================================================
# The stand: three participants, one equivalent, and the trustlines each scenario needs
# ==============================================================================================


class _Triangle:
    """Participants `a`, `b`, `c` in one equivalent, with a cleanup that is scoped to its own ids."""

    def __init__(self, equivalent, participants: dict[str, Participant]) -> None:
        self.equivalent = equivalent
        self.a = participants["a"]
        self.b = participants["b"]
        self.c = participants["c"]
        self.by_id = {p.id: name for name, p in participants.items()}

    def name(self, participant_id) -> str:
        return self.by_id.get(participant_id, str(participant_id))


async def _seed_triangle(factory, *, trustlines: list[tuple[str, str, str]]) -> _Triangle:
    """Three participants, one equivalent, and the named trustlines, committed before the test.

    `trustlines` entries are `(creditor, debtor, limit)` - the repository's direction convention,
    `from -> to` meaning creditor -> debtor and bounding the CREDITOR's risk
    (`AGENTS.md` §8, and `app/core/invariants.py:95-97`).
    """
    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code=f"C6{tag}", precision=2, is_active=True, metadata_={})
        people = {
            name: Participant(
                pid=f"C6_{name.upper()}_{tag}",
                display_name=name.upper(),
                public_key=f"pk_c6_{name}_{tag}",
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
                    limit=Decimal(limit),
                    status="active",
                )
            )
        await session.commit()
    return _Triangle(equivalent, people)


async def _drop_triangle(factory, triangle: _Triangle) -> None:
    """Remove exactly what this module created, in FK order, by id.

    `transactions.initiator_id` is RESTRICT (`app/db/models/transaction.py:13`) and
    `prepare_locks.tx_id` references it, so both go before the participants; `debts.equivalent_id`
    is RESTRICT since T1524, so the debts go before the equivalent. Nothing here leans on a cascade
    to do its work.
    """
    participant_ids = [triangle.a.id, triangle.b.id, triangle.c.id]
    async with factory() as session:
        tx_ids = (
            await session.execute(
                select(Transaction.tx_id).where(Transaction.initiator_id.in_(participant_ids))
            )
        ).scalars().all()
        # The debts and the journal go through the driver, and BEFORE the transactions: once the
        # journal is armed, `session.execute(delete(Debt))` is Core DML the write guard refuses, and
        # `debt_operations.tx_id` is a RESTRICT reference to `transactions.tx_id`, so an envelope
        # still standing would block the delete above it. See `tests/debt_setup.purge_test_ledger`.
        await purge_test_ledger(
            session, equivalent_ids=[triangle.equivalent.id], tx_ids=tx_ids
        )
        if tx_ids:
            await session.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
            await session.execute(delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids)))
            await session.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await session.execute(delete(TrustLine).where(TrustLine.equivalent_id == triangle.equivalent.id))
        await session.execute(delete(Participant).where(Participant.id.in_(participant_ids)))
        await session.execute(delete(Equivalent).where(Equivalent.id == triangle.equivalent.id))
        await session.commit()


async def _edges(factory, triangle: _Triangle) -> dict[tuple[str, str], Decimal]:
    """Every live edge of this equivalent, read on a NEW session, keyed `(debtor, creditor)`."""
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == triangle.equivalent.id
                )
            )
        ).all()
    return {
        (triangle.name(debtor), triangle.name(creditor)): Decimal(str(amount))
        for debtor, creditor, amount in rows
    }


# ==============================================================================================
# The independent algebra - criterion (b)
# ==============================================================================================


def _atoms(value: Decimal) -> int:
    return int((Decimal(value) / ATOM).to_integral_value())


def _apply_flow_in_atoms(state: dict[tuple[str, str], int], sender: str, receiver: str, amount: int):
    """The documented payment rule, in integers, written here and not imported.

    "Independent" is the load-bearing word. Importing `PaymentEngine._apply_flow` would make
    criterion (b) a tautology: a wrong writer would be checked against itself and would agree. This
    is the rule as `docs/ru` and `app/core/payments/engine.py:1468-1472` DESCRIBE it - reduce the
    receiver's existing debt to the sender first, put the remainder on the sender, then net any
    mutual pair - re-implemented in integer atoms so no float or quantisation can enter.
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


def _payment_implied_by_intent(
    before: dict[tuple[str, str], Decimal], flows: list[tuple[str, str, Decimal]]
) -> dict[tuple[str, str], Decimal]:
    """Criterion (b) for a payment: the state its intent implies, in exact atoms."""
    state = {edge: _atoms(amount) for edge, amount in before.items()}
    for sender, receiver, amount in flows:
        _apply_flow_in_atoms(state, sender, receiver, _atoms(amount))
    return {edge: value * ATOM for edge, value in state.items() if value != 0}


def _clearing_implied_by_intent(
    before: dict[tuple[str, str], Decimal], cycle: list[tuple[str, str]]
) -> dict[tuple[str, str], Decimal]:
    """Criterion (b) for a clearing cycle: every edge drops by `min(edge amounts)`, in atoms."""
    state = {edge: _atoms(amount) for edge, amount in before.items()}
    clear = min(state[edge] for edge in cycle)
    for edge in cycle:
        state[edge] -= clear
    return {edge: value * ATOM for edge, value in state.items() if value != 0}


#: Both scenarios here are owned by a transaction, so the envelope is found through `tx_id` rather
#: than through `identity`. Design v2 §5 pins that column for exactly these two kinds
#: (`UNIQUE(tx_id)`, `CHECK tx_id iff kind IN (PAYMENT, CLEARING)`), whereas the IDENTITY of a
#: payment envelope is not fixed by the design at all - and a counterexample that guessed it would
#: be red for the wrong reason once step 4 chose differently.
async def _entries_for_tx(factory, tx_id: str):
    return await stored_rows(
        factory,
        f"SELECT e.flush_ordinal, e.effect, e.amount_before, e.amount_after, e.delta, "  # noqa: S608
        f"e.debtor_id, e.creditor_id FROM {ENTRIES_TABLE} e "
        f"JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.tx_id = :tx_id ORDER BY e.flush_ordinal",
        {"tx_id": tx_id},
    )


async def _journal_totals_per_edge(factory, tx_id: str):
    """Criterion (a): the sum of the journal's deltas per edge, or None when there is no journal."""
    rows = await stored_rows(
        factory,
        f"SELECT e.debtor_id, e.creditor_id, SUM(e.delta) AS total "  # noqa: S608
        f"FROM {ENTRIES_TABLE} e JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.tx_id = :tx_id GROUP BY e.debtor_id, e.creditor_id",
        {"tx_id": tx_id},
    )
    if rows is None:
        return None
    return {(str(row["debtor_id"]), str(row["creditor_id"])): Decimal(str(row["total"])) for row in rows}


def _named_totals(triangle: _Triangle, totals: dict | None) -> dict:
    return {
        (triangle.name(uuid.UUID(debtor)), triangle.name(uuid.UUID(creditor))): total
        for (debtor, creditor), total in (totals or {}).items()
    }


# ==============================================================================================
# The intent AS THE ENVELOPE STORED IT - which is what criterion (b) has to be computed from
# ==============================================================================================
#
# WHY THIS EXISTS AND WHAT IT REPLACED (external review, 2026-09-13). Criterion (b) used to be
# computed from `_intent_flows` - a read of `PrepareLock.effects` taken by the test before the
# commit - and from a literal list of cycle edges. Neither touches `debt_operations.intent`, so a
# journal that stored the WRITER'S OWN RESULT as the operation's intent would have left both halves
# of `C6` green: the decisive counterexample was very nearly circular, because the thing step 4
# adds was the only thing not being read. Criterion (b) is now computed from the stored envelope,
# and the independently captured declaration is kept as the cross-check that makes the stored one
# falsifiable - which is design v2 `C14`'s property, asserted here on the default tier as well.


def _decoded_json(value):
    """JSON as Python. Raw `text()` SQL carries no type information, so a `JSON` column may come
    back as a string; a counterexample must not depend on which."""

    return json.loads(value) if isinstance(value, (str, bytes)) else value


async def _envelope_intents_for_tx(factory, tx_id: str):
    """The envelopes owning `tx_id`, intent decoded. `None` when `debt_operations` does not exist.

    `None` and `[]` are deliberately different answers, as everywhere else in this programme: "there
    is no envelope table" is not "the writer recorded nothing".
    """

    rows = await stored_rows(
        factory,
        f"SELECT kind, identity, state, intent FROM {OPERATIONS_TABLE} "  # noqa: S608
        f"WHERE tx_id = :tx_id",
        {"tx_id": tx_id},
    )
    if rows is None:
        return None
    return [dict(row, intent=_decoded_json(row["intent"])) for row in rows]


def _recorded_payment_flows(
    triangle: _Triangle, intent
) -> list[tuple[str, str, Decimal]]:
    """Every `{from, to, amount}` flow inside a STORED payment intent, named and sorted.

    Design v2 §7 fixes the CONTENT of a payment intent ("validated flows per lock as exact scale-8
    strings") and not its nesting, so this walks whatever shape step 4 chose rather than pinning a
    key path. Amounts are quantized to scale 8 because `"5"` and `"5.00000000"` are the same money
    and a counterexample that failed on the spelling would be testing a serializer.

    THE UUIDS ARE READ THROUGH `uuid.UUID`, which is what makes this tier-independent: the intent is
    JSON and carries the dashed canonical form, while the same ids in the journal's own columns are
    32 hex characters on SQLite and native `uuid` on PostgreSQL. A comparison written against either
    spelling directly would match nothing on the other tier.

    WHAT THIS DECODER PROJECTS AWAY, stated because round 3 asked what exactly the replay covers:
    each flow's `equivalent` and `lock_id`, and the grouping of flows into locks
    (`app/core/payments/engine.py:1319-1336` writes all four). What comes out is `(from, to, amount)`
    per flow, summed across locks. Every scenario in this module runs inside ONE equivalent, so
    dropping it loses nothing HERE - and that is exactly the limit: criterion (b) as this module
    checks it is evidence about single-equivalent routing, not about the whole intent. A payment that
    moved the right amounts between the right parties in the WRONG equivalent would pass this check.
    Multi-equivalent intent is not in `C6`'s scope (design v2 §9) and no test in this module claims
    it; a counterexample for it would have to be written, not inherited from this one.
    """

    found: list[tuple[str, str, Decimal]] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            if {"from", "to", "amount"} <= set(node):
                found.append(
                    (
                        triangle.name(uuid.UUID(str(node["from"]))),
                        triangle.name(uuid.UUID(str(node["to"]))),
                        Decimal(str(node["amount"])).quantize(Decimal("1E-8")),
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


def _recorded_clearing_pre_amounts(
    triangle: _Triangle, intent
) -> dict[tuple[str, str], Decimal]:
    """The cycle's PRE-AMOUNTS per named edge, as the stored clearing intent recorded them.

    PROJECTS AWAY, like its payment counterpart: `debt_id`, `clear_amount` and `equivalent_id`
    (`app/core/clearing/service.py:2032-2044` writes all of them). `clear_amount` is dropped on
    purpose - replaying the documented rule rather than the writer's own number is what makes
    criterion (b) independent - but `equivalent_id` and `debt_id` are dropped only because every
    cycle here lives in one equivalent and the edge names identify it. So this is evidence about a
    single-equivalent cycle, not about the whole intent.
    """

    return {
        (
            triangle.name(uuid.UUID(str(edge["debtor_id"]))),
            triangle.name(uuid.UUID(str(edge["creditor_id"]))),
        ): Decimal(str(edge["amount"])).quantize(Decimal("1E-8"))
        for edge in (intent or {}).get("cycle", [])
    }


def _clearing_implied_by_recorded_cycle(
    pre_amounts: dict[tuple[str, str], Decimal]
) -> dict[tuple[str, str], Decimal]:
    """Criterion (b) for a clearing, replayed from the STORED pre-amounts in integer atoms.

    The documented rule, and nothing read back from the result: every edge of the cycle drops by the
    minimum of the cycle's amounts. `clear_amount` is also in the intent and is deliberately NOT
    used - replaying the rule rather than trusting the writer's own arithmetic is what makes (b) an
    independent check instead of a restatement.
    """

    state = {edge: _atoms(amount) for edge, amount in pre_amounts.items()}
    clear = min(state.values())
    for edge in state:
        state[edge] -= clear
    return {edge: value * ATOM for edge, value in state.items() if value != 0}


async def _clearing_tx_id(factory, triangle: _Triangle) -> str:
    """The tx id of the one CLEARING transaction this triangle produced.

    Scoped to the triangle's participants for the same reason the counterexample above scopes its
    own read: the `db_session` fixture truncates every table on SQLite, so an unscoped query works
    today and would silently start reading someone else's rows the day this module moves.
    """

    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Transaction.tx_id).where(
                    Transaction.type == "CLEARING",
                    Transaction.initiator_id.in_([triangle.a.id, triangle.b.id, triangle.c.id]),
                )
            )
        ).scalars().all()
    assert len(rows) == 1, f"expected exactly one CLEARING transaction for this triangle: {rows}"
    return str(rows[0])


def _observed_change(
    before: dict[tuple[str, str], Decimal], after: dict[tuple[str, str], Decimal]
) -> dict[tuple[str, str], Decimal]:
    """`after - before` per edge, over the union of both key sets, zeros dropped."""
    change = {}
    for edge in set(before) | set(after):
        delta = after.get(edge, Decimal(0)) - before.get(edge, Decimal(0))
        if delta != 0:
            change[edge] = delta
    return change


# ==============================================================================================
# Driving a real payment
# ==============================================================================================


async def _prepare_payment(factory, triangle: _Triangle, path: list[str], amount: Decimal) -> str:
    """Create the transaction and run a REAL `PaymentEngine.prepare` over `path`.

    Nothing is hand-written into `prepare_locks`: the locks - which are the operation's INTENT and
    the only record of what the payment said it would do - must be produced by the application, or
    criterion (b) would be checked against a fixture instead of against the system.
    """
    tx_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=getattr(triangle, path[0]).id,
                payload={"routes": [{"path": [getattr(triangle, n).pid for n in path],
                                     "amount": str(amount)}]},
                state="NEW",
            )
        )
        await session.commit()
    async with factory() as session:
        await PaymentEngine(session).prepare(
            tx_id,
            [getattr(triangle, name).pid for name in path],
            amount,
            triangle.equivalent.id,
        )
    return tx_id


async def _intent_flows(factory, triangle: _Triangle, tx_id: str) -> list[tuple[str, str, Decimal]]:
    """The payment's declared flows, read from `prepare_locks` BEFORE the commit deletes them.

    This is the snapshot design v2 §7 says the envelope's intent will carry ("intent tx_id +
    validated flows per lock as exact scale-8 strings"). Until the envelope exists it has to be
    captured here, and `C14` on the PostgreSQL tier is the counterexample that ties the two together.
    """
    async with factory() as fresh:
        locks = (
            await fresh.execute(
                select(PrepareLock.effects).where(PrepareLock.tx_id == tx_id)
            )
        ).scalars().all()
    flows: list[tuple[str, str, Decimal]] = []
    for effects in locks:
        for flow in (effects or {}).get("flows", []):
            flows.append(
                (
                    triangle.name(uuid.UUID(flow["from"])),
                    triangle.name(uuid.UUID(flow["to"])),
                    Decimal(flow["amount"]),
                )
            )
    # Sorted, and the limit is worth naming: the engine applies flows in the order its locks come
    # back, so criterion (b) is only order-independent for scenarios where it is. Both scenarios in
    # this module are - each edge is touched by at most one flow and no flow's input depends on
    # another's output - and the `C6` control below would go red if that stopped being true.
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


# ==============================================================================================
# C5 - an honest payment, where both criteria hold
# ==============================================================================================


@pytest.mark.asyncio
async def test_c5_the_journal_of_an_honest_payment_equals_the_change_and_the_intent(
    db_session,
) -> None:
    """C5, API-SHAPED. Both criteria on a payment that does exactly what it said.

    THE SCENARIO, from design v2 §9 `C5`: `A` owes `B` 10 and `B` owes `A` 7 - a mutual pair, which
    exists because nothing nets debts that no payment has touched. `A` pays `B` 5. The engine
    reduces `B`'s debt to `A` by 5 (7 -> 2) in one flush, then nets the mutual pair in a second
    flush (10 -> 8 and 2 -> gone). Two flushes, three effects, and the intermediate value 2 is a
    state the database really held.

    WHY THE INTERMEDIATE VALUE MATTERS, which is why this is not "just" a smoke test: a journal
    that summarised the operation as one entry per edge would record `B -> A` as 7 -> gone and
    `A -> B` as 10 -> 8, and criterion (a) would still hold. It is `C6` that separates the two
    readings - but (a) has to be pinned on an honest payment first, or its failure there would be
    read as the counterexample rather than as a broken stand.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so criterion (a) has nothing to sum.
    Criterion (b) is computable today and is asserted before it, so this test cannot pass by having
    measured neither.
    MUTATION once step 4 exists: write one aggregated entry per edge per OPERATION instead of per
    flush - the entry count assertion goes red while the totals still agree.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory,
        trustlines=[("b", "a", "100"), ("a", "b", "100")],
    )
    try:
        async with factory() as session:
            async with debt_fixture_setup(session, label="mutual-edges"):
                session.add_all(
                    [
                        Debt(id=uuid.uuid4(), debtor_id=triangle.a.id, creditor_id=triangle.b.id,
                             equivalent_id=triangle.equivalent.id, amount=Decimal("10"), version=0),
                        Debt(id=uuid.uuid4(), debtor_id=triangle.b.id, creditor_id=triangle.a.id,
                             equivalent_id=triangle.equivalent.id, amount=Decimal("7"), version=0),
                    ]
                )
            await session.commit()

        before = await _edges(factory, triangle)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5"))
        flows = await _intent_flows(factory, triangle, tx_id)

        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(factory, triangle)

        # NON-VACUITY: the payment really ran and really moved money.
        assert await _tx_state(factory, tx_id) == "COMMITTED", await _tx_state(factory, tx_id)
        assert before == {("a", "b"): Decimal("10.00000000"), ("b", "a"): Decimal("7.00000000")}, before
        assert after == {("a", "b"): Decimal("8.00000000")}, (
            f"stand: the payment did not produce the state design v2 §9 C5 describes: {after}"
        )

        # CRITERION (b), computable today: the intent implies exactly the state that happened.
        implied = _payment_implied_by_intent(before, flows)
        assert flows == [("a", "b", Decimal("5.00000000"))], flows
        assert implied == after, (
            f"criterion (b) fails on an HONEST payment: the intent {flows} implies {implied} and "
            f"the database holds {after}. Either the independent algebra in this module is not the "
            f"rule the engine implements, or the engine has stopped implementing it."
        )

        # CRITERION (a). RED TODAY.
        entries = await _entries_for_tx(factory, tx_id)
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        observed = _observed_change(before, after)
        named = _named_totals(triangle, await _journal_totals_per_edge(factory, tx_id))
        assert named == observed, (
            f"criterion (a) fails: the journal's deltas per edge are {named} and the change the "
            f"payment actually made is {observed}"
        )

        # The payment made three effects across two flushes, and the FIRST flush is the one that
        # holds the intermediate value 2. The ordinals are asserted as a grouping rather than as
        # fixed numbers, because how step 4 numbers flushes that produced no effect is its choice
        # and not this counterexample's subject.
        ordinals = [row["flush_ordinal"] for row in entries]
        by_flush = {ordinal: ordinals.count(ordinal) for ordinal in ordinals}
        assert len(entries) == 3 and sorted(by_flush.values()) == [1, 2], (
            f"the payment's effects were not recorded as one flush reducing the reverse debt and a "
            f"second flush netting the pair: {entries}. The intermediate value the database really "
            f"held is what a per-operation summary loses."
        )
        first = [row for row in entries if row["flush_ordinal"] == min(ordinals)]
        assert len(first) == 1 and Decimal(str(first[0]["amount_after"])) == Decimal("2"), (
            f"the first flush did not record `B -> A` passing through 2 on its way to being "
            f"deleted: {entries}"
        )
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# C13 - a genuine replay, which never reaches the opening at all
# ==============================================================================================


async def _envelopes_for_tx(factory, tx_id: str):
    return await stored_rows(
        factory,
        f"SELECT id, kind, state, tx_id FROM {OPERATIONS_TABLE} WHERE tx_id = :tx_id",  # noqa: S608
        {"tx_id": tx_id},
    )


@pytest.mark.asyncio
async def test_c13_a_replayed_payment_commit_leaves_exactly_one_envelope(db_session) -> None:
    """C13, the REPLAY half, API-SHAPED. And design v2 §9 is wrong about this one.

    §9 `C13` says "engine.commit on COMMITTED tx (:1044-1053) -> open refused IntegrityError". It
    is not refused, because it never happens: `PaymentEngine.commit` returns at
    `app/core/payments/engine.py:1092-1101` as soon as it sees `tx.state == "COMMITTED"`, before
    any unit of work that could open an operation. (The quoted `:1044-1053` is also stale on this
    tree.) The binding condition 5 of the spec has the correct reading - "`C13` - отдельно дубликат
    открытия и настоящий replay с ранним возвратом" - and this is the second of those two: the
    duplicate OPENING is `tests/unit/test_p015_b4_entries_and_money.py`'s
    `test_c13_a_second_operation_with_a_spent_identity_is_refused`; this is the real replay.

    So the requirement is not a refusal. It is that a replay changes NOTHING: one envelope, still
    exactly one after the second call, the same effects, the same transaction state. A journal that
    opened an operation on the early-return path would write a second envelope recording an
    operation that did no work, and step 6 would see an effect-free operation it cannot place.

    RED TODAY BECAUSE: `debt_operations` does not exist, so "exactly one envelope" has nothing to
    count. The non-vacuity assertion is placed first and says so; the replay itself is real and is
    asserted before it.
    MUTATION once step 4 exists: open the operation before the `tx.state == "COMMITTED"` check
    instead of after it - two envelopes for one payment, and `UNIQUE(tx_id)` turns an idempotent
    replay into an `IntegrityError` for the caller.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    try:
        tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5"))
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)
        after_first = await _edges(factory, triangle)

        async with factory() as session:
            replayed = await PaymentEngine(session).commit(tx_id)

        after_replay = await _edges(factory, triangle)

        # NON-VACUITY: the replay really ran and really took the early-return path - it returned
        # True without touching anything. Without this the test would pass for a second call that
        # raised, which is a different behaviour entirely.
        assert replayed is True, f"stand: the replayed commit did not return normally: {replayed!r}"
        assert after_first == {("a", "b"): Decimal("5.00000000")}, after_first
        assert after_replay == after_first, (
            f"stand: the replay changed the money ({after_first} -> {after_replay}); this is no "
            f"longer a test about the journal"
        )
        assert await _tx_state(factory, tx_id) == "COMMITTED"

        envelopes = await _envelopes_for_tx(factory, tx_id)
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert len(envelopes) == 1, (
            f"a replayed payment commit left {len(envelopes)} envelopes: {envelopes}. A replay does "
            f"no work and must record none; `UNIQUE(tx_id)` would otherwise turn an idempotent "
            f"call into an IntegrityError."
        )
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_c13_a_replayed_clearing_leaves_exactly_one_envelope(db_session) -> None:
    """C13, the clearing REPLAY half, API-SHAPED.

    `ClearingService` recognises a replay by an `execution_tx_id` derived as a uuid5 over the
    sorted cycle debt ids (`app/core/clearing/service.py:1729-1748`), so calling it twice with the
    same cycle returns the first execution's amount without writing anything. The journal must
    behave the same way: one envelope for the execution that happened, none for the replay.

    RED TODAY BECAUSE: `debt_operations` does not exist.
    MUTATION once step 4 exists: open the operation before the replay lookup - the second call
    writes an envelope for an execution that never touched a debt.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")]
    )
    cycle_edges = [("a", "b"), ("b", "c"), ("c", "a")]
    try:
        debt_ids = []
        # Built outside the fixture block, added inside it: a loop is not fixture setup as far as
        # `fixture_block_violations` is concerned, and the rows and the single flush are unchanged.
        cycle_debts = [
            Debt(
                id=uuid.uuid4(),
                debtor_id=getattr(triangle, debtor).id,
                creditor_id=getattr(triangle, creditor).id,
                equivalent_id=triangle.equivalent.id,
                amount=Decimal("10"),
                version=0,
            )
            for debtor, creditor in cycle_edges
        ]
        debt_ids.extend(str(debt.id) for debt in cycle_debts)
        async with factory() as session:
            async with debt_fixture_setup(session, label="cycle"):
                session.add_all(cycle_debts)
            await session.commit()

        cycle = [{"debt_id": debt_id} for debt_id in debt_ids]
        async with factory() as session:
            first = await ClearingService(session).execute_clearing_with_amount(cycle)
        async with factory() as session:
            replayed = await ClearingService(session).execute_clearing_with_amount(cycle)

        after = await _edges(factory, triangle)

        # NON-VACUITY: the replay really was recognised as one - same amount back, nothing written.
        assert first == Decimal("10"), first
        assert replayed == Decimal("10"), (
            f"stand: the replayed clearing returned {replayed!r} instead of the first execution's "
            f"amount, so it was not recognised as a replay and this test measures something else"
        )
        assert after == {}, f"stand: the cycle did not close: {after}"

        async with factory() as fresh:
            clearing_tx_ids = list(
                (
                    await fresh.execute(
                        select(Transaction.tx_id).where(
                            Transaction.type == "CLEARING",
                            Transaction.initiator_id.in_(
                                [triangle.a.id, triangle.b.id, triangle.c.id]
                            ),
                        )
                    )
                ).scalars().all()
            )
        assert len(clearing_tx_ids) == 1, clearing_tx_ids

        envelopes = await _envelopes_for_tx(factory, clearing_tx_ids[0])
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert len(envelopes) == 1, (
            f"a replayed clearing left {len(envelopes)} envelopes: {envelopes}"
        )
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# C6 (i) - a payment whose route is a lie
# ==============================================================================================


def _collapse_the_route(monkeypatch, triangle: _Triangle) -> list[tuple[str, str]]:
    """Make the engine write ONE `A -> C` obligation for a payment routed `A -> B -> C`.

    The wrapper is the smallest wrong writer that gets past every barrier this system has: the
    first segment is dropped, and the second writes `A -> C` for the same amount, so
    `A -> C` is written EXACTLY ONCE (binding condition 5). It is deliberately NOT a bug injected
    into `_apply_flow`'s own arithmetic: the point is a writer whose individual writes are all
    well-formed and whose TOTAL is right, because that is the writer no existing check can see.
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


@pytest.mark.asyncio
async def test_c6_a_payment_that_writes_the_wrong_edge_passes_every_barrier_today(
    db_session, monkeypatch
) -> None:
    """C6 (i), the mandatory counterexample. DEFECT-SHAPED in (b), API-SHAPED in (a).

    THE WRITER. A payment is prepared over the route `A -> B -> C` for 5. A wrapper collapses the
    two segments into a single `A -> C` obligation. `B`, whose capacity was reserved and whose trust
    was the reason the route was chosen, ends up carrying nothing.

    WHY NOTHING STOPS IT, checked in code and not assumed:

    * `check_payment_delta` (`app/core/payments/engine.py:1596-1633`) compares PER-PARTICIPANT NET
      POSITIONS against the declared flows. The declared flows imply `A: -5, B: 0, C: +5`; a single
      `A -> C` of 5 produces exactly `A: -5, B: 0, C: +5`. It passes, with the zero tolerance T1522
      gave it.
    * `check_trust_limits` (`app/core/invariants.py:87-153`) is called from the payment path with
      `participant_pairs` built from the DECLARED flows (`engine.py:1300-1331`), so the pair
      `(A, C)` is never in the query at all.
    * the integrity checkpoint (`app/core/integrity.py:93-104`) DOES scan the whole equivalent, so
      the `C -> A` trustline in this stand is what makes `verification_passed` true - binding
      condition 5 requires it explicitly, because without it the audit row would be false for the
      wrong reason and the counterexample would prove something weaker.
    * `check_debt_symmetry` sees no mutual pair.

    WHAT THE JOURNAL ADDS. Criterion (a) still holds - a faithful journal records `A -> C += 5`,
    which is what happened. Criterion (b) fails, because the intent said `A -> B` and `B -> C`. The
    journal does not make the payment wrong; it makes the wrongness DECIDABLE, and that decision is
    the entire product of step 4.

    WHERE CRITERION (b) IS READ FROM, and it changed on 2026-09-13. It used to be computed from a
    `PrepareLock.effects` snapshot this test took itself, which never touches the envelope - so a
    journal that stored the writer's own RESULT as the operation's intent would have left this
    counterexample green, and the thing step 4 adds was the only thing not being read. (b) is now
    replayed from `debt_operations.intent`, and the snapshot is kept as the cross-check that makes
    the stored intent falsifiable: the envelope must record what the payment DECLARED, which is what
    the prepare locks held immediately before the commit deleted them.

    RED BEFORE STEP 4 BECAUSE: there is no envelope and no entries, so neither criterion can be
    evaluated at all. The refutation that needs no journal - the declared flows disagree with the
    committed state - is asserted first, so this test cannot pass having measured neither criterion.
    MUTATIONS, and there are now two that are separable:
    * journal the operation's INTENT as if it were its effects (write entries from the validated
      flows rather than from the flush plan) - criterion (a) then passes for a state that never
      existed;
    * store the writer's RESULT as the intent (overwrite `debt_operations.intent` with what the
      flush actually did) - the cross-check against the prepare-lock snapshot goes red, and so does
      (b), because an intent read back from the outcome cannot disagree with it. Measured
      2026-09-13: with the pre-correction assertions this mutation left the test green.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory,
        trustlines=[
            ("b", "a", "100"),  # enables the declared flow A -> B
            ("c", "b", "100"),  # enables the declared flow B -> C
            # Binding condition 5: without an ACTIVE C -> A line of at least the amount, the
            # whole-equivalent checkpoint would find the forged edge over its limit and write
            # verification_passed=false - and the counterexample would be about a barrier that
            # caught it, not about one that did not.
            ("c", "a", "100"),
        ],
    )
    try:
        before = await _edges(factory, triangle)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        flows = await _intent_flows(factory, triangle, tx_id)

        calls = _collapse_the_route(monkeypatch, triangle)
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(factory, triangle)
        audit = await _audit(factory, tx_id)
        state = await _tx_state(factory, tx_id)

        # NON-VACUITY: the wrong writer really ran, and `A -> C` was written exactly once.
        #
        # SORTED, and measured rather than assumed: `_load_prepare_locks` returns the two segments
        # in an order this tree does not fix (observed `[('b','c'), ('a','b')]` on 2026-09-12), so
        # pinning it would make this counterexample fail on an implementation detail it does not
        # depend on. The wrapper is order-independent by construction - it drops whichever segment
        # comes first and rewrites the second - so `A -> C` is written exactly once either way,
        # which is what binding condition 5 requires.
        assert sorted(calls) == [("a", "b"), ("b", "c")], (
            f"stand: the engine did not apply the two declared segments exactly once each: {calls}"
        )
        assert before == {}, f"stand: the equivalent was not empty before the payment: {before}"

        # THE COMMITTED WRONG STATE, read on a session that is not the writer's.
        assert after == {("a", "c"): Decimal("5.00000000")}, (
            f"stand: the collapsed route did not produce a single A -> C obligation: {after}"
        )
        assert state == "COMMITTED", f"stand: the wrong payment did not commit: {state}"
        assert audit == [True], (
            f"stand: the integrity audit did not record this payment as verified ({audit}), so the "
            f"counterexample is no longer about a writer that passes every barrier. If this is a "
            f"real improvement, the barrier that caught it must be named and C6 rewritten around it."
        )

        # THE STAND, and it needs no journal: what the payment DECLARED - captured from the
        # prepare locks before the commit deleted them - disagrees with what it did. Asserted FIRST
        # so this test can never pass having measured neither criterion.
        declared = _payment_implied_by_intent(before, flows)
        assert sorted(flows) == [
            ("a", "b", Decimal("5.00000000")),
            ("b", "c", Decimal("5.00000000")),
        ], flows
        assert declared == {
            ("a", "b"): Decimal("5.00000000"),
            ("b", "c"): Decimal("5.00000000"),
        }, declared
        assert declared != after, (
            "the declared route and the committed state agree, so this stand cannot tell a wrong "
            "writer from an honest one and nothing below it means anything"
        )

        # CRITERION (b), REPLAYED FROM THE INTENT AS THE ENVELOPE STORED IT.
        envelopes = await _envelope_intents_for_tx(factory, tx_id)
        assert envelopes is not None, (
            "a payment routed A -> B -> C committed a single A -> C obligation of 5 and was "
            "recorded as verified, and there is no envelope to read its declared intent out of. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "PAYMENT", (
            f"the committed payment left {envelopes} instead of exactly one PAYMENT envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", (
            f"the payment committed with its envelope {envelopes[0]['state']}: {envelopes}"
        )
        recorded = _recorded_payment_flows(triangle, envelopes[0]["intent"])
        assert recorded == sorted(flows), (
            f"the envelope's intent is not what the payment declared. Stored: {recorded}. The "
            f"prepare locks, immediately before the commit deleted them: {sorted(flows)}. An intent "
            f"that is the writer's own result cannot disagree with the result, and being able to "
            f"disagree is the entire reason it is recorded (design v2 §7)."
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

        # CRITERION (a). RED TODAY, and this is the counterexample.
        entries = await _entries_for_tx(factory, tx_id)
        assert entries is not None, (
            f"a payment routed A -> B -> C committed a single A -> C obligation of 5, was recorded "
            f"as verified (verification_passed={audit}), left the transaction {state}, and passed "
            f"check_payment_delta, check_trust_limits and check_debt_symmetry - because the total "
            f"is right and only the ROUTE is a lie. The database now holds {after}; the payment "
            f"declared {implied}. "
            + missing_journal_tables(entries, ENTRIES_TABLE)
        )
        named = _named_totals(triangle, await _journal_totals_per_edge(factory, tx_id))
        assert named == _observed_change(before, after), (
            f"criterion (a) fails: the journal must record FAITHFULLY what the writer did, even - "
            f"especially - when what it did was wrong. Journal says {named}, the database changed "
            f"by {_observed_change(before, after)}."
        )
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_c6_control_the_same_payment_without_the_wrapper_satisfies_criterion_b(
    db_session,
) -> None:
    """C6, anti-vacuum control. GREEN today and after step 4.

    Design v2 §9 states it as the non-vacuity of `C6`: "без wrapper/event (b) PASS". Without this,
    criterion (b) could be failing for a reason that has nothing to do with the collapsed route -
    a wrong direction convention in this module's algebra, a missed netting rule - and the
    counterexample above would look conclusive while measuring a bug in its own measuring stick.

    IT NOW RUNS THE MECHANISM IT IS A CONTROL FOR, which round 3 found it did not. Until 2026-09-13
    this control replayed `_intent_flows` - a read of `PrepareLock.effects` the TEST takes before the
    commit - while the counterexample above replays `_recorded_payment_flows` over the intent THE
    ENVELOPE STORED. Those are different paths through different code: a positive control over the
    snapshot path says nothing about whether the stored-intent path can recognise an honest payment,
    so "the refutation is not an artefact of the replay" was being claimed from a measurement that
    never touched the replay. Both paths are now asserted, and the envelope's intent is additionally
    required to AGREE with the prepare-lock snapshot - which is the statement that ties the two.

    WHAT IT COVERS AND WHAT IT DOES NOT: see `_recorded_payment_flows` - the decoder projects the
    equivalent and the lock ids away, so this is evidence for a single-equivalent route.

    MUTATION, MEASURED 2026-09-13: store the payment intent with `"flows": []`
    (`app/core/payments/engine.py:1322`). The stored-intent half below goes red - the replay then
    implies the pre-state instead of the committed one - while the `_intent_flows` half stays green,
    which is precisely the gap that existed before this was added.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("c", "a", "100")]
    )
    try:
        before = await _edges(factory, triangle)
        tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
        flows = await _intent_flows(factory, triangle, tx_id)

        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)

        after = await _edges(factory, triangle)
        assert await _tx_state(factory, tx_id) == "COMMITTED"

        # NON-VACUITY, FIRST: the payment really committed the two-hop state, so a criterion that
        # comes out satisfied below was compared against something.
        assert after == {
            ("a", "b"): Decimal("5.00000000"),
            ("b", "c"): Decimal("5.00000000"),
        }, after

        # HALF ONE, the snapshot path: this module's algebra is the rule the engine implements.
        assert _payment_implied_by_intent(before, flows) == after, (
            f"criterion (b) fails on an honest A -> B -> C payment: intent implies "
            f"{_payment_implied_by_intent(before, flows)}, database holds {after}. The algebra in "
            f"this module is not the rule the engine implements, and C6's refutation is worthless."
        )

        # HALF TWO, THE PATH C6 ACTUALLY REFUTES WITH: the envelope's own stored intent, decoded and
        # replayed by the same two functions the counterexample uses.
        envelopes = await _envelope_intents_for_tx(factory, tx_id)
        assert envelopes is not None, (
            "an honest A -> B -> C payment committed and left no envelope to read its intent out "
            "of, so C6's refutation cannot be shown to work on an honest payment. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
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
            f"{sorted(flows)} on an HONEST payment. Then the two criteria are measured against two "
            f"different declarations and C6's gap between them cannot be attributed to the writer."
        )
        assert _payment_implied_by_intent(before, recorded) == after, (
            f"criterion (b) fails on an honest payment when it is replayed from the intent the "
            f"ENVELOPE stored: implied {_payment_implied_by_intent(before, recorded)}, database "
            f"holds {after}. C6's refutation of the collapsed route runs through exactly this path, "
            f"so a failure here makes that refutation an artefact of the path rather than a finding "
            f"about the writer."
        )
    finally:
        await _drop_triangle(factory, triangle)


# ==============================================================================================
# C6 (ii) - a clearing cycle that does not close
# ==============================================================================================


def _under_clear_by_one_atom(monkeypatch) -> dict:
    """Leave one atom on every edge, and ONLY while the clearing service is writing.

    A `set` listener on `Debt.amount` with `retval=True` would otherwise corrupt the seeding and
    the verification reads as well, and the counterexample would be about a broken stand. It is
    armed around `_execute_clearing_with_amount` and disarmed on the way out, so the only
    assignments it touches are the service's own `debt.amount -= clear_amount`
    (`app/core/clearing/service.py:2019`).
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


@pytest.mark.asyncio
async def test_c6_a_clearing_cycle_that_leaves_one_atom_on_every_edge_is_still_verified(
    db_session, monkeypatch
) -> None:
    """C6 (ii), the mandatory counterexample, clearing half. DEFECT-SHAPED in (b), API-SHAPED in (a).

    THE WRITER. A 10/10/10 cycle `A -> B -> C -> A` is cleared. The service computes
    `clear_amount = min(amounts) = 10` and subtracts it from every edge, which should delete all
    three. A listener adds one atom back to each subtraction, so every edge is left holding
    `0.00000001` and none is deleted.

    WHY NOTHING STOPS IT:

    * `verify_clearing_neutrality` (`app/core/invariants.py:260-287`) compares each participant's
      NET POSITION before and after, exactly. Every participant owes one atom and is owed one atom,
      so every net position is unchanged and the check passes with no tolerance at all.
    * the whole-equivalent checkpoint finds three debts of `0.00000001` against limits of 100 and
      writes `verification_passed=true` (`app/core/clearing/service.py:2052-2072`).
    * the clearing transaction reaches `COMMITTED` (`service.py:2088`).

    So the cycle is reported as cleared, the participants' positions are untouched, and three
    obligations that should not exist are now permanent - each too small to notice, and there is no
    record anywhere that says what the operation intended to do.

    WHERE CRITERION (b) IS READ FROM, and it changed on 2026-09-13. It used to be computed from a
    literal list of cycle edges and a `before` read by this test - nothing from the envelope - so
    the mutation this docstring names could not redden it: recording the POST-write amounts as the
    intent still leaves `min(amounts)` equal to every amount, the replay still implies an empty
    equivalent, and `implied != after` still holds. (b) is now replayed from the pre-amounts
    `debt_operations.intent` actually stored, and those are additionally checked against the
    independently measured `before` - which is what the mutation destroys and what design v2 `C14`
    requires of the clearing envelope.

    RED BEFORE STEP 4 BECAUSE: there is no envelope carrying the intent (the cycle's debt ids and
    their pre-amounts, design v2 §7) and no entries to sum. The part that needs no journal - the
    documented rule applied to an independently measured `before` disagrees with the committed
    state - is asserted first.
    MUTATION: record the clearing's intent as the RESULT (the post-write amounts) instead of the
    pre-write amounts captured under `FOR UPDATE`. The stored pre-amounts then differ from the
    measured ones and this test goes red on that comparison. Measured 2026-09-13: with the
    pre-correction assertions the same mutation left it green.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory,
        trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")],
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
                equivalent_id=triangle.equivalent.id,
                amount=Decimal("10"),
                version=0,
            )
            for debtor, creditor in cycle_edges
        ]
        debt_ids.extend(str(debt.id) for debt in cycle_debts)
        async with factory() as session:
            async with debt_fixture_setup(session, label="cycle"):
                session.add_all(cycle_debts)
            await session.commit()

        before = await _edges(factory, triangle)
        armed, remove_listener = _under_clear_by_one_atom(monkeypatch)
        try:
            async with factory() as session:
                cleared = await ClearingService(session).execute_clearing_with_amount(
                    [{"debt_id": debt_id} for debt_id in debt_ids]
                )
        finally:
            remove_listener()

        after = await _edges(factory, triangle)
        async with factory() as fresh:
            clearing_tx = (
                await fresh.execute(
                    select(Transaction.tx_id, Transaction.state, Transaction.payload).where(
                        Transaction.type == "CLEARING",
                        # Scoped to this triangle. The `db_session` fixture truncates every table
                        # on SQLite, so an unscoped query would work today - and would silently
                        # start reading someone else's rows the day this module moves.
                        Transaction.initiator_id.in_(
                            [triangle.a.id, triangle.b.id, triangle.c.id]
                        ),
                    )
                )
            ).all()
            audit = (
                await fresh.execute(
                    select(IntegrityAuditLog.verification_passed).where(
                        IntegrityAuditLog.operation_type == "CLEARING",
                        IntegrityAuditLog.equivalent_code == triangle.equivalent.code,
                    )
                )
            ).scalars().all()

        # NON-VACUITY: the skim really fired, once per edge, and the service really ran.
        assert armed["hits"] == 3, (
            f"stand: the under-clearing listener fired {armed['hits']} times, not once per edge"
        )
        assert cleared == Decimal("10"), f"stand: the service did not clear the cycle: {cleared!r}"
        assert before == {edge: Decimal("10.00000000") for edge in cycle_edges}, before

        # THE COMMITTED WRONG STATE, read independently.
        assert after == {edge: ATOM for edge in cycle_edges}, (
            f"stand: the under-clearing did not leave exactly one atom on every edge: {after}"
        )
        assert [row.state for row in clearing_tx] == ["COMMITTED"], clearing_tx
        assert list(audit) == [True], (
            f"stand: the clearing was not recorded as verified ({list(audit)}), so this is no "
            f"longer a writer that passes every barrier"
        )

        # THE STAND, and it needs no journal: the documented rule applied to the independently
        # measured pre-state implies an empty equivalent, and the database is not empty.
        assert _clearing_implied_by_intent(before, cycle_edges) == {}, (
            f"stand: the documented clearing rule does not close a 10/10/10 cycle in this module's "
            f"own algebra: {_clearing_implied_by_intent(before, cycle_edges)}"
        )

        clearing_tx_id = clearing_tx[0].tx_id

        # CRITERION (b), REPLAYED FROM THE INTENT AS THE ENVELOPE STORED IT.
        envelopes = await _envelope_intents_for_tx(factory, clearing_tx_id)
        assert envelopes is not None, (
            f"a 10/10/10 cycle was reported as cleared while leaving {after} behind, and there is "
            f"no envelope to read the pre-amounts it acted on out of. "
            + missing_journal_tables(envelopes, OPERATIONS_TABLE)
        )
        assert len(envelopes) == 1 and envelopes[0]["kind"] == "CLEARING", (
            f"the clearing left {envelopes} instead of exactly one CLEARING envelope"
        )
        assert envelopes[0]["state"] == "COMPLETED", (
            f"the clearing committed with its envelope {envelopes[0]['state']}: {envelopes}"
        )
        recorded_pre = _recorded_clearing_pre_amounts(triangle, envelopes[0]["intent"])
        assert recorded_pre == before, (
            f"the envelope recorded pre-amounts {recorded_pre}, and the cycle this clearing acted "
            f"on held {before}. The intent must be the state read under FOR UPDATE, not the state "
            f"the writer left behind - an intent taken from the outcome can only ever agree with "
            f"the outcome (design v2 §7, `C14`)."
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
        entries = await _entries_for_tx(factory, clearing_tx_id)
        assert entries is not None, (
            f"a clearing cycle of 10/10/10 was committed, reported as clearing 10, recorded as "
            f"verified (verification_passed={list(audit)}) and left every participant's net "
            f"position unchanged - while leaving {after} behind instead of closing the cycle. "
            f"verify_clearing_neutrality cannot see this, because one atom owed and one atom owed "
            f"to you net to nothing. " + missing_journal_tables(entries, ENTRIES_TABLE)
        )
        named = _named_totals(triangle, await _journal_totals_per_edge(factory, clearing_tx_id))
        assert named == _observed_change(before, after), (
            f"criterion (a) fails: journal says {named}, the database changed by "
            f"{_observed_change(before, after)}"
        )
    finally:
        await _drop_triangle(factory, triangle)


@pytest.mark.asyncio
async def test_c6_control_the_same_cycle_without_the_listener_satisfies_criterion_b(
    db_session,
) -> None:
    """C6 (ii), anti-vacuum control. GREEN today and after step 4.

    Without it, criterion (b) failing above could mean the clearing rule in this module's algebra is
    simply wrong - and the counterexample would be measuring its own arithmetic.

    IT NOW RUNS THE MECHANISM IT IS A CONTROL FOR, which round 3 found it did not - the same defect
    as in the payment control. It replayed `_clearing_implied_by_intent`, which takes the pre-state
    from the TEST's own read of `debts`, while the counterexample replays
    `_clearing_implied_by_recorded_cycle` over the pre-amounts THE ENVELOPE STORED. Both are now
    asserted, and the stored pre-amounts are additionally required to equal the independently
    measured pre-state - which is the assertion that makes the envelope's intent a declaration
    rather than a copy of the outcome (design v2 §7, `C14`).

    WHAT IT COVERS AND WHAT IT DOES NOT: see `_recorded_clearing_pre_amounts` - `debt_id`,
    `clear_amount` and `equivalent_id` are projected away, so this is evidence for a
    single-equivalent cycle.

    MUTATION, MEASURED 2026-09-13: build the clearing intent's `cycle` amounts from the POST-state
    (`app/core/clearing/service.py:2037`, `debt.amount` -> `debt.amount - clear_amount`). The
    stored-intent half below goes red on the pre-amount comparison while the
    `_clearing_implied_by_intent` half stays green.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100")]
    )
    cycle_edges = [("a", "b"), ("b", "c"), ("c", "a")]
    try:
        debt_ids = []
        # Built outside the fixture block, added inside it: a loop is not fixture setup as far as
        # `fixture_block_violations` is concerned, and the rows and the single flush are unchanged.
        cycle_debts = [
            Debt(
                id=uuid.uuid4(),
                debtor_id=getattr(triangle, debtor).id,
                creditor_id=getattr(triangle, creditor).id,
                equivalent_id=triangle.equivalent.id,
                amount=Decimal("10"),
                version=0,
            )
            for debtor, creditor in cycle_edges
        ]
        debt_ids.extend(str(debt.id) for debt in cycle_debts)
        async with factory() as session:
            async with debt_fixture_setup(session, label="cycle"):
                session.add_all(cycle_debts)
            await session.commit()

        before = await _edges(factory, triangle)
        async with factory() as session:
            cleared = await ClearingService(session).execute_clearing_with_amount(
                [{"debt_id": debt_id} for debt_id in debt_ids]
            )

        after = await _edges(factory, triangle)

        # NON-VACUITY, FIRST: there really was a 10/10/10 cycle to close, and it really closed.
        assert cleared == Decimal("10"), cleared
        assert before == {
            ("a", "b"): Decimal("10.00000000"),
            ("b", "c"): Decimal("10.00000000"),
            ("c", "a"): Decimal("10.00000000"),
        }, f"stand: the cycle this control clears is not 10/10/10: {before}"

        # HALF ONE, the test's own read of the pre-state.
        assert _clearing_implied_by_intent(before, cycle_edges) == after == {}, (
            f"criterion (b) fails on an honest clearing: implied "
            f"{_clearing_implied_by_intent(before, cycle_edges)}, database {after}"
        )

        # HALF TWO, THE PATH C6 ACTUALLY REFUTES WITH: the pre-amounts the envelope stored.
        clearing_tx_id = await _clearing_tx_id(factory, triangle)
        envelopes = await _envelope_intents_for_tx(factory, clearing_tx_id)
        assert envelopes is not None, (
            "an honest 10/10/10 cycle closed and left no envelope to read its pre-amounts out of, "
            "so C6's refutation cannot be shown to work on an honest clearing. "
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
            f"criterion (b) fails on an honest clearing when it is replayed from the pre-amounts "
            f"the ENVELOPE stored: implied {_clearing_implied_by_recorded_cycle(recorded_pre)}, "
            f"database {after}. C6 (ii)'s refutation runs through exactly this path."
        )
    finally:
        await _drop_triangle(factory, triangle)
