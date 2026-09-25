from typing import AsyncGenerator
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

# Tests must select their permissive environment explicitly before app.config
# is imported. Override both names so a developer's legacy .env cannot conflict.
os.environ["ENV"] = "test"
os.environ["ENVIRONMENT"] = "test"

from scripts.validate_test_database_url import assert_safe_test_database_url  # noqa: E402
from tests.migrated_schema import (  # noqa: E402
    MigratedSchemaError,
    cloned_database,
    database_exists,
    ensure_tier_database,
    provision_migrated_template,
    repository_head,
    run_alembic_upgrade_head,
)

# --- Database Fixtures ---

# THE TIER RUNS ONLY ON POSTGRESQL, AND THE URL HAS NO DEFAULT HERE (017 stage 2c, T1702).
#
# Until stage 2c an unset `TEST_DATABASE_URL` meant a SQLite file under `.local-run/test-runs/`, and
# PostgreSQL was a second tier reached through the `postgres` marker. The marker is gone: every
# database test runs on PostgreSQL, so a SQLite URL - or none - is refused right here, before the
# engine below is built and before a single test is collected. A refusal, never a skip: a run that
# went green on another backend would be evidence of nothing.
#
# WHY NO DEFAULT IN THIS FILE, although `scripts/verify_local.ps1` derives one. The canonical runner
# derives `geov0_test_<TaskSlug>` from its own validated slug and sets the destructive-reset opt-in for
# THAT name only. This module is also imported by a bare `python -m pytest`, a debug path with no slug
# and no one to own the opt-in; deriving a URL here would either run without the opt-in (and refuse
# anyway, one step later and less clearly) or set it on the operator's behalf, which is exactly what
# the opt-in exists to prevent. So the debug path names its database explicitly.
#
# Since stage 3 (slice S3) no test builds a SQLite stand of its own either: the tests of the SQLite
# mechanism, `tests/scratch_db.py` and the SQLite branches of this file left with SQLite.
_POSTGRES_HOW_TO = "docs/ru/backend/postgres-local-portable.md"
_TIER_URL_EXAMPLE = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_<slug>"


def _require_a_postgres_tier_url(url: str | None) -> str:
    if not url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL is not set. The test tier runs only on PostgreSQL and has no default "
            "database: run it through `scripts/verify_local.ps1 -TaskSlug <slug>`, which derives "
            f"{_TIER_URL_EXAMPLE} from the slug, or set TEST_DATABASE_URL to such a URL together with "
            f"GEO_TEST_ALLOW_DB_RESET=1. No PostgreSQL on this machine? See {_POSTGRES_HOW_TO}."
        )
    try:
        backend = make_url(url).get_backend_name()
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same refusal
        raise pytest.UsageError("TEST_DATABASE_URL is not a valid SQLAlchemy URL.") from exc
    if backend != "postgresql":
        raise pytest.UsageError(
            f"The test tier runs only on PostgreSQL; TEST_DATABASE_URL uses {backend!r}. "
            f"Set it to {_TIER_URL_EXAMPLE} with GEO_TEST_ALLOW_DB_RESET=1, or unset it and run "
            f"`scripts/verify_local.ps1`, which derives one. No PostgreSQL on this machine? See "
            f"{_POSTGRES_HOW_TO}."
        )
    return url


TEST_DATABASE_URL = _require_a_postgres_tier_url(os.environ.get("TEST_DATABASE_URL"))

_validated_test_database_url = assert_safe_test_database_url(
    TEST_DATABASE_URL,
    allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
    repo_root=Path(__file__).resolve().parents[1],
)

# THE APPLICATION'S OWN ENGINE POINTS AT THE TIER'S DATABASE (017 stage 3, T1704). `app/config.py` has
# no `DATABASE_URL` default any more and refuses anything but `postgresql+asyncpg`, and it is read when
# `app.config` is first imported - which is why the `app` imports come only now, after the tier URL
# has been refused or accepted above: a missing or SQLite `TEST_DATABASE_URL` still ends in the
# UsageError above, not in an import error. Until T1704 the engine of `app/db/session.py` was bound to
# the SQLite default (a developer's `.local-run/geov0.db`) on a local run and to the tier's database on
# CI, which sets both variables to the same URL (`.github/workflows/quality.yml`, `required-backend`);
# now both runs are the CI one. Assigned, not `setdefault`: a developer's own `DATABASE_URL` in the
# shell or `.env` must not become the database background work of the suite writes to.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.api.deps import get_db, get_payment_session_factory  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.auth.canonical import canonical_json  # noqa: E402
from app.core.auth.crypto import generate_keypair  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import app  # noqa: E402

# Tests should not start background jobs or best-effort throttling.
settings.RATE_LIMIT_ENABLED = False
settings.RECOVERY_ENABLED = False
settings.INTEGRITY_CHECKPOINT_ENABLED = False


# RT-011-10 / runtime conformance. Wrap httpx for the WHOLE session before any test runs,
# so `test_p011_responses_conform_to_the_canon` sees every 2xx body the suite produces and
# not only the ones made after some test happened to import the harness. Installing at
# module import time is deliberate: this conftest is imported before collection, which is
# the only point that is unambiguously earlier than every request.
from tests.contract import openapi_response_conformance as _openapi_conformance  # noqa: E402

_openapi_conformance.HARNESS.install()


def pytest_collection_modifyitems(session, config, items) -> None:
    """Defer the aggregate conformance assertion to the very end of the session.

    It reads a registry that is only complete once everything else has run, and
    `tests/contract` sorts before `tests/integration` and `tests/unit`.
    """

    _openapi_conformance.move_report_test_last(items)


# `pytest_collection_finish` USED TO LIVE HERE AND IS GONE WITH THE MARKER (017 stage 2c). It failed
# the session closed when a `postgres`-marked test was SELECTED on a non-PostgreSQL URL. Its question -
# "can a test that needs PostgreSQL run without it?" - is now answered for the whole tier, earlier, by
# `_require_a_postgres_tier_url` above: nothing is collected on another backend, so a check after
# collection would be a check that can never fire. The refusal and its control are
# `tests/unit/test_the_tier_refuses_a_database_that_is_not_postgres.py`.


_use_migrated_schema = os.environ.get("GEO_TEST_USE_MIGRATED_SCHEMA") == "1"


def _test_engine_isolation_kwargs(backend_name: str) -> dict[str, str]:
    """THE POSTGRESQL TEST ENGINE RUNS AT THE APPLICATION'S ISOLATION LEVEL (T1549, 2026-09-14).

    The application engine passes `isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL` for
    PostgreSQL (`app/db/session.py`), default SERIALIZABLE. Until T1549 this engine passed nothing, so
    the whole PostgreSQL acceptance tier ran at the server default READ COMMITTED - an isolation level
    the application never runs at. Step 5a paid for it once: a false FAILED that only the application's
    configuration hid. The value is READ FROM THE SAME SETTING, never a literal, so the two cannot drift.

    Measured when it was switched on: 4 of 274 PostgreSQL tests changed. Two leaned on READ COMMITTED
    (the 5b meter inherited it; the inverse multi-segment test disabled the application's 40001 retry)
    and were fixed in the tests; one found a real non-money defect (concurrent trustline create answers
    500), kept visible as a strict xfail.

    READ COMMITTED remains available only as an explicit, named diagnostic counter-probe that asks for it
    on its own engine, connection or transaction. SQLite is unchanged: it has no such knob here.
    `tests/integration/test_p015_t1549_test_engine_runs_at_the_application_isolation_postgres.py`
    holds this in place.
    """

    if backend_name in {"postgresql", "postgres"}:
        return {"isolation_level": settings.DB_POSTGRES_ISOLATION_LEVEL}
    return {}


# NOTE: For asyncpg on Windows, pooled connections can be bound to a previous
# event loop between tests (pytest-asyncio uses per-test loops by default),
# causing errors like "Event loop is closed" and "another operation is in progress".
# Using NullPool avoids reusing loop-bound connections across tests.
engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    **_test_engine_isolation_kwargs(_validated_test_database_url.get_backend_name()),
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


async def _build_migrated_schema() -> None:
    """Build this run's schema WITH THE MIGRATIONS, and refuse if that did not happen (T1534).

    WHAT THIS REPLACED AND WHY IT WAS NOT ENOUGH TO REPAIR IT IN PLACE. Until 2026-09-13 the whole
    body of the `GEO_TEST_USE_MIGRATED_SCHEMA=1` branch was `SELECT version_num FROM alembic_version`:
    it built no schema at all - neither migrations nor `create_all` - so the database stayed whatever
    it had once been and went stale by an unknown amount while the flag reported that all was well.
    Measured that day on `geov0_test_ci`: stamped `022_debt_journal` with the tree at `024`, missing
    `chk_debt_journal_entries_delta_arithmetic`, and the PostgreSQL gate went red only because new
    tests happened to need that constraint. Without them it would have been GREEN ON A SCHEMA MISSING
    A MONEY CONSTRAINT.

    WHY IT BUILDS INSTEAD OF CHECKING, which is the whole design decision. Comparing the stamp with
    the repository head is the cheap repair and it is not sufficient here, measured rather than
    argued: `alembic_version` is not part of `Base.metadata`, so ONE ordinary flag-OFF run of this
    same conftest replaces every application table via `drop_all` + `create_all` AND LEAVES THE STAMP
    AT HEAD. Reproduced on `geov0_test_t1534` on 2026-09-13 - stamp `024_debt_journal_delta`,
    `debt_operations` carrying SQLAlchemy's `debt_operations_pkey` instead of migration 022's
    `pk_debt_operations`, 87 constraints against 90 and 82 indexes against 93, and
    `chk_equivalents_code_format` gone. A stamp comparison accepts that database. Reaching provenance
    by CHECKING needs a migrated database to compare the catalogue against, and building one costs an
    entire `alembic upgrade head`; building THIS one costs the same and leaves nothing to infer.

    THE PRICE, measured on this machine: 5.6 s once per session, against a ~175 s PostgreSQL gate.

    The tables are dropped first on purpose. `alembic upgrade head` against a database that already
    holds a `create_all` schema and a head stamp is a no-op, and a no-op would preserve exactly the
    state this exists to destroy.
    """

    head = repository_head()

    async with engine.begin() as conn:
        # DROP, including `alembic_version`, so what stands afterwards can only be the migrations'.
        # The names come from the catalogue, so nothing here is interpolated from data.
        existing = (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).scalars().all()
        for table_name in existing:
            await conn.exec_driver_sql(f'DROP TABLE IF EXISTS public."{table_name}" CASCADE')

    # Blocking, inside the loop, on purpose: nothing else in this session may proceed until the
    # schema exists, and `migrations/env.py` ends in `asyncio.run(...)` so it cannot be awaited.
    # The `alembic_version` precondition that used to be spelled here is established by
    # `migrations/env.py` inside this run (T1701): the table was just dropped with everything else,
    # and the migration entry creates it wide enough for a 46-character revision id.
    run_alembic_upgrade_head(TEST_DATABASE_URL)

    async with engine.connect() as conn:
        stamps = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalars().all()
        built = (
            await conn.execute(
                text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            )
        ).scalar_one()

    if stamps != [head]:
        raise RuntimeError(
            f"GEO_TEST_USE_MIGRATED_SCHEMA=1: the migrations were run and the database is stamped "
            f"{stamps!r} instead of the repository head [{head!r}]. Refusing rather than testing "
            f"against a schema whose provenance is unknown."
        )
    # NON-VACUITY: a run that dropped everything and then built nothing would also end stamped at
    # head if `alembic_version` alone survived, and that is indistinguishable from success by the
    # check above. `tests/integration/test_p015_t1534_the_migrated_schema_flag_is_true_postgres.py`
    # is what asserts the shape of what was built.
    if built < 2:
        raise RuntimeError(
            f"GEO_TEST_USE_MIGRATED_SCHEMA=1: `alembic upgrade head` reported success and the "
            f"`public` schema holds {built} table(s). Nothing was built."
        )


async def _ensure_schema_initialized() -> None:
    global _schema_ready
    if _schema_ready:
        return

    async with _schema_lock:
        if _schema_ready:
            return

        if _use_migrated_schema:
            await _build_migrated_schema()
            _schema_ready = True
            return

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        _schema_ready = True


def _run_in_fresh_thread(make_coroutine):
    """Run a coroutine to completion on a private event loop in a private thread.

    Not `asyncio.run` on this thread: it ends with `set_event_loop(None)`, which would take away the
    loop pytest-asyncio installed for the session. A thread of its own has its own loop state.
    """

    import threading

    outcome: dict[str, object] = {}

    def _target() -> None:
        try:
            outcome["value"] = asyncio.run(make_coroutine())
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller unchanged
            outcome["error"] = exc

    worker = threading.Thread(target=_target, name="geo-test-provisioning")
    worker.start()
    worker.join()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome.get("value")


@pytest.fixture(scope="session", autouse=True)
def init_db() -> None:
    """The PostgreSQL tier's own database exists before the first test runs (T1702).

    Schema initialization is still performed lazily in the first `db_session`. What happens here is
    the step before it, which until 2026-09-23 nothing did: on a server where `geov0_test_<slug>` did
    not exist the tier produced 705 errors with one cause, `InvalidCatalogNameError`, and CI never saw
    it because its service container creates the database itself. `ensure_tier_database` creates
    only a name the guard would also let this tier reset, and refuses without `CREATEDB`.

    A REFUSAL ENDS THE SESSION rather than erroring every test: one clear reason instead of the same
    traceback two thousand times, and a non-zero exit either way - never a skip.
    """

    if _validated_test_database_url.get_backend_name() != "postgresql":
        return None
    try:
        created = _run_in_fresh_thread(lambda: ensure_tier_database(TEST_DATABASE_URL))
    except MigratedSchemaError as exc:
        pytest.exit(
            f"the PostgreSQL tier cannot provide its own database: {exc} "
            f"(no PostgreSQL on this machine? See {_POSTGRES_HOW_TO})",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    if created:
        print(
            f"\n[tests/conftest.py] created the tier database "
            f"{_validated_test_database_url.database!r}"
        )
    return None


# =====================================================================================================
# MODE B - a disposable database with real root commits (programme 017, T1702)
# =====================================================================================================
#
# Mode A is `db_session` below: one connection, one outer transaction, SAVEPOINTs, rolled back. On
# PostgreSQL three things cannot happen inside it, and the stage-2 inventory
# (`specs/017-postgres-only-engine/t1706-inventory.md`, section 1) names them: clearing refuses a
# connection-bound session (`app/core/clearing/service.py:1530-1539`); another session cannot see
# what was never committed; and a second writer contending for a lock waits on the first test
# connection forever. Mode B is the answer to all three: a database CLONED from a template the
# migrations built, sessions bound to an ENGINE over it, commits that are real, and the whole
# database dropped when the test ends - so nothing leaks into the next test either.
#
# One clone name, reused: tests run one at a time, `cloned_database` drops the name before every
# copy, so a crashed run leaves at most one clone behind and the next test reclaims it.
#
# The template is built ONCE per session on first use and rebuilt only if it has gone. It can go:
# `provision_migrated_template` with its default sweep (the T1701 and T1711 modules) drops every
# `<tier>__*` database, this one included. Building it here WITHOUT the sweep is what keeps this
# fixture from dropping those modules' cached templates in turn.

_MODE_B_TEMPLATE_SUFFIX = "modebtpl"
_MODE_B_CLONE_SUFFIX = "modeb"
_mode_b_template_name: str | None = None

#: MEASUREMENT INSTRUMENT, NOT A TIER SETTING. `GEO_TEST_FIXTURE_MODE=B` makes `db_session` (and
#: with it `client`) hand out a mode-B session instead of the savepoint one, for EVERY test of the
#: run. It exists so the stage-2 catalogue can measure, per test, whether mode B actually repairs
#: what fails in mode A - the inventory predicted it by reading and no one had run it. It selects
#: and deselects nothing. Unset (the default, and the only value any gate uses) means mode A.
_FIXTURE_MODE = os.environ.get("GEO_TEST_FIXTURE_MODE", "A").strip().upper() or "A"
if _FIXTURE_MODE not in {"A", "B"}:
    raise RuntimeError(
        f"GEO_TEST_FIXTURE_MODE must be A or B, got {_FIXTURE_MODE!r}. Unset it for the ordinary tier."
    )


class CommittedDatabase:
    """What a mode-B test gets: the clone's URL, an engine over it, and a sessionmaker bound to it.

    The engine runs at the application's isolation level, read from the same setting as the tier
    engine (`_test_engine_isolation_kwargs`, T1549), so mode B does not quietly become READ COMMITTED.
    """

    def __init__(self, url: str, engine_, sessionmaker_) -> None:
        self.url = url
        self.engine = engine_
        self.sessionmaker = sessionmaker_


async def _mode_b_template() -> str:
    global _mode_b_template_name
    if _mode_b_template_name is not None and await database_exists(
        TEST_DATABASE_URL, _mode_b_template_name
    ):
        return _mode_b_template_name
    _, _mode_b_template_name = await provision_migrated_template(
        TEST_DATABASE_URL, suffix=_MODE_B_TEMPLATE_SUFFIX, sweep_stale=False
    )
    return _mode_b_template_name


def _mode_b_engine(clone_url: str):
    """An engine over a mode-B clone. A clone is made by `CREATE DATABASE ... TEMPLATE`, so it is
    PostgreSQL by construction; the caller has already refused any other backend.

    Until 017 stage 3 (slice S7) this returned `None` for a SQLite URL: that early return was the
    shape the deleted T1525 engine guard read as "this construction cannot be SQLite".
    """

    return create_async_engine(
        clone_url,
        echo=False,
        poolclass=NullPool,
        **_test_engine_isolation_kwargs("postgresql"),
    )


@asynccontextmanager
async def _committed_database_context():
    if _validated_test_database_url.get_backend_name() != "postgresql":
        # A refusal, not a skip: a mode-B test that ran on another backend would measure nothing.
        raise RuntimeError(
            "mode B (a disposable database with real commits) exists only on PostgreSQL; "
            f"TEST_DATABASE_URL uses {_validated_test_database_url.get_backend_name()!r}."
        )
    template_name = await _mode_b_template()
    async with cloned_database(
        TEST_DATABASE_URL, template_name=template_name, suffix=_MODE_B_CLONE_SUFFIX
    ) as clone_url:
        clone_engine = _mode_b_engine(clone_url)
        clone_sessionmaker = async_sessionmaker(
            bind=clone_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            yield CommittedDatabase(clone_url, clone_engine, clone_sessionmaker)
        finally:
            # Before the drop: `cloned_database` terminates stragglers, but a pool that is still
            # checked out would make the drop race it.
            await clone_engine.dispose()


@pytest_asyncio.fixture
async def committed_database() -> AsyncGenerator[CommittedDatabase, None]:
    """Mode B: a fresh clone of the migrated template, dropped when the test ends however it ends."""

    async with _committed_database_context() as database:
        yield database


@pytest_asyncio.fixture
async def committed_session(
    committed_database: CommittedDatabase,
) -> AsyncGenerator[AsyncSession, None]:
    """Mode B's drop-in for `db_session`: an engine-bound session whose `commit()` is a real commit."""

    async with committed_database.sessionmaker() as session:
        session.info["geo_committed_database"] = committed_database
        yield session


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


@asynccontextmanager
async def _session_for_one_test(*, mode_b: bool):
    """The body of `db_session`: one test's session, in mode A or mode B.

    `mode_b=True` is a mode-B session on a clone dropped after the test. The tier's own schema is
    built either way, because a test that imports `TestingSessionLocal` directly keeps reaching the
    tier's database.
    """

    await _ensure_schema_initialized()

    # Simulator runtime can spawn background tasks (heartbeat / real-mode tick) that keep DB
    # connections open; stop any run a previous test left behind before this test starts.
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
        finally:
            app_db_session.AsyncSessionLocal = _orig_async_session_local
    except Exception:
        pass

    if mode_b:
        async with _committed_database_context() as database:
            async with database.sessionmaker() as session:
                session.info["geo_committed_database"] = database
                yield session
        return

    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with TestingSessionLocal(bind=connection) as session:
            # `TestingSessionLocal` joins this outer transaction with
            # join_transaction_mode="create_savepoint": the session's own commit and rollback become
            # SAVEPOINT release and rollback, and the outer transaction is never touched, so the
            # rollback below undoes the whole test. That is SQLAlchemy 2.0's built-in form of
            # "join an external transaction", and it needs nothing else.
            #
            # This fixture used to add the SQLAlchemy 1.4 recipe on top of it - an explicit
            # begin_nested() plus an after_transaction_end listener that reopened a SAVEPOINT
            # whenever one ended. Under create_savepoint that listener is redundant and actively
            # wrong: the payment engine opens its own begin_nested() (engine.py _run_uow_with_retry,
            # _apply_flow), the listener fired as that savepoint closed and started another one
            # underneath it, and the application then failed with "Can't operate on closed
            # transaction inside context manager". Measured 2026-09-23 on PostgreSQL (017 stage 2a,
            # class FIXA, 62 tests in 24 files); SQLite never ran this branch.
            yield session
        await transaction.rollback()


#: MODE B FOR ONE TEST OF THE DEFAULT TIER (017 stage 2b, T1702). Put `@MODE_B` on a test, or
#: `pytestmark = MODE_B` on a module, and `db_session` - and with it `client`, which requests it - is a
#: mode-B session for that test: a clone of the migrated template, dropped after the test.
#:
#: WHY A PARAMETRIZATION AND NOT A MARKER: markers are registered in `pytest.ini` and select or
#: deselect tests; this must do neither. An indirect parameter is how pytest hands one fixture a
#: per-test value, and the `[mode_b]` it adds to the node id says in every report which mode ran.
#: It selects and deselects nothing - a mode-B test is collected exactly as before.
#:
#: A test that needs a SECOND session reaches the same database through `sessionmaker_of(session)`,
#: never through `TestingSessionLocal` directly: on PostgreSQL that is the tier's database, where the
#: clone's commits are not.
MODE_B = pytest.mark.parametrize("db_session", ["B"], indirect=True, ids=["mode_b"])


@pytest_asyncio.fixture
async def db_session(request) -> AsyncGenerator[AsyncSession, None]:
    """SQLAlchemy session with a per-test transaction that is rolled back (mode A).

    A test carrying `MODE_B` gets a mode-B session instead (see `MODE_B`), and so does every test
    under the measurement instrument `GEO_TEST_FIXTURE_MODE=B` (see above).
    """

    requested = getattr(request, "param", "A")
    if requested not in {"A", "B"}:
        raise RuntimeError(f"db_session takes the mode A or B, got {requested!r}")
    async with _session_for_one_test(mode_b=requested == "B" or _FIXTURE_MODE == "B") as session:
        yield session


def sessionmaker_of(session: AsyncSession):
    """The sessionmaker over the database `session` talks to: the mode-B clone's, else the tier's."""

    committed = session.info.get("geo_committed_database")
    return committed.sessionmaker if committed is not None else TestingSessionLocal


async def _end_the_transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await session.commit()
    except Exception:
        await session.rollback()


def _mode_a_has_no_payment_sessions():
    raise RuntimeError(
        "POST /payments opens its own sessions, one per attempt (programme 019 stage 3, FORK-11): "
        "an HTTP payment test must run in mode B (`MODE_B`, tests/conftest.py)"
    )


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient bound to the FastAPI app with a DB override."""

    # IMPORTANT: simulator runtime uses app.db.session.AsyncSessionLocal directly
    # (e.g. in the real-mode heartbeat loop). Patch it to point at the test
    # sessionmaker so background tasks operate on the same DB as request handlers.
    import app.db.session as app_db_session
    _orig_async_session_local = app_db_session.AsyncSessionLocal
    # In mode B the background work has to reach the CLONE the request handlers use, not the tier.
    committed = db_session.info.get("geo_committed_database")
    app_db_session.AsyncSessionLocal = (
        committed.sessionmaker if committed is not None else TestingSessionLocal
    )

    async def override_get_db():
        # MODE B: EACH REQUEST ON ITS OWN TRANSACTION of the test's session (019 stage 3). Since
        # `POST /payments` commits on sessions of its own, a request that ran inside a transaction the
        # test opened earlier would read a SERIALIZABLE snapshot older than the payment it follows, and
        # a test that read after it through `db_session` would too. Ending the session's transaction
        # before and after every request gives both a fresh snapshot - as production's one session
        # per request has - and makes what the test staged visible to the payment's own sessions.
        # `commit()`, not `rollback()`: the session is `expire_on_commit=False`, so the test's loaded
        # objects stay readable (a rollback would expire them and a later attribute read would do IO).
        if committed is not None:
            await _end_the_transaction(db_session)
        try:
            yield db_session
        finally:
            if committed is not None:
                await _end_the_transaction(db_session)

    app.dependency_overrides[get_db] = override_get_db
    # 019 stage 3 (`FORK-11`): `POST /payments` runs as ONE transaction on sessions of its own - one
    # per attempt of `PaymentService.pay` - never on the request's session. In mode B every attempt
    # opens a session on the clone the test seeded and commits there for real. In mode A there is no
    # such session: the seed is uncommitted inside the fixture's rolled-back transaction, a new
    # session cannot see it, and a payment's commit would escape the rollback. Refusing loudly names
    # the fix instead of failing as "Sender not found".
    app.dependency_overrides[get_payment_session_factory] = (
        (lambda: committed.sessionmaker)
        if committed is not None
        else (lambda: _mode_a_has_no_payment_sessions)
    )
    try:
        # Intentionally do NOT run FastAPI lifespan in tests: it starts background jobs
        # (recovery/integrity) that can interfere with DB isolation.
        async with AsyncClient(app=app, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        app_db_session.AsyncSessionLocal = _orig_async_session_local


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
