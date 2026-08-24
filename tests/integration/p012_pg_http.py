"""An HTTP client fixture usable on the PostgreSQL tier, for the 012 money reproducers.

NOT a test module - it is imported by
`test_p012_rt1_signed_amount_versus_stored_amount_postgres.py` and
`test_p012_rt2_precision_1_amount_is_erased_on_the_wire_postgres.py`, which are the first
PostgreSQL-marked tests in this suite to drive the app over HTTP.

WHY IT EXISTS. The shared `client` fixture (`tests/conftest.py`) binds the app to the
savepoint-wrapped `db_session`, which installs an `after_transaction_end` listener that restarts
a SAVEPOINT whenever one ends. `PaymentEngine._apply_flow` (`app/core/payments/engine.py:1401`)
enters `async with self.session.begin_nested()`, and the listener fires while that context
manager is unwinding, so SQLAlchemy raises

    InvalidRequestError: Can't operate on closed transaction inside context manager.

and every payment answers HTTP 500. Measured 2026-08-24 on `geov0_test_ci`: with the shared
fixture even a plain `amount: "10.25"` returns 500, i.e. a money reproducer built on it would be
red for a reason that has nothing to do with money. Existing PostgreSQL-marked payment tests
sidestep this by calling `PaymentEngine` directly on `TestingSessionLocal()` sessions and never
touching the API; the 012 reproducers must go through the door, so they need this instead.

CONSEQUENCE: rows are really committed. Every caller owns its own equivalent and participants and
deletes them at teardown.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_db
from app.main import app


def make_pg_client_fixture():
    """Return a fresh `pg_client` fixture function.

    A factory rather than a shared fixture object so each test module binds the name itself;
    importing a fixture across test modules works but reads as an accidental redefinition.
    """

    @pytest_asyncio.fixture
    async def pg_client() -> AsyncGenerator[AsyncClient, None]:
        from tests.conftest import TestingSessionLocal, _ensure_schema_initialized

        await _ensure_schema_initialized()

        import app.db.session as app_db_session

        original_sessionmaker = app_db_session.AsyncSessionLocal
        app_db_session.AsyncSessionLocal = TestingSessionLocal

        async def override_get_db():
            async with TestingSessionLocal() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        try:
            async with AsyncClient(app=app, base_url="http://test") as http_client:
                yield http_client
        finally:
            app.dependency_overrides.clear()
            app_db_session.AsyncSessionLocal = original_sessionmaker

    return pg_client
