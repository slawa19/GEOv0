"""The lock primitives of `MoneyBoundary` as they execute, and the order the payment's binding phase takes them.

019 STAGE 4 (`T1906`; manifest `t1901-manifest.md` 5.1). `PaymentEngine` is deleted. The tests of the
primitives themselves (pair, transaction and owner keys; the one decreasing budget) were already on
`MoneyBoundary` since stage 2 and stay until stage 5 removes the locks. DROPPED with the engine, each
checking a removed contract:

* `test_tx_preflight_acquires_every_persisted_equivalent_before_tx_lock`,
  `test_abort_preflight_does_not_invent_owner_for_empty_or_malformed_locks` - the owner preflight derived
  from PERSISTED reservations of an engine transition; no transition reads reservations any more;
* `test_abort_reacquires_preheld_tx_lock_only_after_outer_rollback_retry` - the engine abort's lock
  re-acquisition across its own unit-of-work retry; the retry owner is `pay()` on a fresh transaction;
* `test_persisted_prepare_lock_parser_and_keys_share_validated_flows`,
  `test_persisted_prepare_lock_parser_fails_closed` - the parser of persisted reservation effects;
* `test_commit_acquires_keys_derived_from_loaded_prepare_locks` - the engine commit's owner -> tx -> pair
  order derived from loaded reservations: its order SURVIVES, on the declared routes, in
  `test_the_binding_phase_takes_the_pair_locks_after_owner_and_tx_and_before_capacity` below.

`test_all_payment_transitions_acquire_owner_before_tx_and_first_tx_read` (four engine transitions, a
flagged assertion: "owner before any row") is REWRITTEN IN PLACE for the one payment path that is left,
`PaymentService._bind_payment`; the real races of the same contract are
`tests/integration/test_p019_owner_before_row_races_postgres.py`.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Session:
    bind = _Bind()

    def __init__(self):
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))


@pytest.mark.asyncio
async def test_acquire_segment_advisory_locks_executes_pg_advisory_xact_lock_for_each_unique_segment():
    session = _Session()
    engine = MoneyBoundary(session)

    eq = uuid.uuid4()
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    participant_map = {"A": a, "B": b, "C": c, "D": d}

    routes = [
        (["A", "B", "D"], Decimal("5")),
        (["A", "C", "D"], Decimal("5")),
    ]

    await engine._acquire_segment_advisory_locks(
        equivalent_id=eq,
        routes=routes,
        participant_map=participant_map,
    )

    # The remaining transaction budget is applied before every unique lock.
    assert len(session.executed) == 8
    timeout_calls = session.executed[0::2]
    lock_calls = session.executed[1::2]
    assert all("SET LOCAL lock_timeout" in sql for sql, _params in timeout_calls)
    assert all("pg_advisory_xact_lock" in sql for sql, _params in lock_calls)
    assert all("key" in params for _sql, params in lock_calls)


@pytest.mark.asyncio
async def test_acquire_segment_advisory_lock_keys_deduplicates_and_sorts_globally():
    session = _Session()
    engine = MoneyBoundary(session)

    await engine._acquire_segment_advisory_lock_keys([9, -4, 9, 2, -4])

    assert all(
        "SET LOCAL lock_timeout" in sql
        for sql, _params in session.executed[0::2]
    )
    assert [params["key"] for _sql, params in session.executed[1::2]] == [-4, 2, 9]


@pytest.mark.asyncio
async def test_tx_lock_key_is_stable_and_uses_domain_separate_from_segment_keys():
    session = _Session()
    engine = MoneyBoundary(session)
    tx_id = str(uuid.uuid4())

    assert engine._tx_lock_key(tx_id) == engine._tx_lock_key(tx_id)
    assert -(2**31) <= engine._tx_lock_key(tx_id) < 2**31

    await engine._acquire_tx_advisory_lock(tx_id)
    await engine._acquire_segment_advisory_lock_keys([17])

    tx_sql, tx_params = session.executed[1]
    segment_sql, segment_params = session.executed[3]
    assert "SET LOCAL lock_timeout" in session.executed[0][0]
    assert "SET LOCAL lock_timeout" in session.executed[2][0]
    assert "pg_advisory_xact_lock" in tx_sql
    assert set(tx_params) == {"namespace", "key"}
    assert "pg_advisory_xact_lock" in segment_sql
    assert segment_params == {"key": 17}


@pytest.mark.asyncio
async def test_equivalent_owner_locks_are_deduplicated_sorted_and_domain_separated():
    session = _Session()
    engine = MoneyBoundary(session)
    equivalent_a = uuid.uuid4()
    equivalent_b = uuid.uuid4()

    key_a = engine._equivalent_owner_lock_key(equivalent_a)
    key_b = engine._equivalent_owner_lock_key(equivalent_b)
    assert key_a == engine._equivalent_owner_lock_key(equivalent_a)
    assert key_a != key_b
    assert -(2**31) <= key_a < 2**31

    await engine._acquire_equivalent_owner_locks(
        [equivalent_b, equivalent_a, equivalent_b]
    )
    await engine._acquire_tx_advisory_lock("tx-owner-domain")

    owner_calls = session.executed[1:4:2]
    tx_call = session.executed[5]
    assert [params["key"] for _sql, params in owner_calls] == sorted({key_a, key_b})
    assert len({params["namespace"] for _sql, params in owner_calls}) == 1
    assert owner_calls[0][1]["namespace"] != tx_call[1]["namespace"]


@pytest.mark.asyncio
async def test_segment_lock_timeout_uses_one_decreasing_commit_budget(
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

    await engine._acquire_segment_advisory_lock_keys([1, 2])

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


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _BindingSession:
    """Records each statement of the binding phase as a row read; answers the participant read, then stops."""

    bind = None

    def __init__(self, events, participants):
        self.events = events
        self.participants = participants
        self.reads = 0

    async def execute(self, _stmt, params=None):
        self.reads += 1
        self.events.append("row-read")
        if self.reads == 1 and self.participants is not None:
            return _Rows(self.participants)
        raise _StopAtRead


def _binding_service(monkeypatch, events, participants=None) -> PaymentService:
    service = PaymentService(_BindingSession(events, participants))
    boundary = service._boundary
    assert type(boundary) is MoneyBoundary

    async def _owner(equivalent_ids):
        events.append("owner-key")

    async def _tx(_tx_id):
        events.append("tx-key")

    async def _pairs(*, equivalent_id, routes, participant_map):
        events.append("pair-keys")

    monkeypatch.setattr(boundary, "_acquire_equivalent_owner_locks", _owner)
    monkeypatch.setattr(boundary, "_acquire_tx_advisory_lock", _tx)
    monkeypatch.setattr(boundary, "_acquire_segment_advisory_locks", _pairs)
    return service


@pytest.mark.asyncio
async def test_the_binding_phase_acquires_owner_before_tx_and_first_row_read(monkeypatch):
    """"Owner before any row": the equivalent owner lock, then the transaction lock, then the first read.

    Until 019 stage 4 this was `test_all_payment_transitions_acquire_owner_before_tx_and_first_tx_read`
    over the engine's `prepare`, `prepare_routes`, `commit` and `abort`; the payment's one binding phase
    is what is left of all four. RED if a row is read before the owner lock, or the transaction lock is
    taken first.
    """

    events: list[str] = []
    service = _binding_service(monkeypatch, events)

    with pytest.raises(_StopAtRead):
        await service._bind_payment("tx", [(["A", "B"], Decimal("1"))], uuid.uuid4())

    assert events == ["owner-key", "tx-key", "row-read"]


@pytest.mark.asyncio
async def test_the_binding_phase_takes_the_pair_locks_after_owner_and_tx_and_before_capacity(monkeypatch):
    """The pair locks come after the owner and transaction locks and the participant lookup their keys
    need, and before the first capacity read (the trust line of the first segment). RED if the capacity
    is read before the pair locks."""

    events: list[str] = []
    service = _binding_service(monkeypatch, events, participants=[(uuid.uuid4(), "A"), (uuid.uuid4(), "B")])

    with pytest.raises(_StopAtRead):
        await service._bind_payment("tx", [(["A", "B"], Decimal("1"))], uuid.uuid4())

    assert events == ["owner-key", "tx-key", "row-read", "pair-keys", "row-read"]
