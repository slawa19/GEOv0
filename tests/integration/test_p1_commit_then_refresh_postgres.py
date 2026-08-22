"""RT-009-5: a failure after a durable commit must not be reported as a failed operation.

Program 009, finding `F-009-6` (`PAR-A1a-001`, P2).

`TrustLineService.create` commits (`app/core/trustlines/service.py:250`) and then refreshes
(`:261`).  The commit is durable; the refresh is a fresh SELECT.  If that SELECT fails, the
caller is told the operation failed although it *already happened*, and the retry is not
idempotent -- the trustline exists, so the second attempt gets a conflict.  `main.py`
registers handlers only for `GeoException` and `RequestValidationError`, so the failure
leaves the API as HTTP 500 on a mutation that succeeded.

WHY THIS IS A REAL REPRODUCER AND NOT A SYNTHESISED ONE.  The spec forbids proving this
with a monkeypatched `refresh` («Запрещённые способы доказательства»): patching would show
that the `except` branch exists, not that the failure is reachable after a durable commit.
Here nothing about the failure is fabricated -- a second, independent connection really
terminates the backend that served the request, exactly as a connection loss would, and the
refresh then issues real SQL down a channel that is genuinely gone.  Only the *timing* is
made deterministic, by an `after_commit` listener attached to this one session instance.

WHY THE STANDARD FIXTURES CANNOT HOST IT.  On PostgreSQL `tests/conftest.py:222-235` wraps
each test in an outer transaction and turns the application's `commit()` into a SAVEPOINT,
so no commit is durable inside it -- durability is the whole subject here.  This test
therefore owns its engine and sessions, in the same way
`tests/integration/test_clearing_payment_prepare_interlock_postgres.py` does.  The engine is
pinned to a single connection so the refresh cannot silently be served by a healthy one.

An earlier attempt at this stand was abandoned because it tried to hang the listener on the
global `Session` class; the fixture already shows the right form -- listen on
`session.sync_session`, one instance.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio
from nacl.signing import SigningKey
from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.trustlines.service import TrustLineService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCreateRequest
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from sqlalchemy.exc import DBAPIError

from app.utils.exceptions import ConflictException, GeoException

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if "postgresql" not in url:
        pytest.skip("durability of the commit is the subject; SQLite cannot show it")
    return url


@pytest_asyncio.fixture
async def durable_engine():
    # One connection and no overflow: the refresh must reuse the connection whose backend
    # was terminated, instead of being quietly served by a fresh healthy one.
    engine = create_async_engine(_url(), pool_size=1, max_overflow=0, pool_pre_ping=False)
    try:
        yield engine
    finally:
        await engine.dispose()


def _sign(private_key_b64: str, payload: dict) -> str:
    signing_key = SigningKey(base64.b64decode(private_key_b64))
    return base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")


async def _seed(sessionmaker) -> tuple[str, uuid.UUID, str, str]:
    """Create an equivalent and two participants, durably. Returns the request material."""

    nonce = uuid.uuid4().hex[:10]
    eq_code = ("R" + nonce[:9]).upper()
    sender_pub, sender_priv = generate_keypair()
    sender_id = uuid.uuid4()
    to_pid = "to-" + nonce

    async with sessionmaker() as s:
        s.add_all(
            [
                Equivalent(
                    code=eq_code,
                    symbol="R",
                    description=None,
                    precision=2,
                    metadata_={},
                    is_active=True,
                ),
                Participant(
                    id=sender_id,
                    pid="from-" + nonce,
                    display_name="Sender",
                    public_key=sender_pub,
                    type="person",
                    status="active",
                    profile={},
                ),
                Participant(
                    id=uuid.uuid4(),
                    pid=to_pid,
                    display_name="Receiver",
                    public_key="pk-" + nonce,
                    type="person",
                    status="active",
                    profile={},
                ),
            ]
        )
        await s.commit()

    return eq_code, sender_id, sender_priv, to_pid


def _terminate_backend_from_another_connection(url: str, pid: int) -> None:
    """Kill `pid` from a genuinely separate connection, synchronously.

    Called from a SQLAlchemy event listener, which is sync code inside a running event
    loop: the work therefore runs on its own thread with its own loop, and the listener
    blocks until the backend is really gone.  That is what makes the timing deterministic
    without faking the failure.
    """

    async def _kill() -> None:
        import asyncpg

        dsn = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("SELECT pg_terminate_backend($1)", pid)
        finally:
            await conn.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_kill())).result(timeout=30)


@pytest.mark.asyncio
async def test_connection_loss_after_commit_is_not_reported_as_a_failed_mutation(
    durable_engine,
) -> None:
    url = _url()
    sessionmaker = async_sessionmaker(durable_engine, expire_on_commit=False)
    eq_code, sender_id, sender_priv, to_pid = await _seed(sessionmaker)

    payload = {"to": to_pid, "equivalent": eq_code, "limit": "100"}
    request = TrustLineCreateRequest(
        to=to_pid,
        equivalent=eq_code,
        limit="100",
        signature=_sign(sender_priv, payload),
    )

    armed = {"done": False}

    async with sessionmaker() as session:
        pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())

        @event.listens_for(session.sync_session, "after_commit")
        def _drop_connection_after_commit(sess) -> None:  # noqa: ANN001
            # Once only: the rollback path commits too, and killing twice proves nothing.
            if armed["done"]:
                return
            armed["done"] = True
            _terminate_backend_from_another_connection(url, pid)

        with pytest.raises(Exception) as excinfo:
            await TrustLineService(session).create(sender_id, request)

    assert armed["done"], (
        "the connection was never dropped, so this run did not exercise the subject"
    )

    raised = excinfo.value
    assert not isinstance(raised, GeoException), (
        "the failure must be the raw driver error, not a declared outcome -- that is "
        f"exactly why the API answers 500: {type(raised).__name__}: {raised}"
    )
    # Name the failure precisely, so this cannot pass on some unrelated exception:
    # SQLAlchemy's InterfaceError wrapping asyncpg's "connection is closed"...
    assert isinstance(raised, DBAPIError), f"{type(raised).__name__}: {raised}"
    assert "connection is closed" in str(raised), str(raised)
    # ...and it must be the REFRESH that failed, not the commit or anything before it.
    statement = str(getattr(raised, "statement", "") or "")
    assert statement.lstrip().upper().startswith("SELECT"), statement
    assert "trust_lines" in statement, statement

    # Fact 1: the mutation is durable despite the reported failure.  A brand-new session on
    # a brand-new connection is the only honest observer here.
    async with sessionmaker() as observer:
        rows = (
            await observer.execute(
                select(TrustLine)
                .join(Equivalent, Equivalent.id == TrustLine.equivalent_id)
                .where(Equivalent.code == eq_code)
            )
        ).scalars().all()

    assert len(rows) == 1, (
        "the caller was told the operation failed; if the row is absent the finding is "
        f"refuted, if it is present the report was false. rows={rows}"
    )
    assert str(rows[0].status) == "active"

    # Fact 2: the retry is not idempotent -- the user who is told "it failed" and tries
    # again is told the line already exists.
    async with sessionmaker() as retry_session:
        with pytest.raises(ConflictException):
            await TrustLineService(retry_session).create(sender_id, request)

    # This test commits for real, outside the transaction the shared fixture would roll
    # back, so it has to clear up after itself rather than leave rows in a shared database.
    async with sessionmaker() as cleanup:
        eq_id = (
            await cleanup.execute(select(Equivalent.id).where(Equivalent.code == eq_code))
        ).scalar_one_or_none()
        if eq_id is not None:
            await cleanup.execute(delete(TrustLine).where(TrustLine.equivalent_id == eq_id))
            await cleanup.execute(delete(Equivalent).where(Equivalent.id == eq_id))
        await cleanup.execute(
            delete(Participant).where(Participant.pid.in_([to_pid, "from-" + to_pid[3:]]))
        )
        await cleanup.execute(delete(Participant).where(Participant.id == sender_id))
        await cleanup.commit()
