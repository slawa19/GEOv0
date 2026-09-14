"""T1549 class-2 reproduction: two concurrent POST /api/v1/trustlines for the same triple.

Runs the REAL route through the REAL application engine (`app.db.session`, isolation from
`settings.DB_POSTGRES_ISOLATION_LEVEL`), not the test engine. The only test device is a two-party
barrier at the service's single `flush()` - the schedule RT-009-7 already names: both requests are past
the duplicate guard, neither has inserted. Authentication is overridden to a fixed participant; the
database session dependency is NOT overridden.

usage: python repro_trustline_40001.py "SERIALIZABLE" | "READ COMMITTED"
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import uuid

ISOLATION = sys.argv[1] if len(sys.argv) > 1 else "SERIALIZABLE"
os.environ["ENV"] = "test"
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_t1549"
os.environ["DB_POSTGRES_ISOLATION_LEVEL"] = ISOLATION
sys.path.insert(0, r"D:\www\projects\2025\GEOv0")

import httpx  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import settings  # noqa: E402

settings.RATE_LIMIT_ENABLED = False
settings.RECOVERY_ENABLED = False

from app.api import deps  # noqa: E402
from app.core.auth.canonical import canonical_json  # noqa: E402
from app.core.auth.crypto import generate_keypair  # noqa: E402
from app.db import session as app_db  # noqa: E402
from app.db.models.audit_log import IntegrityAuditLog  # noqa: E402
from app.db.models.equivalent import Equivalent  # noqa: E402
from app.db.models.participant import Participant  # noqa: E402
from app.db.models.trustline import TrustLine  # noqa: E402
from app.main import app  # noqa: E402


async def main() -> int:
    async with app_db.AsyncSessionLocal() as probe:
        level = (await probe.execute(text("SHOW transaction_isolation"))).scalar_one()
    print(f"setting DB_POSTGRES_ISOLATION_LEVEL={settings.DB_POSTGRES_ISOLATION_LEVEL!r}; "
          f"application engine connection reports transaction_isolation={level!r}")

    nonce = uuid.uuid4().hex[:9]
    pub, priv = generate_keypair()
    eq = Equivalent(code=("R" + nonce).upper(), symbol="R", precision=2, metadata_={}, is_active=True)
    sender = Participant(id=uuid.uuid4(), pid="rs-" + nonce, display_name="S", public_key=pub,
                         type="person", status="active", profile={})
    receiver = Participant(id=uuid.uuid4(), pid="rr-" + nonce, display_name="R", public_key="pk-" + nonce,
                           type="person", status="active", profile={})
    async with app_db.AsyncSessionLocal() as seed:
        seed.add_all([eq, sender, receiver])
        await seed.commit()

    payload = {"to": receiver.pid, "equivalent": eq.code, "limit": "10"}
    signature = base64.b64encode(
        SigningKey(base64.b64decode(priv)).sign(canonical_json(payload)).signature
    ).decode()

    class _Current:
        id = sender.id

    app.dependency_overrides[deps.get_current_participant] = lambda: _Current()

    barrier = asyncio.Barrier(2)
    waited: set[int] = set()
    original_flush = AsyncSession.flush

    async def _flush(self, *args, **kwargs):
        if id(self) not in waited:
            waited.add(id(self))
            await asyncio.wait_for(barrier.wait(), timeout=15)
        return await original_flush(self, *args, **kwargs)

    AsyncSession.flush = _flush  # type: ignore[method-assign]
    errors: list[str] = []
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def _post():
                return await client.post("/api/v1/trustlines", json={**payload, "signature": signature})

            responses = await asyncio.gather(_post(), _post())
        codes = sorted(r.status_code for r in responses)
        for r in responses:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
    finally:
        AsyncSession.flush = original_flush  # type: ignore[method-assign]
        app.dependency_overrides.clear()
        async with app_db.AsyncSessionLocal() as check:
            live = (await check.execute(
                select(TrustLine).where(TrustLine.equivalent_id == eq.id, TrustLine.status != "closed")
            )).scalars().all()
            print(f"live trustlines for the triple after both requests: {len(live)}")
            await check.execute(delete(IntegrityAuditLog).where(IntegrityAuditLog.equivalent_code == eq.code))
            await check.execute(delete(TrustLine).where(TrustLine.equivalent_id == eq.id))
            await check.execute(delete(Participant).where(Participant.id.in_([sender.id, receiver.id])))
            await check.execute(delete(Equivalent).where(Equivalent.id == eq.id))
            await check.commit()
        await app_db.engine.dispose()
    print(f"RESULT isolation={ISOLATION} codes={codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
