from typing import AsyncGenerator
import os
import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.base import Base
from app.main import app

# --- Database Fixtures ---

# IMPORTANT: always point this to a dedicated test DB.
# For non-SQLite backends this test suite will DROP/CREATE schema when explicitly allowed.
# Defaulting to settings.DATABASE_URL is unsafe because it may point at a developer DB.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///./.pytest_geov0.db",
)

# Tests should not start background jobs or best-effort throttling.
settings.RATE_LIMIT_ENABLED = False
settings.RECOVERY_ENABLED = False
settings.INTEGRITY_CHECKPOINT_ENABLED = False

_is_sqlite = TEST_DATABASE_URL.startswith("sqlite")

# NOTE: For asyncpg on Windows, pooled connections can be bound to a previous
# event loop between tests (pytest-asyncio uses per-test loops by default),
# causing errors like "Event loop is closed" and "another operation is in progress".
# Using NullPool avoids reusing loop-bound connections across tests.
engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={"timeout": 30} if _is_sqlite else {},
)
TestingSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    join_transaction_mode="create_savepoint",
)

_schema_ready = False
_schema_lock = asyncio.Lock()


async def _ensure_schema_initialized() -> None:
    global _schema_ready
    if _schema_ready:
        return

    async with _schema_lock:
        if _schema_ready:
            return

        driver = engine.url.drivername
        if driver != "sqlite+aiosqlite":
            if os.environ.get("GEO_TEST_ALLOW_DB_RESET") != "1":
                raise RuntimeError(
                    "Refusing to reset a non-SQLite database for tests. "
                    "Set GEO_TEST_ALLOW_DB_RESET=1 and ensure TEST_DATABASE_URL points to a dedicated test DB. "
                    f"Got driver: {driver}."
                )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        _schema_ready = True


@pytest.fixture(scope="session", autouse=True)
def init_db() -> None:
    """Guardrail fixture.

    Schema initialization is performed lazily in the first `db_session` to keep
    the suite async-fixture-scope-free.
    """
    return None


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engines_at_end() -> AsyncGenerator[None, None]:
    """Dispose async engines once per test session."""

    yield

    await engine.dispose()
    try:
        from app.db.session import engine as app_engine

        await app_engine.dispose()
    except Exception:
        pass


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """SQLAlchemy session with a per-test transaction that is rolled back."""

    await _ensure_schema_initialized()

    # Simulator runtime can spawn background tasks (heartbeat / real-mode tick)
    # that keep DB connections open. Under SQLite this can race with our per-test
    # hard reset (table deletes) and produce "database is locked".
    try:
        from app.core.simulator.runtime import runtime as simulator_runtime

        # Ensure simulator cleanup uses the same test DB sessionmaker.
        import app.db.session as app_db_session
        _orig_async_session_local = app_db_session.AsyncSessionLocal
        app_db_session.AsyncSessionLocal = TestingSessionLocal
        try:
            with simulator_runtime._lock:
                run_ids = list(simulator_runtime._runs.keys())

            for run_id in run_ids:
                try:
                    await simulator_runtime.stop(run_id)
                except Exception:
                    pass

            # Release any pooled SQLite connections that could keep locks.
            if _is_sqlite:
                try:
                    await app_db_session.engine.dispose()
                except Exception:
                    pass
        finally:
            app_db_session.AsyncSessionLocal = _orig_async_session_local
    except Exception:
        pass

    if _is_sqlite:
        # SQLite has flaky transactional isolation under rapid commit-heavy tests.
        # For stability we hard-reset DB state per test.
        # Truncating tables is much faster than drop/create for the whole schema.
        try:
            # Best-effort: ensure any other engine in the process is disposed before writes.
            from app.db.session import engine as app_engine

            await app_engine.dispose()
        except Exception:
            pass

        # SQLite can intermittently raise "database is locked" if some background task
        # is still releasing a connection/transaction. Use a busy timeout + a small
        # retry loop to de-flake test teardown/setup on Windows.
        for attempt in range(6):
            try:
                async with engine.begin() as conn:
                    try:
                        await conn.execute(text("PRAGMA busy_timeout = 30000"))
                        await conn.execute(text("PRAGMA journal_mode = WAL"))
                    except Exception:
                        # Best-effort pragmas.
                        pass

                    for table in reversed(Base.metadata.sorted_tables):
                        await conn.execute(table.delete())
                break
            except OperationalError as e:
                msg = str(e).lower()
                if "database is locked" not in msg or attempt >= 5:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))

        async with TestingSessionLocal() as session:
            yield session
        return

    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with TestingSessionLocal(bind=connection) as session:
            # Use SAVEPOINTs so application code can call session.commit() without
            # breaking test isolation.
            await session.begin_nested()

            @event.listens_for(session.sync_session, "after_transaction_end")
            def _restart_savepoint(sess, trans):
                if trans.nested and trans._parent and not trans._parent.nested:
                    sess.begin_nested()

            yield session
        await transaction.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient bound to the FastAPI app with a DB override."""

    # IMPORTANT: simulator runtime uses app.db.session.AsyncSessionLocal directly
    # (e.g. in the real-mode heartbeat loop). Patch it to point at the test
    # sessionmaker so background tasks operate on the same DB as request handlers.
    import app.db.session as app_db_session
    _orig_async_session_local = app_db_session.AsyncSessionLocal
    app_db_session.AsyncSessionLocal = TestingSessionLocal

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        # Intentionally do NOT run FastAPI lifespan in tests: it starts background jobs
        # (recovery/integrity) that can interfere with DB isolation.
        async with AsyncClient(app=app, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        app_db_session.AsyncSessionLocal = _orig_async_session_local


# --- Sync E2E Example Fixtures ---


@pytest.fixture
def http_client():
    """Synchronous client for placeholder e2e example tests."""
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def reset_state():
    """Placeholder reset hook for example tests."""
    yield


@pytest.fixture
def collect_artifacts(tmp_path):
    """Placeholder artifact collector for example tests."""

    def _collect(name: str, payload: object | None = None) -> None:
        return None

    return _collect


@pytest.fixture
def alice_keys():
    public_key, private_key = generate_keypair()
    return private_key, public_key, "alice"


@pytest.fixture
def bob_keys():
    public_key, private_key = generate_keypair()
    return private_key, public_key, "bob"

# --- Auth Helpers ---

@pytest.fixture
def test_user_keys():
    """Generates a keypair for a test user."""
    public_key, private_key = generate_keypair()
    return {"public": public_key, "private": private_key}

@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user_keys):
    """
    Registers a user and logs them in, returning the Authorization header.
    """
    import base64
    from nacl.signing import SigningKey

    # 1. Register
    user_pid = None
    message = canonical_json(
        {
            "display_name": "Test User",
            "type": "person",
            "public_key": test_user_keys["public"],
            "profile": {},
        }
    )
    signing_key_bytes = base64.b64decode(test_user_keys["private"])
    signing_key = SigningKey(signing_key_bytes)
    signature_b64 = base64.b64encode(signing_key.sign(message).signature).decode("utf-8")

    user_data = {
        "display_name": "Test User",
        "type": "person",
        "public_key": test_user_keys["public"],
        "signature": signature_b64,
        "profile": {},
    }
    
    # We need to register first. Since this fixture might depend on DB state,
    # we do it via API to simulate real flow, or direct DB insert if preferred.
    # Using API ensures we test the full path.
    response = await client.post("/api/v1/participants", json=user_data)
    assert response.status_code == 201
    user_pid = response.json()["pid"]

    # 2. Challenge
    response = await client.post("/api/v1/auth/challenge", json={"pid": user_pid})
    assert response.status_code == 200
    challenge_data = response.json()
    challenge_str = challenge_data["challenge"]

    # 3. Sign the challenge string
    signature_bytes = signing_key.sign(challenge_str.encode('utf-8')).signature
    signature_b64 = base64.b64encode(signature_bytes).decode('utf-8')

    # 4. Login
    login_data = {
        "pid": user_pid,
        "challenge": challenge_str,
        "signature": signature_b64
    }
    response = await client.post("/api/v1/auth/login", json=login_data)
    assert response.status_code == 200
    tokens = response.json()

    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest_asyncio.fixture
async def auth_user(client: AsyncClient):
    """Registers + logs in a user, returning headers and key material.

    This is used by tests that need to produce Ed25519 signatures for API requests.
    """
    import base64
    from nacl.signing import SigningKey

    public_key, private_key = generate_keypair()

    message = canonical_json(
        {
            "display_name": "Test User",
            "type": "person",
            "public_key": public_key,
            "profile": {},
        }
    )
    signing_key = SigningKey(base64.b64decode(private_key))
    signature_b64 = base64.b64encode(signing_key.sign(message).signature).decode("utf-8")

    user_data = {
        "display_name": "Test User",
        "type": "person",
        "public_key": public_key,
        "signature": signature_b64,
        "profile": {},
    }

    response = await client.post("/api/v1/participants", json=user_data)
    assert response.status_code == 201
    user_pid = response.json()["pid"]

    response = await client.post("/api/v1/auth/challenge", json={"pid": user_pid})
    assert response.status_code == 200
    challenge_str = response.json()["challenge"]

    login_signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode(
        "utf-8"
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"pid": user_pid, "challenge": challenge_str, "signature": login_signature_b64},
    )
    assert response.status_code == 200
    tokens = response.json()

    return {
        "pid": user_pid,
        "public_key": public_key,
        "private_key": private_key,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }
