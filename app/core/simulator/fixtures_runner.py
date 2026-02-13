from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter


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
        self._logger = logging.getLogger(__name__)

    def tick_fixtures_events(self, run_id: str) -> None:
        run = self._get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return

        emitter = SseEventEmitter(sse=self._sse, utc_now=self._utc_now, logger=self._logger)

        # Clearing lifecycle
        if run._clearing_pending_done_at_ms is not None and run.sim_time_ms >= run._clearing_pending_done_at_ms:
            # Emit clearing.done for all equivalents (best-effort). UI can ignore if not subscribed.
            for eq in list(run._edges_by_equivalent.keys()):
                plan_id = run._clearing_pending_plan_id_by_eq.get(eq)
                if not plan_id:
                    continue
                cycle_edges = run._clearing_pending_cycle_edges_by_eq.get(eq)
                run.last_event_type = "clearing.done"
                run.current_phase = None
                emitter.emit_clearing_done(
                    run_id=run_id,
                    run=run,
                    equivalent=eq,
                    plan_id=plan_id,
                    cleared_cycles=1,
                    # Fixtures-mode clearing is a visualization aid; provide a
                    # deterministic non-zero total so UI can show the flyout label.
                    cleared_amount="10.00",
                    cycle_edges=cycle_edges,
                )

            run._clearing_pending_done_at_ms = None
            run._clearing_pending_plan_id_by_eq.clear()
            run._clearing_pending_cycle_edges_by_eq.clear()
            run._next_clearing_at_ms = run.sim_time_ms + 45_000
            return

        if run._clearing_pending_done_at_ms is None and run.sim_time_ms >= run._next_clearing_at_ms:
            # Schedule clearing.done for all equivalents (emit only on completion).
            for eq in list(run._edges_by_equivalent.keys()):
                edges = (run._edges_by_equivalent or {}).get(eq) or []
                if not edges:
                    continue

                (e1_from, e1_to) = run._rng.choice(edges)
                (e2_from, e2_to) = run._rng.choice(edges)
                plan_id = f"clr_{run.run_id}_{run._event_seq + 1:06d}"
                run._clearing_pending_plan_id_by_eq[eq] = plan_id
                run._clearing_pending_cycle_edges_by_eq[eq] = [
                    {"from": str(e1_from), "to": str(e1_to)},
                    {"from": str(e2_from), "to": str(e2_to)},
                ]
                run.current_phase = "clearing"

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
        emitter.emit_tx_updated(
            run_id=run_id,
            run=run,
            equivalent=eq,
            from_pid=str(evt.get("from") or "") or None,
            to_pid=str(evt.get("to") or "") or None,
            amount=str(evt.get("amount") or "") or None,
            amount_flyout=bool(evt.get("amount_flyout")),
            ttl_ms=int(evt.get("ttl_ms") or 1200),
            intensity_key=str(evt.get("intensity_key") or "") or None,
            edges=list(evt.get("edges") or []),
            node_badges=(list(evt.get("node_badges") or []) or None),
            event_id=str(evt.get("event_id") or "") or None,
        )

    def maybe_make_tx_updated(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self._get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (src, dst) = run._rng.choice(edges)
        return {
            "event_id": self._sse.next_event_id(run),
            "ts": self._utc_now().isoformat(),
            "type": "tx.updated",
            "equivalent": equivalent,
            "amount_flyout": False,
            "ttl_ms": 1200,
            "intensity_key": "mid" if run.intensity_percent < 70 else "hi",
            "edges": [
                {
                    "from": src,
                    "to": dst,
                    "style": {"viz_width_key": "highlight", "viz_alpha_key": "hi"},
                }
            ],
            "node_badges": [
                {"id": src, "viz_badge_key": "tx"},
                {"id": dst, "viz_badge_key": "tx"},
            ],
        }


