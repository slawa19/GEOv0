from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.base import Base
from app.main import app

# --- Database Fixtures ---

# IMPORTANT: for real production tests, use a separate TEST_DATABASE_URL.
TEST_DATABASE_URL = settings.DATABASE_URL

# Test suite runs many HTTP calls quickly; disable best-effort rate limiting
# to avoid cross-test interference and flaky 429s.
settings.RATE_LIMIT_ENABLED = False

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@pytest.fixture(scope="session", autouse=True)
def init_db() -> None:
    """Initialize the database schema once per test session.

    This avoids session-scoped async fixtures (which require overriding pytest-asyncio's
    `event_loop` fixture) and prevents pytest-asyncio deprecation warnings.
    """

    if engine.url.drivername != "sqlite+aiosqlite":
        raise RuntimeError(
            "Test suite expects sqlite+aiosqlite DATABASE_URL by default. "
            f"Got: {engine.url.drivername}."
        )

    sync_engine = create_engine(engine.url.set(drivername="sqlite+pysqlite"), echo=False)
    try:
        Base.metadata.drop_all(bind=sync_engine)
        Base.metadata.create_all(bind=sync_engine)
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """SQLAlchemy session with a per-test transaction that is rolled back."""

    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with TestingSessionLocal(bind=connection) as session:
                yield session
            await transaction.rollback()
    finally:
        # Ensure aiosqlite background threads are stopped before pytest closes the event loop.
        await engine.dispose()
        try:
            from app.db.session import engine as app_engine

            await app_engine.dispose()
        except Exception:
            pass


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient bound to the FastAPI app with a DB override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        # Properly manage Starlette/FastAPI lifespan to avoid leaking AnyIO resources.
        async with app.router.lifespan_context(app):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                yield ac
    finally:
        app.dependency_overrides.clear()


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
