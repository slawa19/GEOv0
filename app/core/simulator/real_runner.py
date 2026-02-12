from __future__ import annotations

from typing import Any

# Keep these module-level imports: tests monkeypatch through this module.
import app.core.simulator.storage as simulator_storage
import app.db.session as db_session
from app.core.clearing.service import ClearingService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_runner_impl import RealRunnerImpl as _RealRunnerImpl


class RealRunner(_RealRunnerImpl):
    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: Unused now; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
    ) -> dict[str, float]:
        # Preserve monkeypatch hook points:
        # - tests patch real_runner.db_session.AsyncSessionLocal
        # - tests patch real_runner.ClearingService
        return await super().tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            async_session_local=db_session.AsyncSessionLocal,
            clearing_service_cls=ClearingService,
        )
