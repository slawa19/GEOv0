"""How many pool connections one `POST /payments` holds, and what N concurrent payments do to the pool.

WHY THIS EXISTS IN THE TREE (035 A8, F-035-8). The finding is a hypothesis read from the code: the request's
`get_db` session authenticates the caller with a SELECT and then stays in its transaction for the whole request,
while `PaymentService.pay` opens a session of its own per attempt - so a payment would hold two connections, one of
them `idle in transaction`, against a pool of `DB_POOL_SIZE` + `DB_MAX_OVERFLOW`. The spec asks for a measurement
before any decision. A measurement nobody can re-run is an assertion, so the generator lives here (the precedent
is `scripts/measure_clearing_min_amount_plan.py`). It changes no product code and decides nothing.

WHAT IT READ, AND WHAT CAME OF IT. On `7e21abc1` (before 035 A9): two connections per payment, both
`idle in transaction`; at N = 15 against a pool of 15 no payment arrived at binding and none committed until the
pool timeout. 035 A9 closes the request session before the payment (`app/api/v1/payments.py::create_payment`);
on that code the same run reads one connection per payment and N = 15 commits. The text above describes the
hypothesis as it stood; the script is unchanged in what it measures.

WHAT IT MEASURES. The application in-process (`app.main.app` through `httpx.ASGITransport`) on its REAL wiring:
the real `get_db`, the real `app.db.session.engine` with the pool the settings give it, the real
`get_payment_session_factory`. No dependency is overridden. For each N it sends N payments at once, each from its
own payer to its own payee over its own trust line (so no two share a debt row, a line or a Redis lock key), holds
every one of them at the same point - ARRIVAL AT `PaymentService._bind_payment`, which is after routing and with
the attempt's session open (not "entry into `pay()`": a request waiting for its attempt's connection is already
inside `pay()`) - and reads, while they are held:

* the pool's own counters (`checkedout`, `overflow`);
* `pg_stat_activity` for this database through a connection OUTSIDE the pool: sessions by `state`, and the last
  statement of the `idle in transaction` ones (which shows whose they are);
* where each of the N requests is: `at_binding` (arrived at the gate), `already_answered` (finished before the
  reading - a refusal, a pool timeout shorter than `--settle`), and `before_binding` (neither: still
  authenticating, routing, or waiting for a connection). `before_binding` is NOT by itself "waiting for a
  connection" (corrected 2026-10-09 after the §15 review of A8, which found the first edition calling every
  non-arrival a pool waiter); it reads as that only together with the pool counter standing at its limit.

Then it opens the gate and records every answer's status code and the slowest answer's time.

THE ONE HOOK. `PaymentService._bind_payment` is wrapped to wait for the gate. Nothing else of the product is
replaced. Without a gate a payment takes a few tens of milliseconds and the reading would depend on luck.

WHAT GUARDS THE READING (so a number is not an artefact):
* N=1 must reach the gate, and its payment must commit - else the stand does not exercise `pay()` at all;
* after every round the pool must be back to zero checked-out connections - else a round measures the previous
  one's leftovers;
* the script exits non-zero when either fails.

WHAT IT DOES NOT SHOW. The rate limiter is switched off for the run. Redis is absent (`app.state.redis` is None without the lifespan), so the per-payer lock is
a no-op: that is why payers are distinct. One process, one event loop: not a multi-worker deployment, where each
worker has a pool of its own. The clearing runner and the simulator are idle. Retries: no attempt conflicts here,
so "one session per attempt" is one session. A refusal's own short transaction is not exercised.

RUN (PowerShell; the database must be a disposable `geov0_dev_*` one - the script refuses anything else):

    $env:ENV = 'test'; $env:PYTHONPATH = (Get-Location).Path
    $env:DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_p035a8-pool'
    python scripts/dev_database.py create
    python -m alembic -c migrations/alembic.ini upgrade head
    python scripts/measure_payment_pool_usage.py --pool-timeout 5
    python scripts/dev_database.py drop

`--pool-timeout` shortens `DB_POOL_TIMEOUT_SECONDS` (30 in the settings) so that a round in which nothing can get
a connection ends in seconds; the default keeps the settings' value. `--sizes` chooses the N.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="1,2,5,7,8,10,14,15,16,20", help="comma-separated numbers of concurrent payments")
    parser.add_argument("--pool-timeout", type=int, default=None, help="override DB_POOL_TIMEOUT_SECONDS for this run")
    parser.add_argument("--settle", type=float, default=1.5, help="seconds without a new arrival before a round is read")
    return parser.parse_args()


ARGS = _arguments()
if ARGS.pool_timeout is not None:
    os.environ["DB_POOL_TIMEOUT_SECONDS"] = str(ARGS.pool_timeout)  # before the settings are constructed
# The stand sends a few hundred requests from one address; the per-address limiter (120 a minute) is not the
# subject and would answer 429 before any connection is taken. The test tier switches it off the same way.
os.environ["RATE_LIMIT_ENABLED"] = "false"

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.auth.canonical import canonical_json  # noqa: E402
from app.core.auth.crypto import generate_keypair  # noqa: E402
from app.core.payments.service import PaymentService  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models.equivalent import Equivalent  # noqa: E402
from app.main import app  # noqa: E402

EQUIVALENT = "USD"


def _sign(private_key: str, payload: dict) -> str:
    key = SigningKey(base64.b64decode(private_key))
    return base64.b64encode(key.sign(canonical_json(payload)).signature).decode("utf-8")


async def _register(client: httpx.AsyncClient, name: str) -> dict:
    public, private = generate_keypair()
    body = {"display_name": name, "type": "person", "public_key": public, "profile": {}}
    response = await client.post("/api/v1/participants", json={**body, "signature": _sign(private, body)})
    assert response.status_code == 201, response.text
    pid = response.json()["pid"]
    challenge = (await client.post("/api/v1/auth/challenge", json={"pid": pid})).json()["challenge"]
    key = SigningKey(base64.b64decode(private))
    signature = base64.b64encode(key.sign(challenge.encode("utf-8")).signature).decode("utf-8")
    login = await client.post("/api/v1/auth/login", json={"pid": pid, "challenge": challenge, "signature": signature})
    assert login.status_code == 200, login.text
    return {"pid": pid, "priv": private, "headers": {"Authorization": f"Bearer {login.json()['access_token']}"}}


async def _trust(client: httpx.AsyncClient, creditor: dict, debtor: dict) -> None:
    body = {"to": debtor["pid"], "equivalent": EQUIVALENT, "limit": "1000.00"}
    response = await client.post(
        "/api/v1/trustlines", json={**body, "signature": _sign(creditor["priv"], body)}, headers=creditor["headers"]
    )
    assert response.status_code == 201, response.text


def _payment(payer: dict, payee: dict) -> dict:
    body = {"tx_id": str(uuid.uuid4()), "to": payee["pid"], "equivalent": EQUIVALENT, "amount": "1.00"}
    return {**body, "signature": _sign(payer["priv"], body)}


class _Gate:
    """Holds every payment at the entry of `_bind_payment` until `open()`."""

    def __init__(self) -> None:
        self.arrived = 0
        self._release = asyncio.Event()
        self._release.set()
        original = PaymentService._bind_payment
        gate = self

        async def held(service, *args, **kwargs):
            gate.arrived += 1
            await gate._release.wait()
            return await original(service, *args, **kwargs)

        PaymentService._bind_payment = held  # the one hook (see the module docstring)

    def close(self) -> None:
        self.arrived = 0
        self._release.clear()

    def open(self) -> None:
        self._release.set()


async def _activity(observer: asyncpg.Connection) -> tuple[dict[str, int], list[str]]:
    rows = await observer.fetch(
        "select state, left(query, 70) as query from pg_stat_activity "
        "where datname = current_database() and pid <> pg_backend_pid() and state is not null"
    )
    states: dict[str, int] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    idle_in_transaction = sorted({" ".join(row["query"].split()) for row in rows if row["state"] == "idle in transaction"})
    return states, idle_in_transaction


async def _round(client: httpx.AsyncClient, observer: asyncpg.Connection, gate: _Gate, pairs: list, n: int) -> dict:
    pool = db_session.engine.sync_engine.pool
    gate.close()
    started = time.perf_counter()

    async def one(payer: dict, payee: dict) -> tuple[int, float, str]:
        response = await client.post("/api/v1/payments", json=_payment(payer, payee), headers=payer["headers"])
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        outcome = body.get("status") or (body.get("error") or {}).get("code") or "?"
        return response.status_code, time.perf_counter() - started, str(outcome)

    tasks = [asyncio.create_task(one(payer, payee)) for payer, payee in pairs[:n]]
    # Read once the arrivals stop: all N at the gate, or no new one for `--settle` seconds.
    last, since = -1, time.perf_counter()
    while gate.arrived < n and time.perf_counter() - since < ARGS.settle:
        if gate.arrived != last:
            last, since = gate.arrived, time.perf_counter()
        await asyncio.sleep(0.02)
    states, idle_queries = await _activity(observer)
    answered = sum(1 for task in tasks if task.done())
    reading = {
        "n": n,
        "at_binding": gate.arrived,
        "already_answered": answered,
        "before_binding": n - gate.arrived - answered,
        "pool_checked_out": pool.checkedout(),
        "pool_limit": settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW,
        "pool_overflow_in_use": max(0, pool.overflow()),
        "pg_states": dict(sorted(states.items())),
        "idle_in_transaction_last_statements": idle_queries,
    }
    gate.open()
    answers = await asyncio.gather(*tasks)
    reading["answers"] = {
        f"{status} {outcome}": sum(1 for s, _t, o in answers if (s, o) == (status, outcome))
        for status, outcome in sorted({(s, o) for s, _t, o in answers})
    }
    reading["slowest_answer_seconds"] = round(max(t for _s, t, _o in answers), 2)
    # An answer is sent before its request's session is closed; give the closes up to two seconds to land.
    drained = time.perf_counter()
    while pool.checkedout() and time.perf_counter() - drained < 2.0:
        await asyncio.sleep(0.02)
    reading["pool_checked_out_after"] = pool.checkedout()
    return reading


async def main() -> int:
    url = make_url(settings.DATABASE_URL)
    if not str(url.database or "").startswith("geov0_dev_"):
        print(f"refused: {url.database!r} is not a disposable geov0_dev_* database", file=sys.stderr)
        return 2
    sizes = [int(part) for part in ARGS.sizes.split(",") if part.strip()]
    pool = db_session.engine.sync_engine.pool
    limit = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
    print(json.dumps({
        "DB_POOL_SIZE": settings.DB_POOL_SIZE, "DB_MAX_OVERFLOW": settings.DB_MAX_OVERFLOW,
        "pool_limit": limit, "DB_POOL_TIMEOUT_SECONDS": settings.DB_POOL_TIMEOUT_SECONDS, "sizes": sizes,
    }))

    async with db_session.AsyncSessionLocal() as session:
        session.add(Equivalent(code=EQUIVALENT, description=EQUIVALENT, precision=2))
        await session.commit()

    observer = await asyncpg.connect(
        host=url.host, port=url.port or 5432, user=url.username, password=url.password, database=url.database
    )
    failures: list[str] = []
    try:
        # An unhandled error is answered (500), as a server would answer it, instead of raised into this script.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://measure", timeout=None) as client:
            pairs = []
            for index in range(max(sizes)):
                payer = await _register(client, f"Payer {index}")
                payee = await _register(client, f"Payee {index}")
                await _trust(client, payee, payer)
                pairs.append((payer, payee))
            assert pool.checkedout() == 0, f"the stand's own setup left {pool.checkedout()} connection(s) checked out"

            gate = _Gate()
            for n in sizes:
                reading = await _round(client, observer, gate, pairs, n)
                print(json.dumps(reading))
                if reading["pool_checked_out_after"] != 0:
                    failures.append(f"N={n}: {reading['pool_checked_out_after']} connection(s) still checked out after the round")
                if n == 1 and (reading["at_binding"] != 1 or reading["answers"] != {"200 COMMITTED": 1}):
                    failures.append(f"N=1 did not reach the gate and commit: {reading}")
    finally:
        await observer.close()
        await db_session.engine.dispose()
    for failure in failures:
        print(f"THE READING IS NOT VALID: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
