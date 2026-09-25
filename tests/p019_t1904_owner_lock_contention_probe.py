"""019 `T1904`: owner-lock hold and wait time, deadline exhaustion and progress under payment / clearing /
admin contention - a re-runnable EXPERIMENT (spec, Verification plan §2, "Стадия 3"), not a gate.

NOT PART OF THE TIER. The file name does not match `python_files = test_*.py`; pytest collects it only
when it is named explicitly:

    .\\scripts\\verify_local.ps1 -TaskSlug p019s3 -BackendOnly -BackendSelector tests/p019_t1904_owner_lock_contention_probe.py

It runs unchanged on the tree before stage 3 (three commits per API payment, the owner lock released
with the durable `PREPARED` and taken again for the commit) and after it (one transaction holding the
owner lock from `prepare` to the one commit), so the two JSON artifacts compare the same load.

THE LOAD, in one equivalent of one mode-B clone at the application's isolation (SERIALIZABLE):
* `PAYERS` participants, registered and logged in through the real endpoints, each sending `PAYMENTS`
  payments of 1.00 to one receiver through the real `POST /payments`, all payers concurrently;
* `CYCLES` clearing runs, one after another, each on its own seeded three-party debt cycle in the same
  equivalent (`ClearingService.execute_clearing_with_amount` on an engine-bound session: the pinned
  connection's session-level owner lock);
* an admin loop calling `POST /admin/equivalents/{code}/integrity-hold/clear` every `ADMIN_PERIOD_S`
  while payments run: it takes the owner lock, finds no hold and answers 409 - a real owner-lock
  contender that changes nothing.

THE INSTRUMENT. `MoneyBoundary._acquire_equivalent_owner_locks`, `acquire_staged_equivalent_owner_locks`
and `acquire_session_equivalent_owner_lock` are wrapped: the call's start is the request, its return
the grant. A transaction-level lock is released when its transaction ends, observed by the sync
session's `after_commit` / `after_rollback` events; the clearing's session-level lock at
`release_session_equivalent_owner_lock`. A re-entrant acquisition inside a transaction that already
holds the lock is not a new hold and is not counted. Each hold is attributed to the driver that
caused it (payment / clearing / admin) through a context variable.

WHAT IT DOES NOT SEE: waits on row locks (only the owner lock is timed), time spent before the first
owner-lock request (routing), and anything under a different load. A finite run shows how this load
behaved, not that no other load starves.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import os
import statistics
import time
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import app.api.deps as deps
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.main import app
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

PAYERS = int(os.environ.get("P019_T1904_PAYERS", "8"))
PAYMENTS = int(os.environ.get("P019_T1904_PAYMENTS", "6"))
CYCLES = int(os.environ.get("P019_T1904_CYCLES", "4"))
ADMIN_PERIOD_S = float(os.environ.get("P019_T1904_ADMIN_PERIOD_S", "0.05"))
ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}

_driver: contextvars.ContextVar[str] = contextvars.ContextVar("p019_t1904_driver", default="other")


class _Recorder:
    def __init__(self) -> None:
        self.open: dict[int, dict[str, Any]] = {}
        self.holds: list[dict[str, Any]] = []

    def granted(self, sync_session: Session, kind: str, requested: float, granted: float) -> None:
        key = id(sync_session)
        if key in self.open:
            return  # re-entrant inside the same transaction
        self.open[key] = {"kind": kind, "wait": granted - requested, "granted": granted}

    def released(self, sync_session: Session) -> None:
        record = self.open.pop(id(sync_session), None)
        if record is None:
            return
        record["hold"] = time.perf_counter() - record.pop("granted")
        self.holds.append(record)


def _install(monkeypatch, recorder: _Recorder) -> None:
    for name in ("_acquire_equivalent_owner_locks", "acquire_staged_equivalent_owner_locks"):
        original = getattr(MoneyBoundary, name)

        def make(original):
            async def timed(self, *args, **kwargs):
                started = time.perf_counter()
                result = await original(self, *args, **kwargs)
                recorder.granted(
                    self.session.sync_session, _driver.get(), started, time.perf_counter()
                )
                return result

            return timed

        monkeypatch.setattr(MoneyBoundary, name, make(original))

    original_session_lock = MoneyBoundary.acquire_session_equivalent_owner_lock
    original_session_release = MoneyBoundary.release_session_equivalent_owner_lock
    session_holds: dict[int, dict[str, Any]] = {}

    # Keyed by the equivalent: the clearing releases through a NEW `MoneyBoundary` over its work session.
    async def session_lock(self, equivalent_id, *args, **kwargs):
        started = time.perf_counter()
        result = await original_session_lock(self, equivalent_id, *args, **kwargs)
        session_holds[equivalent_id] = {
            "kind": _driver.get() + "-session",
            "wait": time.perf_counter() - started,
            "granted": time.perf_counter(),
        }
        return result

    async def session_release(self, equivalent_id, *args, **kwargs):
        try:
            return await original_session_release(self, equivalent_id, *args, **kwargs)
        finally:
            record = session_holds.pop(equivalent_id, None)
            if record is not None:
                record["hold"] = time.perf_counter() - record.pop("granted")
                recorder.holds.append(record)

    monkeypatch.setattr(MoneyBoundary, "acquire_session_equivalent_owner_lock", session_lock)
    monkeypatch.setattr(MoneyBoundary, "release_session_equivalent_owner_lock", session_release)

    def end(sync_session, *args) -> None:
        recorder.released(sync_session)

    event.listen(Session, "after_commit", end)
    event.listen(Session, "after_rollback", end)
    recorder.listener = end


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
        "sum_ms": round(sum(ordered) * 1000, 1),
    }


@pytest.mark.asyncio
async def test_t1904_owner_lock_contention(committed_database) -> None:
    engine = create_async_engine(
        committed_database.url,
        pool_size=40,
        max_overflow=10,
        isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[deps.get_db] = override_get_db
    has_payment_sessions = hasattr(deps, "get_payment_session_factory")
    if has_payment_sessions:
        app.dependency_overrides[deps.get_payment_session_factory] = lambda: factory
    recorder = _Recorder()
    mp = pytest.MonkeyPatch()
    try:
        async with AsyncClient(app=app, base_url="http://test") as api:
            code = "T4" + uuid.uuid4().hex[:8].upper()
            resp = await api.post(
                "/api/v1/admin/equivalents",
                json={"code": code, "precision": 2, "reason": "p019 t1904 probe"},
                headers=ADMIN,
            )
            assert resp.status_code == 200, resp.text
            receiver = await register_and_login(api, "R_" + code)
            payers = [await register_and_login(api, f"P{i}_" + code) for i in range(PAYERS)]
            rkey = SigningKey(base64.b64decode(receiver["priv"]))
            for payer in payers:
                resp = await api.post(
                    "/api/v1/trustlines",
                    json={
                        "to": payer["pid"],
                        "equivalent": code,
                        "limit": "1000.00",
                        "signature": _sign_trustline_create_request(
                            signing_key=rkey, to_pid=payer["pid"], equivalent=code, limit="1000.00"
                        ),
                    },
                    headers=receiver["headers"],
                )
                assert resp.status_code == 201, resp.text

            # Clearing cycles: three seeded participants each, debts and trust lines direct.
            async with factory() as s:
                equivalent_id = (
                    await s.execute(select(Equivalent.id).where(Equivalent.code == code))
                ).scalar_one()
            cycles: list[list[str]] = []
            for n in range(CYCLES):
                async with factory() as s:
                    people = [
                        Participant(
                            pid=f"T4C{n}{r}_{code}",
                            display_name=r,
                            public_key=f"pk_t4_{n}_{r}_{code}",
                            type="person",
                            status="active",
                            profile={},
                        )
                        for r in "abc"
                    ]
                    s.add_all(people)
                    await s.flush()
                    a, b, c = people
                    for creditor, debtor in ((b, a), (c, b), (a, c)):
                        s.add(
                            TrustLine(
                                from_participant_id=creditor.id,
                                to_participant_id=debtor.id,
                                equivalent_id=equivalent_id,
                                limit=Decimal("100.00"),
                                status="active",
                            )
                        )
                    debts = [
                        Debt(
                            id=uuid.uuid4(),
                            debtor_id=d.id,
                            creditor_id=cr.id,
                            equivalent_id=equivalent_id,
                            amount=Decimal("10.00"),
                            version=0,
                        )
                        for d, cr in ((a, b), (b, c), (c, a))
                    ]
                    async with debt_fixture_setup(s, label=f"t1904-cycle-{n}"):
                        s.add_all(debts)
                    await s.commit()
                cycles.append([str(d.id) for d in debts])
            PaymentRouter.invalidate_cache(code)

            _install(mp, recorder)
            outcomes: Counter = Counter()
            clearing_outcomes: Counter = Counter()
            admin_outcomes: Counter = Counter()
            done = asyncio.Event()

            async def pay_all(payer: dict) -> None:
                _driver.set("payment")
                key = SigningKey(base64.b64decode(payer["priv"]))
                for _ in range(PAYMENTS):
                    tx_id = str(uuid.uuid4())
                    body = {
                        "tx_id": tx_id,
                        "to": receiver["pid"],
                        "equivalent": code,
                        "amount": "1.00",
                        "signature": _sign_payment_request(
                            signing_key=key, tx_id=tx_id, from_pid=payer["pid"],
                            to_pid=receiver["pid"], equivalent=code, amount="1.00",
                        ),
                    }
                    r = await api.post("/api/v1/payments", json=body, headers=payer["headers"])
                    if r.status_code == 200:
                        outcomes[r.json()["status"]] += 1
                    else:
                        outcomes[f"{r.status_code}/{r.json()['error']['code']}"] += 1

            async def clear_all() -> None:
                _driver.set("clearing")
                for ids in cycles:
                    try:
                        async with factory() as s:
                            cleared = await ClearingService(s).execute_clearing_with_amount(
                                [{"debt_id": i} for i in ids]
                            )
                        clearing_outcomes[f"cleared:{cleared}"] += 1
                    except Exception as exc:  # noqa: BLE001 - an outcome of the experiment
                        clearing_outcomes[type(exc).__name__] += 1

            async def admin_loop() -> None:
                _driver.set("admin")
                while not done.is_set():
                    r = await api.post(
                        f"/api/v1/admin/equivalents/{code}/integrity-hold/clear",
                        json={"reason": "p019 t1904 probe"},
                        headers=ADMIN,
                    )
                    admin_outcomes[r.status_code] += 1
                    await asyncio.sleep(ADMIN_PERIOD_S)

            started = time.perf_counter()
            admin = asyncio.create_task(admin_loop())
            await asyncio.gather(*(pay_all(p) for p in payers), clear_all())
            elapsed = time.perf_counter() - started
            done.set()
            await admin
    finally:
        listener = getattr(recorder, "listener", None)
        if listener is not None:
            event.remove(Session, "after_commit", listener)
            event.remove(Session, "after_rollback", listener)
        mp.undo()
        app.dependency_overrides.pop(deps.get_db, None)
        if has_payment_sessions:
            app.dependency_overrides.pop(deps.get_payment_session_factory, None)
        await engine.dispose()

    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({h["kind"] for h in recorder.holds}):
        holds = [h for h in recorder.holds if h["kind"] == kind]
        by_kind[kind] = {
            "wait": _stats([h["wait"] for h in holds]),
            "hold": _stats([h["hold"] for h in holds]),
        }
    committed = outcomes.get("COMMITTED", 0)
    summary = {
        "tree": "stage3" if has_payment_sessions else "before-stage3",
        "load": {"payers": PAYERS, "payments_each": PAYMENTS, "cycles": CYCLES, "admin_period_s": ADMIN_PERIOD_S},
        "elapsed_s": round(elapsed, 3),
        "payments": dict(outcomes),
        "payments_committed_per_s": round(committed / elapsed, 2),
        "clearing": dict(clearing_outcomes),
        "admin": {str(k): v for k, v in admin_outcomes.items()},
        "owner_lock": by_kind,
    }
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".")
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"p019_t1904_contention_{summary['tree']}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("P019-T1904-CONTENTION " + json.dumps(summary))
    # Non-vacuity only: the instrument saw holds of the payment driver, and every payment ended.
    assert by_kind.get("payment", {}).get("hold", {}).get("n", 0) > 0, summary
    assert sum(outcomes.values()) == PAYERS * PAYMENTS, summary
