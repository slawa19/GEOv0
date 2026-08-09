import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.payments.engine import PaymentEngine
from app.utils.exceptions import GeoException


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


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _ScalarsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _CommitSession:
    bind = None

    def __init__(self, tx, locks):
        self.responses = [_ScalarResult(tx), _ScalarsResult(locks)]

    async def execute(self, _stmt, params=None):
        assert params is None
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_acquire_segment_advisory_locks_executes_pg_advisory_xact_lock_for_each_unique_segment():
    session = _Session()
    engine = PaymentEngine(session)

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
    engine = PaymentEngine(session)

    await engine._acquire_segment_advisory_lock_keys([9, -4, 9, 2, -4])

    assert all(
        "SET LOCAL lock_timeout" in sql
        for sql, _params in session.executed[0::2]
    )
    assert [params["key"] for _sql, params in session.executed[1::2]] == [-4, 2, 9]


@pytest.mark.asyncio
async def test_tx_lock_key_is_stable_and_uses_domain_separate_from_segment_keys():
    session = _Session()
    engine = PaymentEngine(session)
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
async def test_segment_lock_timeout_uses_one_decreasing_commit_budget(
    monkeypatch,
):
    from app.config import settings
    from app.core.payments import engine as engine_module

    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5)
    monotonic_values = iter([100.0, 101.0])
    monkeypatch.setattr(
        engine_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )
    session = _Session()
    engine = PaymentEngine(session)

    await engine._acquire_segment_advisory_lock_keys([1, 2])

    assert "5000ms" in session.executed[0][0]
    assert "4000ms" in session.executed[2][0]


def test_advisory_lock_budget_matches_service_zero_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0)

    engine = PaymentEngine(_Session())

    assert engine._advisory_lock_budget_s == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["prepare", "prepare_routes", "commit", "abort"])
async def test_all_payment_transitions_acquire_tx_key_before_first_tx_read(
    monkeypatch,
    operation,
):
    engine = PaymentEngine(SimpleNamespace(bind=None))
    events: list[str] = []

    class _StopAtRead(RuntimeError):
        pass

    async def _acquire_tx(_tx_id):
        events.append("tx-key")

    async def _get_tx(_tx_id):
        events.append("tx-read")
        raise _StopAtRead

    monkeypatch.setattr(engine, "_acquire_tx_advisory_lock", _acquire_tx)
    monkeypatch.setattr(engine, "_get_tx", _get_tx)

    with pytest.raises(_StopAtRead):
        if operation == "prepare":
            await engine.prepare("tx", ["A", "B"], Decimal("1"), uuid.uuid4())
        elif operation == "prepare_routes":
            await engine.prepare_routes(
                "tx",
                [(["A", "B"], Decimal("1"))],
                uuid.uuid4(),
            )
        elif operation == "commit":
            await engine.commit("tx")
        else:
            await engine.abort("tx")

    assert events == ["tx-key", "tx-read"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commit", "expected_reacquires"),
    [(True, 1), (False, 0)],
)
async def test_abort_reacquires_preheld_tx_lock_only_after_outer_rollback_retry(
    monkeypatch,
    commit,
    expected_reacquires,
):
    engine = PaymentEngine(SimpleNamespace(bind=None))
    reacquires: list[str] = []
    attempts = 0

    class _Retry(RuntimeError):
        pass

    class _Stop(RuntimeError):
        pass

    async def _acquire_tx(tx_id):
        reacquires.append(tx_id)

    async def _get_tx(_tx_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _Retry
        raise _Stop

    async def _run_twice(*, op, fn, use_savepoint=False):
        assert op == ("abort" if commit else "abort_nocommit")
        assert use_savepoint is (not commit)
        with pytest.raises(_Retry):
            await fn()
        return await fn()

    monkeypatch.setattr(engine, "_acquire_tx_advisory_lock", _acquire_tx)
    monkeypatch.setattr(engine, "_get_tx", _get_tx)
    monkeypatch.setattr(engine, "_run_uow_with_retry", _run_twice)

    with pytest.raises(_Stop):
        await engine.abort(
            "tx-retry",
            commit=commit,
            _tx_lock_already_held=True,
        )

    assert reacquires == ["tx-retry"] * expected_reacquires


def test_persisted_prepare_lock_parser_and_keys_share_validated_flows(monkeypatch):
    equivalent_id = uuid.uuid4()
    from_id = uuid.uuid4()
    to_id = uuid.uuid4()
    other_to_id = uuid.uuid4()
    seen: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []

    def _fake_segment_lock_key(
        *,
        equivalent_id,
        from_participant_id,
        to_participant_id,
    ) -> int:
        seen.append((equivalent_id, from_participant_id, to_participant_id))
        return 3 if to_participant_id == to_id else 7

    monkeypatch.setattr(
        PaymentEngine,
        "_segment_lock_key",
        staticmethod(_fake_segment_lock_key),
    )
    valid_flow = {
        "equivalent": str(equivalent_id),
        "from": str(from_id),
        "to": str(to_id),
        "amount": "8.00",
    }
    locks = [
        SimpleNamespace(
            id=uuid.uuid4(),
            effects={
                "flows": [
                    valid_flow,
                    dict(valid_flow),
                    {
                        "equivalent": str(equivalent_id),
                        "from": str(from_id),
                        "to": str(other_to_id),
                        "amount": "2.00",
                    },
                ]
            },
        )
    ]

    validated = PaymentEngine._parse_persisted_prepare_locks(locks)
    keys = PaymentEngine._segment_lock_keys_from_validated_flows(validated)

    assert keys == {3, 7}
    assert seen == [
        (equivalent_id, from_id, to_id),
        (equivalent_id, from_id, to_id),
        (equivalent_id, from_id, other_to_id),
    ]


@pytest.mark.parametrize(
    "effects",
    [
        None,
        {},
        {"flows": "not-a-list"},
        {"flows": []},
        {"flows": ["not-a-flow"]},
        {"flows": [{"from": str(uuid.uuid4()), "to": str(uuid.uuid4()), "amount": "1"}]},
        {
            "flows": [
                {
                    "equivalent": "not-a-uuid",
                    "from": str(uuid.uuid4()),
                    "to": str(uuid.uuid4()),
                    "amount": "1",
                }
            ]
        },
        {
            "flows": [
                {
                    "equivalent": str(uuid.uuid4()),
                    "from": str(uuid.uuid4()),
                    "to": str(uuid.uuid4()),
                    "amount": "NaN",
                }
            ]
        },
        {
            "flows": [
                {
                    "equivalent": str(uuid.uuid4()),
                    "from": str(uuid.uuid4()),
                    "to": str(uuid.uuid4()),
                    "amount": "0",
                }
            ]
        },
    ],
)
def test_persisted_prepare_lock_parser_fails_closed(effects):
    lock = SimpleNamespace(id=uuid.uuid4(), effects=effects)

    with pytest.raises(GeoException) as raised:
        PaymentEngine._parse_persisted_prepare_locks([lock])

    assert raised.value.code == "E010"


@pytest.mark.asyncio
async def test_commit_acquires_keys_derived_from_loaded_prepare_locks(monkeypatch):
    equivalent_id = uuid.uuid4()
    from_id = uuid.uuid4()
    to_id = uuid.uuid4()
    tx_id = str(uuid.uuid4())
    lock = SimpleNamespace(
        id=uuid.uuid4(),
        effects={
            "flows": [
                {
                    "equivalent": str(equivalent_id),
                    "from": str(from_id),
                    "to": str(to_id),
                    "amount": "8.00",
                }
            ]
        }
    )
    session = _CommitSession(SimpleNamespace(state="PREPARED"), [lock])
    engine = PaymentEngine(session)
    expected_key = PaymentEngine._segment_lock_key(
        equivalent_id=equivalent_id,
        from_participant_id=from_id,
        to_participant_id=to_id,
    )
    acquired: list[tuple[str, set[int] | None]] = []

    class _StopAfterAcquire(RuntimeError):
        pass

    async def _capture_tx_acquire(_tx_id):
        acquired.append(("tx", None))

    async def _capture_acquire(keys):
        acquired.append(("segment", set(keys)))
        raise _StopAfterAcquire

    monkeypatch.setattr(engine, "_acquire_tx_advisory_lock", _capture_tx_acquire)
    monkeypatch.setattr(engine, "_acquire_segment_advisory_lock_keys", _capture_acquire)

    with pytest.raises(_StopAfterAcquire):
        await engine.commit(tx_id)

    assert acquired == [("tx", None), ("segment", {expected_key})]
