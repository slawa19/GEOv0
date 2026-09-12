"""Scaffolding shared by the programme 015 phase B step-2 counterexamples (`B4`).

WHY THIS FILE EXISTS AND WHAT IT IS NOT. The counterexamples for the debt journal are written
BEFORE the journal exists - that is the whole point of step 2, and the spec's binding acceptance of
`B4` (2026-09-11) says so: "каждое пишется красным контрпримером до кода". Three modules need the
same two things, and duplicating either would let them drift apart:

* one definition of "import the API under construction from inside the test body", so that a
  counterexample fails on the PROPERTY it names and not on a missing file;
* one small world (two participants, one equivalent, debts) that every scenario writes into.

It is not a test module (pytest collects `test_*.py` only) and it holds no assertions of its own.

THE IMPORT RULE, and why it is built this way. `app/core/ledger/journal.py` does not exist today.
Importing it at module level would make every counterexample fail with `ModuleNotFoundError` during
collection, which proves nothing: a missing file is not a missing property, and the whole suite
would go green the moment an empty module appeared. So:

1. `journal_api()` is called INSIDE the test body. When the module is absent it returns a handle
   whose `refusals` tuple holds a private exception class that nothing in this repository ever
   raises. Every `except api.refusals` therefore catches nothing, the scenario runs to its end, and
   the test fails on its own verdict assertion - "a Debt was written with no operation open and
   nothing refused it" - which names the property.
2. `operation()` opens a real journal operation when the module is there and is a no-op when it is
   not. A counterexample whose subject is "operation A on this session does not cover a write on
   that session" therefore still runs today, in its degenerate form ("nothing covers the write at
   all"), and is red for a reason that is a strict weakening of the one it will test later. Each
   such test says so in its own docstring; `api.available` is asserted wherever the degenerate form
   would be VACUOUS instead of weaker.

MONEY VALUES. The SQLite tier may only use the domain where scale-8 Numeric round-trips exactly
through the driver's float binding - `|v| < 2^26` (design v2 §4, confirmed by the round-2 reviewer).
`EXACT_DOMAIN_LIMIT` below is that bound and `exact_money()` refuses anything outside it, so a
counterexample cannot quietly become a measurement of SQLite's float conversion instead of the
property it was written for.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable

from sqlalchemy import delete, select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

#: Largest absolute scale-8 money value SQLite is proven to round-trip exactly (design v2 §4).
EXACT_DOMAIN_LIMIT = Decimal(2**26)

#: The module the journal will live in. Named once so a rename shows up in one place.
JOURNAL_MODULE = "app.core.ledger.journal"


class _NoRefusalExistsYet(BaseException):
    """Stand-in refusal type used while the journal does not exist. Nothing ever raises it.

    It inherits `BaseException` rather than `Exception` on purpose: if some future code were to
    catch it by accident through a broad `except Exception`, the counterexample would go green
    without the property existing, which is exactly the false green this class is here to avoid.
    """


@dataclass(frozen=True)
class JournalApi:
    """The journal API as this run found it, plus the refusal types to catch."""

    module: Any | None
    refusals: tuple[type[BaseException], ...]

    @property
    def available(self) -> bool:
        return self.module is not None

    def missing(self, what: str) -> str:
        """The message for a non-vacuity assertion that cannot hold without the journal."""
        return (
            f"{what} - and it cannot be observed here at all, because {JOURNAL_MODULE} does not "
            "exist yet, so no debt operation could be opened and the scenario below has nothing "
            "to hold it up. This is the step-2 red state, not a passing test."
        )


def journal_api() -> JournalApi:
    """Import the journal from INSIDE a test body. See the module docstring for the rule."""
    try:
        from app.core.ledger import journal  # type: ignore[attr-defined]
    except ImportError:
        return JournalApi(module=None, refusals=(_NoRefusalExistsYet,))
    refusals = tuple(
        cls
        for name in ("DebtJournalError", "DebtOperationIncomplete")
        if isinstance(cls := getattr(journal, name, None), type) and issubclass(cls, BaseException)
    )
    return JournalApi(module=journal, refusals=refusals or (_NoRefusalExistsYet,))


@asynccontextmanager
async def operation(api: JournalApi, session, **kwargs: Any) -> AsyncIterator[Any]:
    """Open a journal operation if there is one to open; otherwise run the block bare.

    The degenerate form is deliberate and is documented per test: without the journal a scenario
    that should be refused because its write is outside THIS operation is instead not refused
    because there is no operation anywhere. The verdict assertion is the same sentence in both
    worlds - "the write was refused" - and it is false today.
    """
    if api.module is None:
        yield None
        return
    async with api.module.debt_operation(session, **kwargs) as op:
        yield op


async def refusal_of(api: JournalApi, awaitable: Awaitable[Any]) -> BaseException | None:
    """Run `awaitable`; return the journal's refusal if it raised one, else None."""
    try:
        await awaitable
    except api.refusals as exc:  # noqa: B902 - the refusal contract is the subject under test
        return exc
    return None


def exact_money(value: str) -> Decimal:
    """A scale-8 amount inside the domain SQLite round-trips exactly, or a loud failure."""
    amount = Decimal(value).quantize(Decimal("1E-8"))
    if abs(amount) >= EXACT_DOMAIN_LIMIT:
        raise ValueError(
            f"{value} is outside the proven-exact SQLite domain |v| < {EXACT_DOMAIN_LIMIT}; "
            "full-size money acceptance belongs on the PostgreSQL tier (design v2 §4)"
        )
    return amount


@dataclass
class World:
    """Two participants and one equivalent, plus a second equivalent for scope counterexamples."""

    equivalent: Equivalent
    other_equivalent: Equivalent
    debtor: Participant
    creditor: Participant
    tag: str
    extra_participants: list[Participant] = field(default_factory=list)

    @property
    def participant_ids(self) -> list[uuid.UUID]:
        return [self.debtor.id, self.creditor.id] + [p.id for p in self.extra_participants]

    @property
    def equivalent_ids(self) -> list[uuid.UUID]:
        return [self.equivalent.id, self.other_equivalent.id]

    def debt(self, amount: str, *, equivalent: Equivalent | None = None, **kw: Any) -> Debt:
        """A Debt of this world, with every key column set explicitly."""
        return Debt(
            id=kw.pop("id", uuid.uuid4()),
            debtor_id=kw.pop("debtor_id", self.debtor.id),
            creditor_id=kw.pop("creditor_id", self.creditor.id),
            equivalent_id=kw.pop("equivalent_id", (equivalent or self.equivalent).id),
            amount=exact_money(amount),
            version=kw.pop("version", 0),
            **kw,
        )

    def debt_values(self, amount: str, *, equivalent: Equivalent | None = None, **kw: Any) -> dict:
        """The same row as a plain dict, for Core DML and the bulk_* entry points."""
        return {
            "id": kw.pop("id", uuid.uuid4()),
            "debtor_id": kw.pop("debtor_id", self.debtor.id),
            "creditor_id": kw.pop("creditor_id", self.creditor.id),
            "equivalent_id": kw.pop("equivalent_id", (equivalent or self.equivalent).id),
            "amount": exact_money(amount),
            "version": kw.pop("version", 0),
            **kw,
        }


async def seed_world(factory, *, extra_participants: int = 0) -> World:
    """Commit the world in its own transaction, so no test session inherits its writes."""
    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code=f"B4{tag}", precision=2, is_active=True, metadata_={})
        other = Equivalent(code=f"B4X{tag}", precision=2, is_active=True, metadata_={})
        debtor = Participant(
            pid=f"B4_D_{tag}", display_name="Debtor", public_key=f"pk_b4_d_{tag}",
            type="person", status="active", profile={},
        )
        creditor = Participant(
            pid=f"B4_C_{tag}", display_name="Creditor", public_key=f"pk_b4_c_{tag}",
            type="person", status="active", profile={},
        )
        extras = [
            Participant(
                pid=f"B4_E{i}_{tag}", display_name=f"Extra {i}", public_key=f"pk_b4_e{i}_{tag}",
                type="person", status="active", profile={},
            )
            for i in range(extra_participants)
        ]
        session.add_all([equivalent, other, debtor, creditor, *extras])
        await session.commit()
    return World(equivalent, other, debtor, creditor, tag, extras)


async def drop_world(factory, world: World) -> None:
    async with factory() as session:
        await session.execute(delete(Debt).where(Debt.equivalent_id.in_(world.equivalent_ids)))
        await session.execute(delete(Participant).where(Participant.id.in_(world.participant_ids)))
        await session.execute(delete(Equivalent).where(Equivalent.id.in_(world.equivalent_ids)))
        await session.commit()


async def stored_debts(factory, world: World) -> dict[tuple[str, str, str], Decimal]:
    """Every debt of this world AS THE DATABASE HOLDS IT, read on a NEW session.

    Never read a verdict through the session under test: its identity map answers from memory, and
    a write that never reached the database looks identical to one that did.
    """
    name = {world.debtor.id: "debtor", world.creditor.id: "creditor"}
    for index, participant in enumerate(world.extra_participants):
        name[participant.id] = f"extra{index}"
    eq_name = {world.equivalent.id: "eq", world.other_equivalent.id: "other"}
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id, Debt.amount).where(
                    Debt.equivalent_id.in_(world.equivalent_ids)
                )
            )
        ).all()
    return {
        (name.get(d, str(d)), name.get(c, str(c)), eq_name.get(e, str(e))): Decimal(str(a))
        for d, c, e, a in rows
    }


#: Names fixed by design v2 §5. They are written here rather than imported because the module that
#: will own them does not exist yet, and a counterexample that cannot even name its table would be
#: unable to say what is missing.
OPERATIONS_TABLE = "debt_operations"
ENTRIES_TABLE = "debt_journal_entries"
OPERATION_EQUIVALENTS_TABLE = "debt_operation_equivalents"
JOURNAL_TABLES = (OPERATIONS_TABLE, ENTRIES_TABLE, OPERATION_EQUIVALENTS_TABLE)


async def stored_rows(factory, sql: str, params: dict[str, Any] | None = None) -> list[dict] | None:
    """Rows for a read-only query on a NEW session, or None when the table does not exist yet.

    None and `[]` are deliberately different answers: "the journal has no table to look in" is not
    "the journal recorded nothing", and a counterexample that let them collapse would go green the
    day an empty module appeared (`AGENTS.md` §1, "отсутствующее измерение обязано отличаться от
    нулевого").
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    async with factory() as fresh:
        try:
            result = await fresh.execute(text(sql), params or {})
        except DatabaseError:
            return None
        return [dict(row) for row in result.mappings()]


async def stored_operations(factory, identity: str) -> list[dict] | None:
    """Envelope rows for one operation identity, read fresh. None when the table is absent."""
    return await stored_rows(
        factory,
        f"SELECT id, kind, identity, state, tx_id, effect_count, flush_count "  # noqa: S608
        f"FROM {OPERATIONS_TABLE} WHERE identity = :identity",
        {"identity": identity},
    )


async def stored_entries(factory, identity: str) -> list[dict] | None:
    """Journal entries belonging to one operation identity, read fresh."""
    return await stored_rows(
        factory,
        f"SELECT e.flush_ordinal, e.effect, e.amount_before, e.amount_after, e.delta "  # noqa: S608
        f"FROM {ENTRIES_TABLE} e JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.identity = :identity ORDER BY e.flush_ordinal",
        {"identity": identity},
    )


def missing_journal_tables(rows: list[dict] | None, table: str) -> str:
    return (
        f"the `{table}` table does not exist, so nothing in this database can record what the "
        f"operation did. {JOURNAL_MODULE} and migration 021 are what step 4 must add; until then "
        "this counterexample is red because the property has no carrier at all."
    )


#: Anything that reads `sqlite3.Connection.in_transaction` needs the driver connection, which is two
#: wrappers down: `AsyncConnection.get_raw_connection()` gives SQLAlchemy's adapted connection and
#: `.driver_connection` the aiosqlite one, whose `in_transaction` is the sqlite3 one's. The modules
#: that need it also need to read it AFTER the session has finished with the connection, so they
#: take the handle themselves rather than calling through a session here.
