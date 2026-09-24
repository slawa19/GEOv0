from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import settings


def _create_engine():
    # One engine, one dialect (programme 017, T1704): `settings.DATABASE_URL` is refused at settings
    # construction unless it is `postgresql+asyncpg` (`app/config.py::_require_postgresql_database_url`),
    # so this construction has no dialect branch and no SQLite arm to reach.
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL,
        echo=settings.DEBUG,
        future=True,
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
