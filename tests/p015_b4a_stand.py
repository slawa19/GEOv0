"""A private stand for the step-4 slice-A tests: one engine, one session class, one journal.

WHY A PRIVATE STAND AND NOT `db_session`. Slice A builds the journal's mechanism and registers it
NOWHERE - no production engine, no production sessionmaker, no `Session` class. That is the point of
the slice: the machinery is proven before a single existing writer changes behaviour, so both
canonical gates must come back exactly as they were. A test that installed the listeners on the
shared test engine would break that promise for every other module in the run, and a test that used
`db_session` would additionally hide the commit and rollback boundaries the mechanism is about -
that fixture wraps each test in a transaction of its own.

So each stand here owns its own engine, its own `Session` subclass and its own world. There are two
builders. `new_postgres_stand` is the one the journal's rules are measured on since programme 017
stage 3; its docstring says how it shares the tier database safely. `new_sqlite_stand` remains for
the few tests whose subject is SQLite itself, and leaves with SQLite. A SQLite stand owns:

* its own SQLite FILE under the test's `tmp_path` (never `:memory:` with a `StaticPool`, because a
  shared connection would let an UNCOMMITTED write be read back by a "fresh" session, and every
  verdict in these tests is read on a fresh one),
* its own sync `Session` SUBCLASS, so the session-level half of the journal is registered on a
  class that exists only for this test,
* `install_sqlite_transaction_control`, because a savepoint opened before the first write is
  otherwise its own transaction on SQLite and a root rollback does not undo it (T1525) - and
  because the journal refuses to open an operation on a SQLite engine without it.

MONEY STAYS INSIDE `|v| < 2^26` on the SQLite tier: the domain where a scale-8 `Numeric`
round-trips exactly through the driver's float binding (design v2 §4). `exact_money` refuses
anything outside it, so a test cannot quietly become a measurement of SQLite's float conversion.
The one exception is a test whose assertion IS the refusal of an unstorable value; those name the
value themselves and never store it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.core.ledger import journal
from app.db.base import Base
from app.db.journal_tables import (
    debt_journal_entries,
    debt_operation_equivalents,
    debt_operations,
)
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.sqlite_transaction_control import install_sqlite_transaction_control

#: Largest absolute scale-8 money value SQLite is proven to round-trip exactly (design v2 §4).
EXACT_DOMAIN_LIMIT = Decimal(2**26)

OPERATIONS_TABLE = "debt_operations"
ENTRIES_TABLE = "debt_journal_entries"
OPERATION_EQUIVALENTS_TABLE = "debt_operation_equivalents"


def exact_money(value: str) -> Decimal:
    """A scale-8 amount inside the domain SQLite round-trips exactly, or a loud failure."""

    amount = Decimal(value).quantize(Decimal("1E-8"))
    if abs(amount) >= EXACT_DOMAIN_LIMIT:
        raise ValueError(
            f"{value} is outside the proven-exact SQLite domain |v| < {EXACT_DOMAIN_LIMIT}; "
            f"full-size money acceptance belongs on the PostgreSQL tier (design v2 §4)"
        )
    return amount


def identity(name: str) -> str:
    return f"p015-b4a/{name}/{uuid.uuid4().hex[:12]}"


@dataclass
class Stand:
    """One engine, one session class, one small world - and the journal armed on all of it."""

    engine: AsyncEngine
    factory: async_sessionmaker
    session_class: type[Session]
    equivalent_id: uuid.UUID
    other_equivalent_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    extra_ids: list[uuid.UUID] = field(default_factory=list)
    #: Journal-table row counts when the stand was armed. Zero on a SQLite stand, whose database file
    #: is its own; on the PostgreSQL stand the tier database is shared with the rest of the run, so
    #: `counts()` answers "how many rows did THIS test add", which on a file of its own is the same
    #: number as the absolute count. Tests run one at a time, so nothing else moves it in between.
    count_baseline: dict[str, int] = field(default_factory=dict)

    # -- building blocks -------------------------------------------------------------------

    def debt(self, amount: str, **kw: Any) -> Debt:
        """A `Debt` of this world with every key column set explicitly."""

        return Debt(
            id=kw.pop("id", uuid.uuid4()),
            debtor_id=kw.pop("debtor_id", self.debtor_id),
            creditor_id=kw.pop("creditor_id", self.creditor_id),
            equivalent_id=kw.pop("equivalent_id", self.equivalent_id),
            amount=kw.pop("raw_amount", exact_money(amount)),
            version=kw.pop("version", 0),
            **kw,
        )

    def debt_values(self, amount: str, **kw: Any) -> dict:
        """The same row as a plain dict, for Core DML and the `bulk_*` entry points."""

        return {
            "id": kw.pop("id", uuid.uuid4()),
            "debtor_id": kw.pop("debtor_id", self.debtor_id),
            "creditor_id": kw.pop("creditor_id", self.creditor_id),
            "equivalent_id": kw.pop("equivalent_id", self.equivalent_id),
            "amount": kw.pop("raw_amount", exact_money(amount)),
            "version": kw.pop("version", 1),
            **kw,
        }

    def operation(self, name: str, **kw: Any):
        """`journal.debt_operation` with this stand's standard arguments."""

        return journal.debt_operation(
            kw.pop("session"),
            kind=kw.pop("kind", "TEST_FIXTURE"),
            identity=kw.pop("identity", identity(name)),
            intent=kw.pop("intent", {"source": "p015-b4a", "name": name}),
            scope_equivalent_ids=kw.pop("scope_equivalent_ids", frozenset({self.equivalent_id})),
            **kw,
        )

    # -- verdicts, always on a NEW session --------------------------------------------------

    async def stored_debts(self) -> dict[tuple[str, str, str], Decimal]:
        """Every debt of this world AS THE DATABASE HOLDS IT, read on a new session.

        Never through the session under test: its identity map answers from memory, so a write
        that never reached the database looks identical to one that did.
        """

        names = {self.debtor_id: "debtor", self.creditor_id: "creditor"}
        for index, participant_id in enumerate(self.extra_ids):
            names[participant_id] = f"extra{index}"
        equivalents = {self.equivalent_id: "eq", self.other_equivalent_id: "other"}
        async with self.factory() as fresh:
            rows = (
                await fresh.execute(
                    select(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id, Debt.amount).where(
                        Debt.equivalent_id.in_([self.equivalent_id, self.other_equivalent_id])
                    )
                )
            ).all()
        return {
            (names.get(d, str(d)), names.get(c, str(c)), equivalents.get(e, str(e))): Decimal(str(a))
            for d, c, e, a in rows
        }

    async def rows(self, statement: Any) -> list[dict]:
        """Run a read on a NEW session and return plain dicts.

        Statements are built from the `Table` objects rather than from `text()` on purpose: a
        textual SELECT on SQLite comes back through no type at all, so a `NUMERIC(20, 8)` column
        arrives as a float and every money assertion would silently be about a float.
        """

        async with self.factory() as fresh:
            result = await fresh.execute(statement)
            return [dict(row) for row in result.mappings()]

    async def envelopes(self, op_identity: str) -> list[dict]:
        return await self.rows(
            select(debt_operations).where(debt_operations.c.identity == op_identity)
        )

    async def entries(self, op_identity: str) -> list[dict]:
        return await self.rows(
            select(
                debt_journal_entries.c.flush_ordinal,
                debt_journal_entries.c.effect,
                debt_journal_entries.c.amount_before,
                debt_journal_entries.c.amount_after,
                debt_journal_entries.c.delta,
                debt_journal_entries.c.equivalent_id,
            )
            .join(debt_operations, debt_operations.c.id == debt_journal_entries.c.operation_id)
            .where(debt_operations.c.identity == op_identity)
            .order_by(debt_journal_entries.c.flush_ordinal, debt_journal_entries.c.delta)
        )

    async def operation_equivalents(self, op_identity: str) -> list[dict]:
        return await self.rows(
            select(
                debt_operation_equivalents.c.equivalent_id,
                debt_operation_equivalents.c.in_intent,
                debt_operation_equivalents.c.in_scope,
                debt_operation_equivalents.c.effect_count,
            )
            .join(
                debt_operations,
                debt_operations.c.id == debt_operation_equivalents.c.operation_id,
            )
            .where(debt_operations.c.identity == op_identity)
        )

    async def counts(self) -> dict[str, int]:
        """How many rows each journal table gained since the stand was armed, read fresh.

        WHOLE TABLES, not this stand's world: a write the guard should have refused lands wherever
        the statement put it, and a count scoped to the stand's identities or equivalents would not
        see a forged row that named something else. The baseline is what makes the whole-table count
        usable on a database other tests share (see `count_baseline`).
        """

        counted = await self._absolute_counts()
        return {name: n - self.count_baseline.get(name, 0) for name, n in counted.items()}

    async def _absolute_counts(self) -> dict[str, int]:
        counted = {}
        for table in (debt_operations, debt_journal_entries, debt_operation_equivalents):
            counted[table.name] = (
                await self.rows(select(func.count().label("n")).select_from(table))
            )[0]["n"]
        return counted

    def driver_sql(self, statement: str) -> str:
        """A raw statement written with `?` placeholders, in the placeholder style of this engine.

        `exec_driver_sql` hands the string to the DBAPI untouched, so the placeholder is the driver's
        business: `qmark` on sqlite3, `numeric_dollar` (`$1`) on asyncpg. The journal meets the same
        difference through the dialect's `paramstyle` - this is a dialect's spelling, not behaviour.
        """

        style = self.engine.dialect.paramstyle
        if style == "qmark":
            return statement
        if style not in {"numeric_dollar", "numeric"}:
            raise ValueError(f"no placeholder rewrite for paramstyle {style!r}")
        prefix = "$" if style == "numeric_dollar" else ":"
        parts = statement.split("?")
        return parts[0] + "".join(f"{prefix}{index}{part}" for index, part in enumerate(parts[1:], 1))

    async def purge(self) -> None:
        """Delete everything this stand created, through `exec_driver_sql`.

        `exec_driver_sql` fires no `before_execute` (`sqlalchemy/engine/base.py:1712-1778`), which
        is the write guard's ONE documented blind spot - and a teardown is the only place in this
        repository allowed to use it deliberately. The alternative would be to grant the guard an
        exemption for cleanups, and an exemption is a hole that outlives the test that asked for
        it. Order follows the RESTRICT foreign keys: entries and completion rows, then envelopes,
        then debts, then the world.
        """

        ids = ", ".join(f"'{value}'" for value in [*self.extra_ids, self.debtor_id, self.creditor_id])
        equivalents = f"'{self.equivalent_id}', '{self.other_equivalent_id}'"
        async with self.engine.begin() as connection:
            for statement in (
                f"DELETE FROM {OPERATION_EQUIVALENTS_TABLE} WHERE equivalent_id IN ({equivalents})",  # noqa: S608
                f"DELETE FROM {ENTRIES_TABLE} WHERE equivalent_id IN ({equivalents})",  # noqa: S608
                f"DELETE FROM {OPERATIONS_TABLE} WHERE identity LIKE 'p015-b4a/%'",  # noqa: S608
                f"DELETE FROM debts WHERE equivalent_id IN ({equivalents})",  # noqa: S608
                f"DELETE FROM participants WHERE id IN ({ids})",  # noqa: S608
                f"DELETE FROM equivalents WHERE id IN ({equivalents})",  # noqa: S608
            ):
                await connection.exec_driver_sql(statement)

    async def close(self, *, purge: bool = False) -> None:
        if purge:
            await self.purge()
        journal.uninstall_journal(self.engine, self.session_class)
        await self.engine.dispose()


async def new_sqlite_stand(
    tmp_path: Any, *, filename: str = "journal.db", extra_participants: int = 0
) -> Stand:
    """A SQLite stand on its own database file under the test's `tmp_path`.

    `tmp_path` and not a path this module invents: under the canonical runner that directory is
    `.local-run/test-runs/<slug>/pytest/...`, which is the only tree a test may write a mutable
    database into (T1406, `tests/scratch_db.py`) - and it is per test, so one test's database can
    never be another's.
    """

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}", connect_args={"timeout": 30}
    )

    @_listens_on_connect(engine)
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    install_sqlite_transaction_control(engine.sync_engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return await _arm(engine, extra_participants=extra_participants)


async def new_postgres_stand(*, extra_participants: int = 0) -> Stand:
    """A stand on the tier's PostgreSQL database, with real root commits, purged by `close`.

    THE ENGINE IS THE STAND'S OWN, over conftest's `TEST_DATABASE_URL` - never the tier's session
    fixture, whose outer transaction would turn every commit these tests are about into a savepoint
    release. `tests/conftest.py::_require_a_postgres_tier_url` refuses a non-PostgreSQL URL before
    anything is collected, so this construction cannot be SQLite (which is also how
    `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py` reads it).

    It runs at the application's isolation level, read from the same setting the tier engine reads
    (T1549), so the journal is measured at SERIALIZABLE and not at the server default.

    WHY NOT A CLONED DATABASE PER TEST (the mode-B fixture). Every test here commits for real, so
    each would need its own clone, and a clone measured 0.35-0.82 s to create and drop on this
    machine (2026-09-24) -
    for about seventy tests that is most of the required job's remaining budget. The stand instead
    builds a world of its own (fresh equivalents and participants), `counts()` subtracts what the
    tables held when it was armed, and `close(purge=True)` removes what it created. The price is
    named: the world is isolated by construction, the database is not. The purge runs in each
    fixture's `finally`, so rows survive only if the purge itself fails - loudly, as a teardown
    error - and even then the next stand's baseline absorbs them rather than miscounting.
    """

    from tests.conftest import (
        TEST_DATABASE_URL,
        _ensure_schema_initialized,
        _test_engine_isolation_kwargs,
    )

    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=4,
        max_overflow=0,
        pool_timeout=15,
        **_test_engine_isolation_kwargs("postgresql"),
    )
    built = await _arm(engine, extra_participants=extra_participants)
    built.count_baseline = await built._absolute_counts()
    return built


def _listens_on_connect(engine: AsyncEngine):
    from sqlalchemy import event

    def decorate(fn):
        event.listen(engine.sync_engine, "connect", fn)
        return fn

    return decorate


async def _arm(engine: AsyncEngine, *, extra_participants: int) -> Stand:
    class _StandSession(Session):
        """A Session class that exists only for this stand, so the hook is registered privately."""

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        sync_session_class=_StandSession,
        expire_on_commit=False,
        autoflush=False,
    )
    journal.install_journal(engine, _StandSession)

    tag = uuid.uuid4().hex[:8].upper()
    equivalent = Equivalent(code=f"B4A{tag[:5]}", precision=2, is_active=True, metadata_={})
    other = Equivalent(code=f"B4B{tag[:5]}", precision=2, is_active=True, metadata_={})
    debtor = Participant(
        pid=f"B4A_D_{tag}",
        display_name="Debtor",
        public_key=f"pk_b4a_d_{tag}",
        type="person",
        status="active",
        profile={},
    )
    creditor = Participant(
        pid=f"B4A_C_{tag}",
        display_name="Creditor",
        public_key=f"pk_b4a_c_{tag}",
        type="person",
        status="active",
        profile={},
    )
    extras = [
        Participant(
            pid=f"B4A_E{index}_{tag}",
            display_name=f"Extra {index}",
            public_key=f"pk_b4a_e{index}_{tag}",
            type="person",
            status="active",
            profile={},
        )
        for index in range(extra_participants)
    ]
    async with factory() as session:
        session.add_all([equivalent, other, debtor, creditor, *extras])
        await session.commit()

    return Stand(
        engine=engine,
        factory=factory,
        session_class=_StandSession,
        equivalent_id=equivalent.id,
        other_equivalent_id=other.id,
        debtor_id=debtor.id,
        creditor_id=creditor.id,
        extra_ids=[participant.id for participant in extras],
    )


#: `arm_stand` is exported for the PostgreSQL module, which builds its OWN engine next to its
#: `pytest.skip` refusal - `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py`
#: requires a PostgreSQL-only engine construction to sit beside the refusal that makes it so, and
#: that refusal belongs in a postgres-marked module rather than in this shared helper.
arm_stand = _arm


