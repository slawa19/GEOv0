"""Shared stand for the programme 019 stage-1 tests (`T1902`). NOT a test module.

WHAT IT PROVIDES
* `factory` - a SERIALIZABLE engine over a mode-B clone (re-exported from the P1 stand, so the tier
  has one definition). SERIALIZABLE is load-bearing: the application runs it, and every conflict and
  every "the other transaction cannot see it yet" observation below exists only there.
* `api` - the real FastAPI app over that clone: every request gets its OWN session from `factory`,
  exactly like production gets one per request from `get_db`. A per-request hook (`with_session_hook`)
  lets a test instrument the session of ONE request - the payment it watches - and no other.
* `ApiWorld` - an equivalent, and participants registered and logged in through the real endpoints,
  with the trust lines they need created through the real endpoint.
* observers that read on a NEW session, so what they report is what another transaction can see.

WHY THE OBSERVERS USE A FRESH SESSION EACH TIME. Under SERIALIZABLE a session keeps its snapshot for
the whole transaction; an observer that reused one would report the past. Every read here opens its
own session and closes it.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterator

import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import func, insert, select, text, update

from app.api.deps import get_db, get_payment_session_factory
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.main import app
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}

#: The instrument for ONE request: called with that request's session before the handler runs.
_request_hook: contextvars.ContextVar[Callable[[Any], None] | None] = contextvars.ContextVar(
    "p019_request_hook", default=None
)


@pytest_asyncio.fixture
async def api(factory):  # noqa: F811 - the fixture above, by name
    """The real app, one `factory` session per request, as `get_db` gives one in production.

    `POST /payments` (019 stage 3) does not run on the request's session: `PaymentService.pay` opens one
    session per attempt through `get_payment_session_factory`. That factory is `factory` too, and the
    per-request hook is applied to EVERY session it opens - the payment's own sessions are the ones an
    instrument has to see.
    """

    async def override_get_db():
        async with factory() as session:
            hook = _request_hook.get()
            if hook is not None:
                hook(session)
            yield session

    @asynccontextmanager
    async def payment_session():
        async with factory() as session:
            hook = _request_hook.get()
            if hook is not None:
                hook(session)
            yield session

    previous = {
        dep: app.dependency_overrides.get(dep) for dep in (get_db, get_payment_session_factory)
    }
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_payment_session_factory] = lambda: payment_session
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            yield client
    finally:
        for dep, value in previous.items():
            if value is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = value


@contextmanager
def with_session_hook(hook: Callable[[Any], None]) -> Iterator[None]:
    """Requests STARTED inside this block get `hook(session)`; a task copies the context it is made in."""

    token = _request_hook.set(hook)
    try:
        yield
    finally:
        _request_hook.reset(token)


# ── the world ─────────────────────────────────────────────────────────────────────────────────


@dataclass
class ApiWorld:
    code: str
    equivalent_id: uuid.UUID
    alice: dict[str, Any]
    bob: dict[str, Any]
    carol: dict[str, Any]
    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    def pid_of(self, participant_id: uuid.UUID) -> str:
        for name, value in self.ids.items():
            if value == participant_id:
                return name
        return str(participant_id)


async def build_api_world(
    api: AsyncClient,
    factory,  # noqa: F811
    *,
    limit: str = "100.00",
    people: "ApiWorld | None" = None,
) -> ApiWorld:
    """Alice may pay Bob and Carol up to `limit` each (Bob and Carol trust Alice), in a NEW equivalent.

    `people`: reuse the participants (and logins) of an earlier world instead of registering new ones.
    """

    code = "P19" + uuid.uuid4().hex[:8].upper()
    resp = await api.post(
        "/api/v1/admin/equivalents",
        json={"code": code, "precision": 2, "reason": "p019 t1902"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    if people is not None:
        alice, bob, carol = people.alice, people.bob, people.carol
    else:
        alice = await register_and_login(api, "A_" + code)
        bob = await register_and_login(api, "B_" + code)
        carol = await register_and_login(api, "C_" + code)
    for truster in (bob, carol):
        key = SigningKey(base64.b64decode(truster["priv"]))
        resp = await api.post(
            "/api/v1/trustlines",
            json={
                "to": alice["pid"],
                "equivalent": code,
                "limit": limit,
                "signature": _sign_trustline_create_request(
                    signing_key=key, to_pid=alice["pid"], equivalent=code, limit=limit
                ),
            },
            headers=truster["headers"],
        )
        assert resp.status_code == 201, resp.text
    async with factory() as s:
        equivalent_id = (
            await s.execute(select(Equivalent.id).where(Equivalent.code == code))
        ).scalar_one()
        rows = (
            await s.execute(
                select(Participant.pid, Participant.id).where(
                    Participant.pid.in_([alice["pid"], bob["pid"], carol["pid"]])
                )
            )
        ).all()
    ids = {str(pid): pid_id for pid, pid_id in rows}
    PaymentRouter.invalidate_cache(code)
    return ApiWorld(code, equivalent_id, alice, bob, carol, ids)


def payment_body(world: ApiWorld, sender: dict, receiver: dict, amount: str, tx_id: str | None = None) -> dict:
    tx_id = tx_id or str(uuid.uuid4())
    key = SigningKey(base64.b64decode(sender["priv"]))
    return {
        "tx_id": tx_id,
        "to": receiver["pid"],
        "equivalent": world.code,
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=key,
            tx_id=tx_id,
            from_pid=sender["pid"],
            to_pid=receiver["pid"],
            equivalent=world.code,
            amount=amount,
        ),
    }


# ── observers (each on its own, new session) ──────────────────────────────────────────────────


async def tx_row(factory, tx_id: str) -> tuple[str, dict | None] | None:  # noqa: F811
    async with factory() as s:
        row = (
            await s.execute(
                select(Transaction.state, Transaction.error).where(Transaction.tx_id == tx_id)
            )
        ).one_or_none()
    return None if row is None else (str(row[0]), row[1])


async def debts(factory, world: ApiWorld) -> dict[tuple[str, str], Decimal]:  # noqa: F811
    async with factory() as s:
        rows = (
            await s.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == world.equivalent_id
                )
            )
        ).all()
    return {(world.pid_of(d), world.pid_of(c)): Decimal(str(a)) for d, c, a in rows}


async def envelopes(factory, tx_id: str) -> list[tuple[str, int, int]]:  # noqa: F811
    """(state, declared effect_count, journal entry rows) of every PAYMENT envelope of `tx_id`."""

    async with factory() as s:
        ops = (
            await s.execute(
                select(debt_operations.c.id, debt_operations.c.state, debt_operations.c.effect_count)
                .where(debt_operations.c.kind == "PAYMENT", debt_operations.c.identity == tx_id)
            )
        ).all()
        out = []
        for op_id, state, count in ops:
            entries = int(
                await s.scalar(
                    select(func.count())
                    .select_from(debt_journal_entries)
                    .where(debt_journal_entries.c.operation_id == op_id)
                )
            )
            out.append((str(state), int(count or 0), entries))
    return out


@dataclass(frozen=True)
class FreshObservation:
    """What a transaction whose snapshot is PROVABLY later than `marker` sees of one `tx_id`."""

    marker_seen: bool
    state: str | None


async def observe_after_marker(factory, tx_id: str) -> FreshObservation:  # noqa: F811
    """Commit a marker transaction, then check it and read the payment IN ONE snapshot.

    The marker's transaction id being visible in the observer's snapshot proves that snapshot was
    taken after the marker's commit - that is, after whatever the caller had established before
    calling (a barrier reached). Without it an "I saw nothing" could be an observer that looked too
    early.

    WHY A TRANSACTION ID AND NOT A MARKER ROW (measured 2026-09-25, anti-vacuum run): the first version
    inserted a marker row into `equivalents`. The payment reads that table, so under SERIALIZABLE the
    marker became part of a read-write cycle with the payment (payment -rw-> marker -wr-> observer
    -rw-> payment) and a single-transaction payment was refused with `40001` - the instrument changed
    the outcome it was measuring. `txid_current()` assigns an id without reading or writing any row, so
    it can be in no cycle with anything.
    """

    async with factory() as s:
        marker_xid = await s.scalar(text("SELECT txid_current()"))
        await s.commit()
    async with factory() as s:
        seen = await s.scalar(
            text("SELECT txid_visible_in_snapshot(:xid, txid_current_snapshot())"),
            {"xid": marker_xid},
        )
        state = await s.scalar(select(Transaction.state).where(Transaction.tx_id == tx_id))
        await s.rollback()
    return FreshObservation(bool(seen), None if state is None else str(state))


# ── instruments ───────────────────────────────────────────────────────────────────────────────


class CommitRecorder:
    """Wraps `commit()` of ONE request's session and records, after each REAL commit, what another
    transaction then sees of `tx_id`: its state, or None (the reservation count left with `prepare_locks`, 019 `T1909`).

    It wraps the AsyncSession instance the request handler received, so every commit made through it
    is seen - the service's and the engine's alike (`PaymentEngine` holds the same session object).
    """

    def __init__(self, factory, tx_id: str) -> None:  # noqa: F811
        self._factory = factory
        self.tx_id = tx_id
        self.commits: list[str | None] = []

    def __call__(self, session) -> None:
        original = session.commit

        async def recording_commit():
            await original()
            row = await tx_row(self._factory, self.tx_id)
            self.commits.append(None if row is None else row[0])

        session.commit = recording_commit


class EngineCommitBarrier:
    """Holds ONE payment at the entry of its MONEY phase - after the binding phase (locks, capacity),
    before any money is written. Until 019 stage 4 that was the entry of `PaymentEngine.commit`; since
    the direct execution of stage 4 it is `PaymentService._apply_payment` - on any tree, after routing,
    the `Transaction` insert and the binding checks."""

    def __init__(self, tx_id: str) -> None:
        self.tx_id = tx_id
        self.hits = 0
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    def install(self, monkeypatch) -> None:
        from app.core.payments.service import PaymentService

        original = PaymentService._apply_payment
        barrier = self

        async def commit_behind_barrier(service_self, declaration, *args, **kwargs):
            if declaration.tx_id == barrier.tx_id and barrier.hits == 0:
                barrier.hits += 1
                barrier.reached.set()
                await barrier.release.wait()
            return await original(service_self, declaration, *args, **kwargs)

        monkeypatch.setattr(PaymentService, "_apply_payment", commit_behind_barrier)


async def finish(task: asyncio.Task | None, *, timeout: float = 30) -> None:
    """Let a released task FINISH before cancelling it: cancelling a request that holds locks leaves
    its connection idle in transaction, and the clone drop then waits on it (seen in the T1544 stand)."""

    if task is None or task.done():
        return
    await asyncio.wait([task], timeout=timeout)
    if not task.done():
        task.cancel()
        await asyncio.wait([task], timeout=5)


async def set_integrity_hold(factory, equivalent_id: uuid.UUID) -> None:  # noqa: F811
    """A FAILED reconciliation result and the equivalent pointed at it - the state step 5c's reaction
    leaves behind. Written directly: the subject here is the refusal, not how a hold comes about."""

    from datetime import datetime, timezone

    from app.core.ledger.reconciliation import FAILED
    from app.db.reconciliation_tables import debt_reconciliation_results

    result_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with factory() as s:
        await s.execute(
            insert(debt_reconciliation_results).values(
                id=result_id,
                equivalent_id=equivalent_id,
                status=FAILED,
                fingerprint="f" * 64,
                detail={"stand": "p019 t1902 hold"},
                checked_at=now,
                last_checked_at=now,
                is_latest=True,
            )
        )
        await s.execute(
            update(Equivalent)
            .where(Equivalent.id == equivalent_id)
            .values(integrity_hold_result_id=result_id)
        )
        await s.commit()


async def backend_waits_on_advisory(factory, pid: int, *, timeout: float = 10.0) -> bool:  # noqa: F811
    """Backend `pid` is waiting (not granted) on an advisory lock. Observed on its own connection."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with factory() as observer:
        while True:
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND NOT granted AND pid = :pid)"
                ),
                {"pid": pid},
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)
