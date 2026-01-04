import pytest
import pytest_asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from httpx import AsyncClient
import asyncio
from app.main import app
from app.config import settings
from app.db.base import Base
from app.api.deps import get_db
from app.core.auth.crypto import generate_keypair
import uuid
from sqlalchemy import text

# --- Database Fixtures ---

# Create a separate database for tests if needed, or use the existing one with transaction rollbacks.
# For simplicity in this environment, we'll use the same DB URL but with transaction wrapping
# or a separate test DB URL if configured.
# IMPORTANT: For real production tests, use a separate TEST_DATABASE_URL.
TEST_DATABASE_URL = settings.DATABASE_URL

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_db():
    """Initialize the database (create tables) once for the session."""
    async with engine.begin() as conn:
        if engine.url.get_backend_name() in {"postgresql", "postgres"}:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Optional: cleanup after session
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Fixture that returns a SQLAlchemy session with a SAVEPOINT.
    This allows each test to run in a transaction that is rolled back at the end.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with TestingSessionLocal(bind=connection) as session:
            yield session
        await transaction.rollback()

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Fixture that returns an httpx AsyncClient with the app dependency override.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()

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
    message = f"geo:participant:create:Test User:person:{test_user_keys['public']}".encode("utf-8")
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
