from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError


class _NestedCtx:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        self._session.begin_nested_enters += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._session.begin_nested_exits += 1
        # emulate SQLAlchemy savepoint behavior: swallow nothing
        return False


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.begin_nested_enters = 0
        self.begin_nested_exits = 0
        self.rollback_calls = 0

    def begin_nested(self):
        return _NestedCtx(self)

    async def rollback(self):
        self.rollback_calls += 1


@pytest.mark.asyncio
async def test_uow_retry_uses_savepoint_and_does_not_rollback_session(monkeypatch):
    from app.core.payments.engine import PaymentEngine

    session = _FakeAsyncSession()
    eng = PaymentEngine(session)  # type: ignore[arg-type]

    # Make deterministic / fast.
    eng._retry_attempts = 2
    eng._retry_base_delay_s = 0.0
    eng._retry_max_delay_s = 0.0

    # Force Postgres + retryable.
    monkeypatch.setattr(eng, "_is_postgres", lambda: True)

    calls = {"n": 0}

    class _FakePgError(Exception):
        sqlstate = "40P01"

    async def _fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise DBAPIError(
                statement="SELECT 1",
                params=None,
                orig=_FakePgError("deadlock_detected"),
                connection_invalidated=False,
            )
        return "ok"

    res = await eng._run_uow_with_retry(op="t", fn=_fn, use_savepoint=True)
    assert res == "ok"

    assert calls["n"] == 2
    assert session.begin_nested_enters == 2
    assert session.begin_nested_exits == 2

    # When using savepoints, we must NOT rollback the whole session.
    assert session.rollback_calls == 0
