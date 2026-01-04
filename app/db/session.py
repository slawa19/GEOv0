from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.engine.url import make_url

from app.config import settings


def _create_engine():
    url = settings.DATABASE_URL
    common_kwargs = {
        "echo": settings.DEBUG,
        "future": True,
    }

    # SQLite (especially aiosqlite) is not well-served by connection pooling.
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            poolclass=NullPool,
            **common_kwargs,
        )

    # Postgres/MySQL/etc: use pool settings to improve stability under load.
    backend = make_url(url).get_backend_name()
    db_kwargs = {}
    if backend in {"postgresql", "postgres"}:
        db_kwargs["isolation_level"] = settings.DB_POSTGRES_ISOLATION_LEVEL

    return create_async_engine(
        url,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        **db_kwargs,
        **common_kwargs,
    )


engine = _create_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session