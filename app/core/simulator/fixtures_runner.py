from __future__ import annotations

from typing import Any, Callable, Optional

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast
from app.schemas.simulator import (
    SimulatorClearingDoneEvent,
    SimulatorClearingPlanEvent,
    SimulatorTxUpdatedEvent,
)


class FixturesRunner:
    def __init__(
        self,
        *,
        lock,
        get_run: Callable[[str], RunRecord],
        sse: SseBroadcast,
        utc_now,
    ) -> None:
        self._lock = lock
        self._get_run = get_run
        self._sse = sse
        self._utc_now = utc_now

    def tick_fixtures_events(self, run_id: str) -> None:
        run = self._get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return

        # Clearing lifecycle
        if run._clearing_pending_done_at_ms is not None and run.sim_time_ms >= run._clearing_pending_done_at_ms:
            # Emit clearing.done for all equivalents (best-effort). UI can ignore if not subscribed.
            for eq in list(run._edges_by_equivalent.keys()):
                plan_id = run._clearing_pending_plan_id_by_eq.get(eq)
                if not plan_id:
                    continue
                evt = SimulatorClearingDoneEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=self._utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                    plan_id=plan_id,
                ).model_dump(mode="json", by_alias=True)
                run.last_event_type = "clearing.done"
                run.current_phase = None
                self._sse.broadcast(run_id, evt)

            run._clearing_pending_done_at_ms = None
            run._clearing_pending_plan_id_by_eq.clear()
            run._next_clearing_at_ms = run.sim_time_ms + 45_000
            return

        if run._clearing_pending_done_at_ms is None and run.sim_time_ms >= run._next_clearing_at_ms:
            # Emit clearing.plan for all equivalents.
            for eq in list(run._edges_by_equivalent.keys()):
                plan = self.make_clearing_plan(run_id=run_id, equivalent=eq)
                if plan is None:
                    continue
                plan_id = str(plan.get("plan_id") or "").strip()
                if plan_id:
                    run._clearing_pending_plan_id_by_eq[eq] = plan_id
                run.last_event_type = "clearing.plan"
                run.current_phase = "clearing"
                self._sse.broadcast(run_id, plan)

            run._clearing_pending_done_at_ms = run.sim_time_ms + 2_000
            return

        # tx.updated cadence (based on intensity)
        if run.sim_time_ms < run._next_tx_at_ms:
            return

        # Higher intensity -> more frequent tx events.
        base_interval_ms = 2_000
        scale = max(0.25, 1.0 - (run.intensity_percent / 100.0) * 0.75)
        jitter = int(run._rng.randint(0, 600))
        run._next_tx_at_ms = run.sim_time_ms + int(base_interval_ms * scale) + jitter

        # Pick an equivalent that likely has edges.
        candidates = [eq for eq, edges in run._edges_by_equivalent.items() if edges]
        if not candidates:
            return
        eq = run._rng.choice(candidates)
        evt = self.maybe_make_tx_updated(run_id=run_id, equivalent=eq)
        if evt is None:
            return
        run.last_event_type = "tx.updated"
        self._sse.broadcast(run_id, evt)

    def maybe_make_tx_updated(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self._get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (src, dst) = run._rng.choice(edges)
        evt = SimulatorTxUpdatedEvent(
            event_id=self._sse.next_event_id(run),
            ts=self._utc_now(),
            type="tx.updated",
            equivalent=equivalent,
            ttl_ms=1200,
            intensity_key="mid" if run.intensity_percent < 70 else "hi",
            edges=[
                {
                    "from": src,
                    "to": dst,
                    "style": {"viz_width_key": "highlight", "viz_alpha_key": "hi"},
                }
            ],
            node_badges=[
                {"id": src, "viz_badge_key": "tx"},
                {"id": dst, "viz_badge_key": "tx"},
            ],
        ).model_dump(mode="json", by_alias=True)
        return evt

    def make_clearing_plan(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self._get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (e1_from, e1_to) = run._rng.choice(edges)
        (e2_from, e2_to) = run._rng.choice(edges)
        plan_id = f"clr_{run.run_id}_{run._event_seq + 1:06d}"
        evt = SimulatorClearingPlanEvent(
            event_id=self._sse.next_event_id(run),
            ts=self._utc_now(),
            type="clearing.plan",
            equivalent=equivalent,
            plan_id=plan_id,
            steps=[
                {"at_ms": 0, "highlight_edges": [{"from": e1_from, "to": e1_to}], "intensity_key": "hi"},
                {"at_ms": 180, "particles_edges": [{"from": e2_from, "to": e2_to}], "intensity_key": "mid"},
                {"at_ms": 420, "flash": {"kind": "clearing"}},
            ],
        ).model_dump(mode="json", by_alias=True)
        return evt
