from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.core.simulator.commit_resolution import resolve_commit_under_cancellation
from app.core.simulator.models import RunRecord
from app.core.simulator.trust_drift_engine import TrustDriftEngine


class RealTickTrustDriftCoordinator:
    def __init__(self, *, logger: logging.Logger) -> None:
        self._logger = logger

    def _apply_callback(self, callback: Callable[[], Any] | None, *, kind: str) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            self._logger.warning(
                "simulator.real.payment_%s_callback_failed",
                kind,
                exc_info=True,
            )

    async def _commit_and_resolve(
        self,
        session: Any,
        *,
        on_commit: Callable[[], Any] | None,
        on_rollback: Callable[[], Any] | None,
    ) -> None:
        await resolve_commit_under_cancellation(
            commit=session.commit,
            rollback=session.rollback,
            on_commit=lambda: self._apply_callback(on_commit, kind="post_commit"),
            on_rollback=lambda: self._apply_callback(on_rollback, kind="rollback"),
            logger=self._logger,
        )

    async def apply_trust_decay_and_broadcast(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        tick_index: int,
        debt_snapshot: dict[tuple[str, str, str], Any],
        scenario: dict[str, Any],
        trust_drift_engine: TrustDriftEngine,
        build_edge_patch_for_equivalent: Callable[..., Awaitable[dict[str, Any]]],
        broadcast_topology_edge_patch: Callable[..., None],
        on_commit: Callable[[], Any] | None = None,
        on_rollback: Callable[[], Any] | None = None,
    ) -> None:
        try:
            decay_res = await trust_drift_engine.apply_trust_decay(
                run=run,
                session=session,
                tick_index=int(tick_index or 0),
                debt_snapshot=debt_snapshot,
                scenario=scenario,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.warning(
                "simulator.real.trust_drift.decay_failed run_id=%s tick=%s",
                str(run.run_id),
                int(tick_index or 0),
                exc_info=True,
            )
            return

        if not decay_res.updated_count:
            return

        await self._commit_and_resolve(
            session,
            on_commit=on_commit,
            on_rollback=on_rollback,
        )

        # Notify frontend about changed limits via edge_patch (no full refresh).
        try:
            for eq in sorted(decay_res.touched_equivalents or set()):
                eq_upper = str(eq or "").strip().upper()
                if not eq_upper:
                    continue
                only_edges = (decay_res.touched_edges_by_eq or {}).get(eq_upper)
                edge_patch = await build_edge_patch_for_equivalent(
                    session=session,
                    run=run,
                    equivalent_code=eq_upper,
                    only_edges=only_edges,
                    include_width_keys=True,
                )
                broadcast_topology_edge_patch(
                    run_id=run_id,
                    run=run,
                    equivalent=eq_upper,
                    edge_patch=edge_patch,
                    reason="trust_drift_decay",
                )
        except Exception:
            self._logger.warning(
                "simulator.real.trust_drift.decay_edge_patch_broadcast_error",
                exc_info=True,
            )
