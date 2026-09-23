"""T1549: the PostgreSQL test engine runs at the application's isolation level, read from its setting.

The application engine takes `isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL` for PostgreSQL
(`app/db/session.py`), default SERIALIZABLE. Until 2026-09-14 the shared test engine in `tests/conftest.py`
passed nothing, so the whole PostgreSQL acceptance tier ran at the server default READ COMMITTED. This
module holds the switch in place.

It checks EFFECT, not only shape: a fresh session from the shared sessionmaker, and the per-test
`db_session` connection, must REPORT the application's level from the server. A stand that proves
nothing is refused first - if the server default already equalled the setting, removing the engine
option would change nothing and this guard would pass vacuously.

MUTATIONS, measured 2026-09-14: remove the isolation kwargs from the conftest engine - red; replace them
with a literal `isolation_level="READ COMMITTED"` - red.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


def _normalised(level: str) -> str:
    return str(level).strip().lower().replace("_", " ")


@pytest.mark.asyncio
async def test_the_shared_test_engine_connects_at_the_application_isolation(db_session) -> None:
    from tests.conftest import TEST_DATABASE_URL, TestingSessionLocal, engine

    expected = _normalised(settings.DB_POSTGRES_ISOLATION_LEVEL)

    # NON-VACUITY: an engine that asks for nothing gets the server default, and it must differ from the
    # application's level, or the assertions below cannot tell a configured engine from a bare one.
    bare = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with bare.connect() as conn:
            server_default = _normalised((await conn.execute(text("SHOW transaction_isolation"))).scalar_one())
    finally:
        await bare.dispose()
    assert server_default != expected, (
        f"stand: the server default ({server_default!r}) already equals the application's level; "
        f"this guard cannot detect a test engine that asks for nothing"
    )

    async with TestingSessionLocal() as session:
        fresh = _normalised((await session.execute(text("SHOW transaction_isolation"))).scalar_one())
    per_test = _normalised((await db_session.execute(text("SHOW transaction_isolation"))).scalar_one())

    assert fresh == expected, (
        f"the shared PostgreSQL test engine runs at {fresh!r}, the application at {expected!r} "
        f"(settings.DB_POSTGRES_ISOLATION_LEVEL); acceptance measured here would not transfer"
    )
    assert per_test == expected, f"the per-test db_session connection runs at {per_test!r}, not {expected!r}"
    assert _normalised(engine.sync_engine.dialect._on_connect_isolation_level or "") == expected


def test_the_test_engine_isolation_is_read_from_the_setting_not_a_literal(monkeypatch) -> None:
    """A literal equal to today's default would pass the effect test above; this one moves the setting."""

    from tests import conftest

    monkeypatch.setattr(settings, "DB_POSTGRES_ISOLATION_LEVEL", "REPEATABLE READ")
    assert conftest._test_engine_isolation_kwargs("postgresql") == {"isolation_level": "REPEATABLE READ"}
    assert conftest._test_engine_isolation_kwargs("sqlite") == {}
