"""T1525: a classification reads THIS failure, never the one that happened to be in flight.

THE DEFECT, on the backend that actually runs the money. Python sets `__context__` to whatever was
being handled when an exception was raised. Two chain walks that decide RETRYABILITY followed it:

* `app/core/payments/service.py::_iter_exception_chain`, which feeds both `_payment_db_sqlstate`
  and the SQLite busy check in `_classify_payment_db_error`;
* `app/core/clearing/service.py::_postgres_error_codes`, which feeds
  `_is_retryable_concurrency_error` - and clearing writes debt.

So a TERMINAL failure raised inside a `40001` handler - an ordinary shape in retry code - inherited
the conflict's SQLSTATE and was classified as transient. Retrying it cannot succeed.

WHAT IS REAL HERE. Both errors are produced by PostgreSQL, not constructed and not assigned:
the `40001` comes from a genuine SERIALIZABLE write-write conflict (two transactions read the same
debt, one commits, the other writes), and the terminal error is a genuine `23505` from the unique
index on `participants.pid`. The chaining is built by raising the terminal error INSIDE the
`except` block that is handling the conflict, which is the only way `__context__` is set by the
interpreter - `pytest.raises` has already left its handler when its block ends.

THE MUTATION that must turn these red again: put `current.__context__` back into either walk (the
`following = ...` step in `_iter_exception_chain`, or the same step in `_postgres_error_codes`).

WHAT THE LAST TEST PROVES, and why it is here. Narrowing a traversal can only be safe if the codes
the consumers need are still found. `_postgres_error_codes` is read for `55P03` at
`clearing/service.py:1611` and for `40001`/`40P01` at `:1717`, `:1743` and `:2121`. Both codes are
therefore produced for real here - the `55P03` by a genuine `lock_timeout` against a held row lock -
and asserted to survive the narrowed walk. That is a measurement of the consumers' inputs, not an
assertion about them.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.core.payments.service import _classify_payment_db_error
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import RetryablePaymentConflictException

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def serializable_factory():
    """SERIALIZABLE and a real pool: under READ COMMITTED the 40001 below does not exist at all."""
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=4,
        max_overflow=0,
        pool_timeout=10,
        isolation_level="SERIALIZABLE",
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class _World:
    def __init__(self, equivalent_id, creditor_id, debtor_id, debtor_pid):
        self.equivalent_id = equivalent_id
        self.creditor_id = creditor_id
        self.debtor_id = debtor_id
        self.debtor_pid = debtor_pid


async def _seed(factory) -> _World:
    n = uuid.uuid4().hex[:8]
    async with factory() as session:
        equivalent = Equivalent(code=f"CTX{n}".upper()[:16], precision=2, is_active=True)
        creditor = Participant(
            pid=f"CTXC_{n}", display_name="Creditor", public_key=f"pk_ctxc_{n}",
            type="person", status="active", profile={},
        )
        debtor = Participant(
            pid=f"CTXD_{n}", display_name="Debtor", public_key=f"pk_ctxd_{n}",
            type="person", status="active", profile={},
        )
        session.add_all([equivalent, creditor, debtor])
        await session.flush()
        session.add(
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=equivalent.id,
                amount=5,
            )
        )
        await session.commit()
        return _World(equivalent.id, creditor.id, debtor.id, debtor.pid)


async def _a_real_terminal_raised_inside_a_real_40001_handler(
    factory, world: _World
) -> tuple[DBAPIError, DBAPIError]:
    """Return (the genuine 40001, the genuine 23505 raised while handling it)."""
    async with factory() as loser, factory() as winner, factory() as third:
        # Both transactions READ the row first: that is what makes the later write a
        # serialization failure rather than a plain lock wait.
        await loser.execute(select(Debt.amount).where(Debt.debtor_id == world.debtor_id))
        await winner.execute(select(Debt.amount).where(Debt.debtor_id == world.debtor_id))

        await winner.execute(
            update(Debt).where(Debt.debtor_id == world.debtor_id).values(amount=7)
        )
        await winner.commit()

        try:
            await loser.execute(
                update(Debt).where(Debt.debtor_id == world.debtor_id).values(amount=9)
            )
            await loser.flush()
        except DBAPIError as conflict:
            assert getattr(conflict.orig, "sqlstate", None) == "40001", conflict
            # INSIDE the handler, so the interpreter sets `__context__` on what follows.
            try:
                third.add(
                    Participant(
                        pid=world.debtor_pid,  # already taken: a real unique violation
                        display_name="Duplicate",
                        public_key=f"pk_dup_{uuid.uuid4().hex[:8]}",
                        type="person",
                        status="active",
                        profile={},
                    )
                )
                await third.flush()
            except DBAPIError as terminal:
                await _quiet_rollback(loser)
                await _quiet_rollback(third)
                return conflict, terminal
            raise AssertionError("the duplicate insert did not fail")
        raise AssertionError("the concurrent update was not refused with a 40001")


async def _quiet_rollback(session) -> None:
    try:
        await session.rollback()
    except Exception:
        pass


def _reachable_the_old_way(exc: BaseException, target: BaseException) -> bool:
    """Would the OLD walk have reached `target`? `orig`/`__cause__`/`__context__`, TRANSITIVELY.

    The chain is deeper than one link: SQLAlchemy's `IntegrityError` carries the asyncpg adapter's
    own `IntegrityError` in its `__context__`, and the genuine `40001` sits further along. The old
    traversals pushed every node's `__context__` onto their stack, so depth cost them nothing -
    which is precisely why identity on a single link is the wrong thing to assert here.
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current is target:
            return True
        for linked in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return False


def _reachable_by_deliberate_wrapping(exc: BaseException, target: BaseException) -> bool:
    """Would the NEW rule reach `target`? `orig`/`__cause__` only."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current is target:
            return True
        following = getattr(current, "orig", None)
        if not isinstance(following, BaseException):
            following = current.__cause__
        current = following if isinstance(following, BaseException) else None
    return False


@pytest.mark.asyncio
async def test_a_terminal_error_inside_a_40001_handler_is_not_retryable(
    serializable_factory,
) -> None:
    """Both classifiers must read the 23505 they were handed, not the 40001 behind it."""
    world = await _seed(serializable_factory)
    conflict, terminal = await _a_real_terminal_raised_inside_a_real_40001_handler(
        serializable_factory, world
    )

    # NON-VACUITY, stated as the defect itself: the old traversal really would have found the
    # 40001 from this terminal error and answered "retryable"...
    assert _reachable_the_old_way(terminal, conflict), (
        "the real 40001 is not reachable through __context__ from the terminal error, so this "
        "stand does not reproduce the masking it exists to refute"
    )
    # ... and deliberate wrapping alone does not lead to it, which is what makes the fix a fix
    # rather than a coincidence of this particular error.
    assert not _reachable_by_deliberate_wrapping(terminal, conflict)

    # Read through the real function: the asyncpg adapter splits `sqlstate` from the underlying
    # error across links, so asserting it off one link would be brittle for the wrong reason.
    codes = ClearingService._postgres_error_codes(terminal)
    assert "23505" in codes, codes
    assert "40001" not in codes, (
        f"the terminal error still carries the conflict's SQLSTATE through __context__: {codes}"
    )

    assert not isinstance(
        _classify_payment_db_error(terminal), RetryablePaymentConflictException
    ), (
        "a terminal 23505 was classified as a retryable conflict because a 40001 sat in its "
        "__context__; the payment would be retried and the retry cannot succeed"
    )
    assert ClearingService._is_retryable_concurrency_error(terminal) is False, (
        "clearing would retry a terminal 23505 because a 40001 sat in its __context__ - and "
        "clearing writes debt"
    )


@pytest.mark.asyncio
async def test_a_genuine_40001_is_still_retryable_on_both_classifiers(
    serializable_factory,
) -> None:
    """The positive control: narrowing the walk must not lose a real conflict."""
    world = await _seed(serializable_factory)
    conflict, _terminal = await _a_real_terminal_raised_inside_a_real_40001_handler(
        serializable_factory, world
    )

    assert isinstance(
        _classify_payment_db_error(conflict), RetryablePaymentConflictException
    ), "a real serialization failure must still be retryable through orig/__cause__"
    assert ClearingService._is_retryable_concurrency_error(conflict) is True
    # The consumers at clearing/service.py:1717, :1743 and :2121 read exactly this set.
    assert "40001" in ClearingService._postgres_error_codes(conflict)


@pytest.mark.asyncio
async def test_a_genuine_55p03_is_still_found_by_the_narrowed_walk(
    serializable_factory,
) -> None:
    """The `55P03` consumer at clearing/service.py:1611, measured on a real lock timeout."""
    world = await _seed(serializable_factory)

    async with serializable_factory() as holder, serializable_factory() as waiter:
        await holder.execute(
            select(Debt.id).where(Debt.debtor_id == world.debtor_id).with_for_update()
        )
        await waiter.execute(text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(DBAPIError) as timed_out:
            await waiter.execute(
                select(Debt.id).where(Debt.debtor_id == world.debtor_id).with_for_update()
            )
        await _quiet_rollback(waiter)
        await _quiet_rollback(holder)

    assert getattr(timed_out.value.orig, "sqlstate", None) == "55P03", timed_out.value
    assert "55P03" in ClearingService._postgres_error_codes(timed_out.value), (
        "the narrowed walk must still find the interlock timeout code, or "
        "clearing/service.py:1611 stops turning it into a TimeoutException"
    )
