"""Programme 019, `T1902` item 5 (spec, Verification plan §1, №6): two concurrent staged inserts of ONE `tx_id`.

THE SCHEDULE IS REAL, nothing is injected. Transaction A stages a payment (or, for the identity
counter-checks, a row of another identity) under its caller's savepoint and stays open. Transaction B
stages a payment with the same `tx_id` under its own caller's savepoint: its idempotency read finds
nothing (A is uncommitted), and its `Transaction` insert QUEUES on A's uncommitted unique-index entry
(asserted: a non-granted `transactionid` lock exists while B is in flight). A commits; B's insert then
meets the uniqueness of `transactions.tx_id` - the constraint `transactions_tx_id_key` (`T1902`).

WHAT B MUST END WITH (spec, "Идентичность `tx_id`"): the exact identity resolver reads the winner on a
FRESH transaction - B's own SERIALIZABLE snapshot cannot see a row committed after it began - and
compares type, initiator and fingerprint: the same request gets the winner's stored result; any other
identity gets a `409`. B's caller transaction stays usable and commits; nothing is written twice.

BEFORE THE RESOLVER (b43e55f, red there: 4 x `TargetMismatch`, `056b27b`): the operation savepoint was
rolled back and the winner was looked up in B's own snapshot, where it is invisible, so the raw
`IntegrityError` reached the caller (before stage 3, `InvalidRequestError`; `specs/BACKLOG.md`,
"Staged-ветка гонки вставки…"). The resolver is `PaymentService._resolve_identity` (`T1905`).

CONTROLS, asserted before the comparison: B really queued on A's insert; A committed; exactly one row of
the `tx_id` exists afterwards, and it is A's; the money moved once.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text

from app.core.auth.canonical import canonical_json
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.transaction import Transaction
from app.utils.exceptions import ConflictException
from tests.integration.p019_stand import finish, tx_row
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _debts,
    _seed,
    factory,
)
from tests.p019_support import require_target


async def _transactionid_waiter_exists(factory, *, timeout: float = 10.0) -> bool:  # noqa: F811
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
            waiting = await observer.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted AND locktype = 'transactionid')")
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


async def _staged(session, world, tx_id: str, amount: str, *, sender=None):
    PaymentRouter.invalidate_cache(world.equivalent.code)
    return await PaymentService(session).create_payment_internal_staged(
        (sender or world.sender).id,
        to_pid=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount=amount,
        idempotency_key=tx_id,
    )


@dataclass
class _Race:
    queued: bool
    b_outcome: tuple[str, Any]
    b_committed: bool
    rows: int
    stored: tuple[str, dict | None] | None


async def _race(factory, world, *, winner, b_amount: str, b_sender=None) -> _Race:  # noqa: F811
    """`winner(session, tx_id)` writes A's row under A's caller savepoint; B then stages the same tx_id."""

    tx_id = str(uuid.uuid4())
    b_task: asyncio.Task | None = None
    async with factory() as a:
        async with a.begin_nested():
            await winner(a, tx_id)

        async def b_side() -> tuple[tuple[str, Any], bool]:
            async with factory() as b:
                try:
                    async with b.begin_nested():
                        staged = await _staged(b, world, tx_id, b_amount, sender=b_sender)
                    outcome: tuple[str, Any] = (
                        "result",
                        (staged.result.status, staged.result.tx_id, staged.post_commit_effects is None),
                    )
                except ConflictException as exc:
                    outcome = ("409", exc.message)
                except Exception as exc:  # noqa: BLE001 - the pre-resolver outcome, classified below
                    outcome = ("raised", type(exc).__name__)
                try:
                    await b.commit()
                    committed = True
                except Exception:  # noqa: BLE001 - a caller transaction left unusable
                    await b.rollback()
                    committed = False
                return outcome, committed

        b_task = asyncio.create_task(b_side())
        try:
            queued = await _transactionid_waiter_exists(factory)
            await a.commit()
            b_outcome, b_committed = await asyncio.wait_for(b_task, timeout=30)
        finally:
            await finish(b_task)
    async with factory() as s:
        rows = int(await s.scalar(select(func.count()).select_from(Transaction).where(Transaction.tx_id == tx_id)))
    return _Race(queued, b_outcome, b_committed, rows, await tx_row(factory, tx_id))


def _fingerprint(world, tx_id: str, amount: str) -> str:
    """The fingerprint `PaymentService.execute` computes for the staged request B makes."""

    return hashlib.sha256(
        canonical_json(
            {"tx_id": tx_id, "to": world.receiver.pid, "equivalent": world.equivalent.code, "amount": amount}
        )
    ).hexdigest()


def _raw_winner(world, *, type_: str, initiator_id, fingerprint_amount: str):
    """A's row of the given identity, written directly.

    Written as a row and not as a payment ON PURPOSE: a winner that moves money writes the debt B's
    routing read, and SERIALIZABLE then refuses B with `40001` before the uniqueness is ever reported
    (measured 2026-09-25; that manifestation is the last test below). The resolver's subject is the row
    it reads - type, initiator, fingerprint - so the winner here is exactly that row, COMMITTED.
    """

    async def winner(a, tx_id):
        await a.execute(
            insert(Transaction).values(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type=type_,
                initiator_id=initiator_id,
                payload={
                    "from": world.sender.pid,
                    "to": world.receiver.pid,
                    "amount": fingerprint_amount,
                    "equivalent": world.equivalent.code,
                    "routes": [{"path": [world.sender.pid, world.receiver.pid], "amount": fingerprint_amount}],
                    "idempotency": {"key": tx_id, "fingerprint": _fingerprint(world, tx_id, fingerprint_amount)},
                },
                state="COMMITTED",
            )
        )

    return winner


@pytest.mark.asyncio
async def test_the_same_request_racing_its_own_insert_gets_the_winners_result(factory) -> None:  # noqa: F811
    world = await _seed(factory)
    race = await _race(
        factory,
        world,
        winner=_raw_winner(world, type_="PAYMENT", initiator_id=world.sender.id, fingerprint_amount="1.00"),
        b_amount="1.00",
    )

    assert race.queued, "premise: B never queued on A's uncommitted tx_id"
    assert race.rows == 1 and race.stored == ("COMMITTED", None), (race.rows, race.stored)
    # B moved nothing: the stored row answers it.
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
    require_target(
        race.b_outcome == ("result", ("COMMITTED", race.b_outcome[1][1], True)) and race.b_committed,
        f"B, racing the same request's insert, ended with {race.b_outcome!r} "
        f"(caller transaction committed: {race.b_committed})",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["fingerprint", "initiator", "type"])
async def test_another_identity_racing_the_insert_is_a_declared_conflict(
    factory, identity: str  # noqa: F811
) -> None:
    """Counter-checks: a winner of another fingerprint, initiator or type is a 409, never its result."""

    world = await _seed(factory)
    winner = {
        "fingerprint": _raw_winner(world, type_="PAYMENT", initiator_id=world.sender.id, fingerprint_amount="2.00"),
        "initiator": _raw_winner(world, type_="PAYMENT", initiator_id=world.outsider_a.id, fingerprint_amount="1.00"),
        "type": _raw_winner(world, type_="CLEARING", initiator_id=world.sender.id, fingerprint_amount="1.00"),
    }[identity]

    race = await _race(factory, world, winner=winner, b_amount="1.00")

    assert race.queued, "premise: B never queued on A's uncommitted tx_id"
    assert race.rows == 1 and race.stored == ("COMMITTED", None), (race.rows, race.stored)
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
    require_target(
        race.b_outcome[0] == "409" and race.b_committed,
        f"B, racing an insert of another {identity}, ended with {race.b_outcome!r} "
        f"(caller transaction committed: {race.b_committed})",
    )


@pytest.mark.asyncio
async def test_a_winner_that_moved_money_is_a_retryable_conflict_the_owner_resolves_afresh(
    factory,  # noqa: F811
) -> None:
    """The other manifestation of the same race: A's payment wrote the debt B's routing read, so
    SERIALIZABLE refuses B with `40001` - a retryable conflict, propagated to the owner of B's
    transaction (spec: `execute()` never heals a transaction-level conflict inside a savepoint). The
    owner's fresh attempt then reads A's row through ordinary idempotency. Green before and after the
    resolver: it pins that the resolver does not turn this conflict into a stored or refused outcome."""

    world = await _seed(factory)

    async def winner(a, tx_id):
        staged = await _staged(a, world, tx_id, "1.00")
        assert staged.result.status == "COMMITTED", staged.result

    race = await _race(factory, world, winner=winner, b_amount="1.00")

    assert race.queued, "premise: B never queued on A's uncommitted tx_id"
    assert race.b_outcome == ("409", "State conflict") and not race.b_committed, race
    assert race.rows == 1 and race.stored == ("COMMITTED", None), race
    tx_id = await _only_tx_id(factory, world)
    async with factory() as fresh:
        async with fresh.begin_nested():
            again = await _staged(fresh, world, tx_id, "1.00")
        await fresh.commit()
    assert (again.result.status, again.post_commit_effects) == ("COMMITTED", None), again
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal("1.00")
    }


async def _only_tx_id(factory, world) -> str:  # noqa: F811
    async with factory() as s:
        return str(
            (
                await s.execute(
                    select(Transaction.tx_id).where(Transaction.initiator_id == world.sender.id)
                )
            ).scalar_one()
        )
