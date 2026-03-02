from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import event
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
        engine = create_async_engine(
            url,
            poolclass=NullPool,
            # timeout=10: SQLite write-lock wait time before raising "database is locked".
            # Default is 5s which is too short for simulator clearing (two concurrent
            # sessions: parent tick + clearing).
            connect_args={"timeout": 30},
            **common_kwargs,
        )

        sqlite_db = make_url(url).database
        enable_wal = sqlite_db not in {None, "", ":memory:"}

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_set_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                if enable_wal:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

        return engine

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