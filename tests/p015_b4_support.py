"""Scaffolding shared by the programme 015 phase B step-4 counterexamples (`B4`). NOT a test module.

WHAT IT IS NOW (018 stage B1, 2026-09-24). The step-2 scaffolding this file was written for - an import
of the listener journal from inside the test body, a no-op `operation()` while the journal did not
exist, the journal's refusal types - went with `app/core/ledger/journal.py`. The journal is the
database's (`app/db/journal_triggers.py`, migration 029) and the envelope is the book's
(`app/core/ledger/book.py`). What stays is what the surviving modules share:

* `operation()` - a `Book` operation with the keyword shape these modules spell;
* one small world (two participants, two equivalents, optional extras) and fresh-session readers.

`None` from `stored_rows` still means "the query could not run", never "no rows": the
distinction the step-2 red state needed stays useful for a table that a broken schema lacks.

MONEY VALUES. `exact_money` keeps the scale-8 quantisation and the old `|v| < 2^26` bound. The bound
was the SQLite tier's exact domain; since 017 the tier is PostgreSQL only, so it is no longer a
correctness precondition - it is kept so the unit modules keep their small, readable amounts, and the
full-size money lives in the PostgreSQL entries module.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, AsyncIterator, Iterable

from sqlalchemy import select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

#: The historical SQLite exact domain (design v2 §4); see the module docstring.
EXACT_DOMAIN_LIMIT = Decimal(2**26)

#: The module that owns the envelope and every refusal before SQL (018). Named once for messages.
WRITER_MODULE = "app.core.ledger.book"


@asynccontextmanager
async def operation(
    session,
    *,
    kind: str,
    identity: str,
    intent: Any,
    scope_equivalent_ids: Iterable[Any] | None = None,
    intent_equivalent_ids: Iterable[Any] = (),
    tx_id: str | None = None,
) -> AsyncIterator[Any]:
    """A `Book` operation (018): the envelope is the book's, the entries are the trigger's.

    The keyword shape is the one the deleted `debt_operation` took, so the surviving counterexamples
    read as they did; what they now open is the only kind of operation there is.
    """

    from app.core.ledger.book import Book, operation_for

    async with Book.operation(
        session,
        operation_for(
            kind,
            identity,
            intent,
            tx_id=tx_id,
            scope_equivalent_ids=scope_equivalent_ids,
            intent_equivalent_ids=intent_equivalent_ids,
        ),
    ) as posting:
        yield posting


def exact_money(value: str) -> Decimal:
    """A scale-8 amount inside the historical exact domain, or a loud failure."""
    amount = Decimal(value).quantize(Decimal("1E-8"))
    if abs(amount) >= EXACT_DOMAIN_LIMIT:
        raise ValueError(
            f"{value} is outside |v| < {EXACT_DOMAIN_LIMIT}; full-size money acceptance belongs "
            "to tests/integration/test_p015_b4_entries_and_money_postgres.py"
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


#: Names fixed by design v2 §5, spelled here so a counterexample can name the table it reads.
OPERATIONS_TABLE = "debt_operations"
ENTRIES_TABLE = "debt_journal_entries"
OPERATION_EQUIVALENTS_TABLE = "debt_operation_equivalents"
JOURNAL_TABLES = (OPERATIONS_TABLE, ENTRIES_TABLE, OPERATION_EQUIVALENTS_TABLE)


async def stored_rows(factory, sql: str, params: dict[str, Any] | None = None) -> list[dict] | None:
    """Rows for a read-only query on a NEW session, or None when the query could not run.

    None and `[]` are deliberately different answers: "there is no table to look in" is not "the
    journal recorded nothing" (`AGENTS.md` §1, "отсутствующее измерение обязано отличаться от
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
        f"SELECT id, kind, identity, state, tx_id, effect_count, schema_version "  # noqa: S608
        f"FROM {OPERATIONS_TABLE} WHERE identity = :identity",
        {"identity": identity},
    )


async def stored_entries(factory, identity: str) -> list[dict] | None:
    """Journal entries belonging to one operation identity, read fresh, in `ordinal` order.

    `ordinal` is a sequence value: strictly increasing within an operation, gapped, and NOT a flush
    number (018 stage B1; the listener's `flush_ordinal` is gone).
    """
    return await stored_rows(
        factory,
        f"SELECT e.ordinal, e.effect, e.amount_before, e.amount_after, e.delta, "  # noqa: S608
        f"e.debtor_id, e.creditor_id "
        f"FROM {ENTRIES_TABLE} e JOIN {OPERATIONS_TABLE} o ON o.id = e.operation_id "
        f"WHERE o.identity = :identity ORDER BY e.ordinal",
        {"identity": identity},
    )


def missing_journal_tables(rows: list[dict] | None, table: str) -> str:
    return (
        f"the `{table}` table could not be read, so nothing in this database can say what the "
        f"operation did. Migrations 021 and 029 create and arm it; a schema without it is a broken "
        "stand, not a passing counterexample."
    )
