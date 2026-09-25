"""The lock primitives of `MoneyBoundary` as they execute, and where the payment's binding phase takes its lock.

019 STAGE 5 (`T1909`, decision `KEEP-EQUIVALENT-LOCK` of the fourth consultation, 2026-09-25). ONE advisory
identity per equivalent remains, in two modes; the statements each primitive sends are pinned here on a
recording session:

* SHARED, transaction level - `_acquire_shared_equivalent_locks_in_order`: one
  `pg_advisory_xact_lock_shared(:namespace, :key)` per UNIQUE equivalent, in SORTED key order, each preceded
  by the remaining budget as `SET LOCAL lock_timeout` (one decreasing budget per `MoneyBoundary`);
* SHARED, staged entry - `acquire_shared_equivalent_locks`: reads `lock_timeout`, takes the set as above and
  restores the caller's `lock_timeout` for the rest of the caller's transaction;
* EXCLUSIVE, session level - `acquire_exclusive_equivalent_session_lock` (`pg_advisory_lock`, the clearing
  only) and `release_exclusive_equivalent_session_lock` (`pg_advisory_unlock`, whose answer is returned).

What these do NOT show - that the modes really exclude or admit each other in PostgreSQL - is the real
concurrency of `tests/integration/test_p019_equivalent_lock_modes_postgres.py`.

DROPPED at stage 5, each with its removed contract (the primitives no longer exist):

* `test_acquire_segment_advisory_locks_executes_pg_advisory_xact_lock_for_each_unique_segment`,
  `test_acquire_segment_advisory_lock_keys_deduplicates_and_sorts_globally` - the pair locks;
* `test_tx_lock_key_is_stable_and_uses_domain_separate_from_segment_keys` - the transaction lock and its
  namespace;
* `test_the_binding_phase_takes_the_pair_locks_after_owner_and_tx_and_before_capacity` - the pair locks'
  place in the binding phase; what is left of its order (the lock before the participant lookup and the
  capacity reads) is `test_the_binding_phase_takes_the_shared_equivalent_lock_before_the_first_row_read`.

REWRITTEN IN PLACE: the owner-lock dedupe/sort/namespace test (now the shared mode, `_EQUIVALENT_OWNER_LOCK_NAMESPACE`
is the one namespace left), the one-decreasing-budget test (it ran over pair keys; now over two equivalents),
and the binding-phase order (owner -> tx -> row is now shared -> row).

(Stage 4 history: the engine's transitions and persisted-reservation parsers were dropped with
`PaymentEngine`, see `specs/019-payment-one-transaction/spec.md`, stage 4 changelog.)
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
from app.core.payments.service import PaymentService


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Session:
    """Records every statement; `scalar` answers from `scalars` in order."""

    bind = _Bind()

    def __init__(self, scalars=()):
        self.executed = []
        self._scalars = list(scalars)

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))

    async def scalar(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))
        return self._scalars.pop(0)


def _lock_statements(executed):
    return [(sql, params) for sql, params in executed if "pg_advisory" in sql]


@pytest.mark.asyncio
async def test_shared_equivalent_locks_are_deduplicated_sorted_and_in_the_one_namespace():
    """One shared transaction-level lock per UNIQUE equivalent, sorted by key, each after its timeout.

    RED if a key is taken twice, out of order (two staged batches over {a, b} and {b, a} would then be
    able to interleave with a clearing's exclusive request into a deadlock), in another mode or namespace.
    """

    session = _Session()
    engine = MoneyBoundary(session)
    equivalent_a = uuid.uuid4()
    equivalent_b = uuid.uuid4()

    key_a = engine._equivalent_owner_lock_key(equivalent_a)
    key_b = engine._equivalent_owner_lock_key(equivalent_b)
    assert key_a == engine._equivalent_owner_lock_key(equivalent_a)
    assert key_a != key_b
    assert -(2**31) <= key_a < 2**31

    await engine._acquire_shared_equivalent_locks_in_order([equivalent_b, equivalent_a, equivalent_b])

    assert len(session.executed) == 4, session.executed
    assert all("SET LOCAL lock_timeout" in sql for sql, _params in session.executed[0::2])
    lock_calls = session.executed[1::2]
    assert all("pg_advisory_xact_lock_shared(:namespace, :key)" in sql for sql, _params in lock_calls)
    assert [params["key"] for _sql, params in lock_calls] == sorted({key_a, key_b})
    assert {params["namespace"] for _sql, params in lock_calls} == {_EQUIVALENT_OWNER_LOCK_NAMESPACE}


@pytest.mark.asyncio
async def test_the_staged_entry_takes_the_shared_set_and_restores_the_callers_lock_timeout():
    """`acquire_shared_equivalent_locks`: `SHOW lock_timeout`, the shared set, then the caller's value back.

    The lock budget is a `SET LOCAL`, which would otherwise outlive the lock and become the timeout policy
    of every later statement of the caller's (the tick's, the inject's) transaction. RED if the restore is
    missing, restores another value, or comes before the locks.
    """

    session = _Session(scalars=["1234ms"])
    engine = MoneyBoundary(session)
    equivalent_a, equivalent_b = uuid.uuid4(), uuid.uuid4()

    await engine.acquire_shared_equivalent_locks([equivalent_a, equivalent_b])

    sqls = [sql for sql, _params in session.executed]
    assert sqls[0] == "SHOW lock_timeout", sqls
    locks = _lock_statements(session.executed)
    assert [params["key"] for _sql, params in locks] == sorted(
        {engine._equivalent_owner_lock_key(equivalent_a), engine._equivalent_owner_lock_key(equivalent_b)}
    )
    assert all("pg_advisory_xact_lock_shared" in sql for sql, _params in locks)
    restore_sql, restore_params = session.executed[-1]
    assert "set_config('lock_timeout', :lock_timeout, true)" in restore_sql, session.executed
    assert restore_params == {"lock_timeout": "1234ms"}
    assert sqls.index(restore_sql) > max(sqls.index(sql) for sql, _params in locks)


@pytest.mark.asyncio
async def test_the_exclusive_session_lock_and_its_release_use_the_same_identity():
    """The clearing's lock: `pg_advisory_lock` (SESSION level, exclusive) after its timeout; the release is
    `pg_advisory_unlock` on the same namespace and key and returns PostgreSQL's answer - False is what makes
    the clearing invalidate its connection instead of pooling it with the lock."""

    equivalent = uuid.uuid4()
    session = _Session(scalars=[True, False])
    engine = MoneyBoundary(session)
    identity = {
        "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
        "key": engine._equivalent_owner_lock_key(equivalent),
    }

    await engine.acquire_exclusive_equivalent_session_lock(equivalent)
    assert "SET LOCAL lock_timeout" in session.executed[0][0]
    lock_sql, lock_params = session.executed[1]
    assert "pg_advisory_lock(:namespace, :key)" in lock_sql
    assert "xact" not in lock_sql and "shared" not in lock_sql
    assert lock_params == identity

    assert await engine.release_exclusive_equivalent_session_lock(equivalent) is True
    assert await engine.release_exclusive_equivalent_session_lock(equivalent) is False
    unlocks = session.executed[2:]
    assert all("pg_advisory_unlock(:namespace, :key)" in sql for sql, _params in unlocks), unlocks
    assert all(params == identity for _sql, params in unlocks)


@pytest.mark.asyncio
async def test_advisory_lock_timeout_uses_one_decreasing_commit_budget(
    monkeypatch,
):
    from app.config import settings
    from app.core import money_boundary as money_boundary_module

    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5)
    monotonic_values = iter([100.0, 101.0])
    monkeypatch.setattr(
        money_boundary_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )
    session = _Session()
    engine = MoneyBoundary(session)

    await engine._acquire_shared_equivalent_locks_in_order([uuid.uuid4(), uuid.uuid4()])

    assert "5000ms" in session.executed[0][0]
    assert "4000ms" in session.executed[2][0]


def test_advisory_lock_budget_matches_service_zero_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0)

    engine = MoneyBoundary(_Session())

    assert engine._advisory_lock_budget_s == 5.0


class _StopAtRead(RuntimeError):
    pass


class _BindingSession:
    """Records each statement of the binding phase as a row read, and stops at the first."""

    bind = None

    def __init__(self, events):
        self.events = events

    async def execute(self, _stmt, params=None):
        self.events.append("row-read")
        raise _StopAtRead


@pytest.mark.asyncio
async def test_the_binding_phase_takes_the_shared_equivalent_lock_before_the_first_row_read(monkeypatch):
    """"The lock before any row": the payment's shared equivalent lock, for its equivalent, then the first read.

    Until 019 stage 5 this was owner -> tx -> row; the transaction lock is gone. RED if a row is read before
    the lock (a clearing holding the exclusive lock would then not keep this payment's reads out), or if the
    lock is taken for another equivalent.
    """

    events: list[str] = []
    service = PaymentService(_BindingSession(events))
    boundary = service._boundary
    assert type(boundary) is MoneyBoundary
    equivalent_id = uuid.uuid4()
    locked: list[list[uuid.UUID]] = []

    async def _shared(equivalent_ids):
        locked.append(list(equivalent_ids))
        events.append("shared-equivalent-lock")

    monkeypatch.setattr(boundary, "_acquire_shared_equivalent_locks_in_order", _shared)

    with pytest.raises(_StopAtRead):
        await service._bind_payment("tx", [(["A", "B"], Decimal("1"))], equivalent_id)

    assert events == ["shared-equivalent-lock", "row-read"]
    assert locked == [[equivalent_id]]
