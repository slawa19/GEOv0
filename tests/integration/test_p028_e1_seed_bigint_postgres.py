"""028 E1 / F-028-1: a run whose seed needs 32 bits is stored, with the seed it ran on.

The seed is the first 4 bytes of sha256(run_id) (`run_lifecycle.create_run`) - up to 2**32-1 - and it seeds the
planner's generator (`real_payment_planner.py`, `tick_seed`). Before migration 034 `simulator_runs.seed` was
`integer`: for about half of all run ids `upsert_run` failed, the failure was swallowed and the run had no row.
The seed is NOT masked to fit (spec 028, «Запрещено»): masking would change the planner's sequence for that run.
Via `runtime.create_run`, the path the API takes; the heartbeat clock is the p024 stand (no real time)."""

import hashlib

from sqlalchemy import select

import app.core.simulator.runtime_impl as runtime_impl
import app.db.session as app_db_session
from app.config import settings
from app.core.simulator.runtime import runtime
from app.db.models.simulator_storage import SimulatorRun
from tests.conftest import MODE_B, sessionmaker_of
from tests.p019_support import require_target
from tests.unit.test_p024_heartbeat_follows_every_entry_into_running import _Clock

pytestmark = MODE_B


def _high_seed_run_id() -> str:
    return next(rid for i in range(64) if hashlib.sha256((rid := f"p028-e1-seed-{i}").encode()).digest()[0] >= 0x80)


async def test_a_32_bit_seed_is_stored(db_session, monkeypatch) -> None:
    factory = sessionmaker_of(db_session)
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    monkeypatch.setattr(runtime_impl, "asyncio", _Clock())
    run_id = _high_seed_run_id()
    monkeypatch.setattr(runtime._run_lifecycle, "_new_run_id", lambda: run_id)
    await runtime.create_run(scenario_id="greenfield-village-100-realistic-v2", mode="fixtures",
                             intensity_percent=50, owner_id="anon:p028-e1-seed")
    try:
        expected = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:4], "big")
        assert expected >= 2**31 and runtime.get_run(run_id).seed == expected, "control: the seed is not masked"
        async with factory() as session:
            stored = (await session.execute(select(SimulatorRun.seed).where(SimulatorRun.run_id == run_id))).all()
    finally:
        await runtime.stop(run_id)
    require_target(stored == [(expected,)], f"simulator_runs for a 32-bit seed {expected}: {stored}")
