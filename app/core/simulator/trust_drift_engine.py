from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

from sqlalchemy import select, update

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import (
    EdgeClearingHistory,
    RunRecord,
    TrustDriftConfig,
    TrustDriftResult,
)
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.db.models.equivalent import Equivalent
from app.db.models.trustline import TrustLine
from app.schemas.simulator import TopologyChangedPayload


def broadcast_trust_drift_changed(
    *,
    sse: SseBroadcast,
    utc_now,
    logger: logging.Logger,
    run_id: str,
    run: RunRecord,
    reason: str,
    equivalents: list[str] | set[str],
    edge_patches_by_eq: dict[str, list[dict]] | None = None,
) -> None:
    """Broadcast SSE topology.changed for trust-drift limit changes.

    When *edge_patches_by_eq* is provided, each event carries a non-empty
    ``payload.edge_patch`` so the frontend can apply incremental updates
    **without** a full snapshot refresh.  If no edge_patch is available for
    a given equivalent the event is **skipped** — sending an empty payload
    would trigger ``refreshSnapshot()`` on every tick and cause visible
    jitter / "sticking" in the UI.

    Best-effort: errors are logged but never crash the tick.
    """

    try:
        emitter = SseEventEmitter(sse=sse, utc_now=utc_now, logger=logger)

        for eq in equivalents:
            eq_upper = str(eq).strip().upper()
            if not eq_upper:
                continue

            edge_patch = (edge_patches_by_eq or {}).get(eq_upper) or []
            if not edge_patch:
                # Skip: empty topology.changed would trigger full refreshSnapshot()
                # on the frontend and cause jitter.
                logger.debug(
                    "simulator.real.trust_drift.topology_changed_skipped_empty eq=%s reason=%s",
                    eq_upper,
                    reason,
                )
                continue

            payload = TopologyChangedPayload(edge_patch=edge_patch)
            emitter.emit_topology_changed(
                run_id=run_id,
                run=run,
                equivalent=eq_upper,
                payload=payload,
                reason=reason,
            )
            logger.info(
                "simulator.real.trust_drift.topology_changed eq=%s reason=%s edges=%d",
                eq_upper,
                reason,
                len(edge_patch),
            )
    except Exception:
        logger.warning(
            "simulator.real.trust_drift.topology_changed_broadcast_error reason=%s",
            reason,
            exc_info=True,
        )


class TrustDriftEngine:
    def __init__(
        self,
        *,
        sse: SseBroadcast,
        utc_now,
        logger: logging.Logger,
        get_scenario_raw: Callable[[str], dict[str, Any]],
    ) -> None:
        self._sse = sse
        self._utc_now = utc_now
        self._logger = logger
        self._get_scenario_raw = get_scenario_raw

    def init_trust_drift(self, run: RunRecord, scenario: dict[str, Any]) -> None:
        """Initialize trust drift config and edge clearing history from scenario."""

        run._trust_drift_config = TrustDriftConfig.from_scenario(scenario)
        if run._edge_clearing_history:
            return  # already populated (e.g. by inject adding edges)

        trustlines = scenario.get("trustlines") or []
        for tl in trustlines:
            eq = str(tl.get("equivalent") or "").strip().upper()
            creditor_pid = str(tl.get("from") or "").strip()
            debtor_pid = str(tl.get("to") or "").strip()
            if not eq or not creditor_pid or not debtor_pid:
                continue

            try:
                limit = Decimal(str(tl.get("limit", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            if limit <= 0:
                continue

            key = f"{creditor_pid}:{debtor_pid}:{eq}"
            run._edge_clearing_history[key] = EdgeClearingHistory(original_limit=limit)

        if run._trust_drift_config.enabled:
            self._logger.info(
                "simulator.real.trust_drift.init run_id=%s edges=%d "
                "growth_rate=%s decay_rate=%s max_growth=%s "
                "min_limit_ratio=%s overload_threshold=%s",
                run.run_id,
                len(run._edge_clearing_history),
                run._trust_drift_config.growth_rate,
                run._trust_drift_config.decay_rate,
                run._trust_drift_config.max_growth,
                run._trust_drift_config.min_limit_ratio,
                run._trust_drift_config.overload_threshold,
            )

    async def apply_trust_growth(
        self,
        run: RunRecord,
        clearing_session,
        touched_edges: set[tuple[str, str]],
        eq_code: str,
        tick_index: int,
        cleared_amount_per_edge: dict[tuple[str, str], float],
    ) -> TrustDriftResult:
        """Apply trust growth to edges that participated in clearing.

        Uses the *clearing_session* (isolated per-equivalent session used by
        ``tick_real_mode_clearing``). Commits internally on success.

        Returns structured information about updated edges.
        """

        cfg = run._trust_drift_config
        if not cfg or not cfg.enabled:
            return TrustDriftResult(updated_count=0)

        if not touched_edges:
            return TrustDriftResult(updated_count=0)

        pid_to_uuid: dict[str, uuid.UUID] = {
            pid: uid for uid, pid in (run._real_participants or [])
        }

        eq_upper = eq_code.strip().upper()
        try:
            eq_id = (
                await clearing_session.execute(
                    select(Equivalent.id).where(Equivalent.code == eq_upper)
                )
            ).scalar_one_or_none()
        except Exception:
            return TrustDriftResult(updated_count=0)
        if not eq_id:
            return TrustDriftResult(updated_count=0)

        updated = 0
        updated_edges: set[tuple[str, str]] = set()
        for creditor_pid, debtor_pid in touched_edges:
            key = f"{creditor_pid}:{debtor_pid}:{eq_upper}"
            hist = run._edge_clearing_history.get(key)
            if not hist:
                continue

            try:
                original_limit = Decimal(str(hist.original_limit)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue

            # Update history
            hist.clearing_count += 1
            hist.last_clearing_tick = tick_index
            try:
                hist.cleared_volume += Decimal(
                    str(cleared_amount_per_edge.get((creditor_pid, debtor_pid), 0.0))
                ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            except Exception:
                pass

            creditor_uuid = pid_to_uuid.get(creditor_pid)
            debtor_uuid = pid_to_uuid.get(debtor_pid)
            if not creditor_uuid or not debtor_uuid:
                continue

            # Get current limit from DB
            tl_limit_row = (
                await clearing_session.execute(
                    select(TrustLine.limit).where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                    )
                )
            ).scalar_one_or_none()
            if tl_limit_row is None:
                continue

            try:
                current_limit = Decimal(str(tl_limit_row)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue

            rate_mult = (Decimal("1") + Decimal(str(cfg.growth_rate))).quantize(
                Decimal("0.0000001")
            )
            max_growth = Decimal(str(cfg.max_growth))
            new_limit = min(
                (current_limit * rate_mult),
                (original_limit * max_growth),
            ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            if new_limit != current_limit:
                await clearing_session.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                    )
                    .values(limit=new_limit)
                )

                # Update scenario trustlines in-memory
                scenario = getattr(run, "_scenario_raw", None) or self._get_scenario_raw(
                    run.scenario_id
                )
                s_tls = scenario.get("trustlines") or []
                for s_tl in s_tls:
                    if (
                        str(s_tl.get("from") or "").strip() == creditor_pid
                        and str(s_tl.get("to") or "").strip() == debtor_pid
                        and str(s_tl.get("equivalent") or "").strip().upper() == eq_upper
                    ):
                        s_tl["limit"] = float(new_limit)
                        break

                self._logger.info(
                    "simulator.real.trust_drift.growth key=%s old=%s new=%s",
                    key,
                    current_limit,
                    new_limit,
                )
                updated += 1
                updated_edges.add((creditor_pid, debtor_pid))

        if updated:
            # Trust drift changes limits and must invalidate routing cache.
            PaymentRouter._graph_cache.pop(eq_upper, None)
            await clearing_session.commit()

        touched_eqs = {eq_upper} if updated_edges else set()
        touched_edges_by_eq = {eq_upper: updated_edges} if updated_edges else {}
        return TrustDriftResult(
            updated_count=int(updated),
            touched_equivalents=touched_eqs,
            touched_edges_by_eq=touched_edges_by_eq,
        )

    async def apply_trust_decay(
        self,
        run: RunRecord,
        session,
        tick_index: int,
        debt_snapshot: dict[tuple[str, str, str], Decimal],
        scenario: dict[str, Any],
    ) -> TrustDriftResult:
        """Apply trust decay to overloaded edges that didn't get cleared.

        Uses the main tick session. Does NOT commit — caller commits.
        Returns count of decayed edges.
        """

        cfg = run._trust_drift_config
        if not cfg or not cfg.enabled:
            return TrustDriftResult(updated_count=0)

        pid_to_uuid: dict[str, uuid.UUID] = {
            pid: uid for uid, pid in (run._real_participants or [])
        }

        eq_id_cache: dict[str, uuid.UUID] = {}
        updated = 0
        touched_eq_codes: set[str] = set()
        touched_edges_by_eq: dict[str, set[tuple[str, str]]] = {}
        trustlines = scenario.get("trustlines") or []

        for tl in trustlines:
            eq_code = str(tl.get("equivalent") or "").strip().upper()
            creditor_pid = str(tl.get("from") or "").strip()
            debtor_pid = str(tl.get("to") or "").strip()
            status = str(tl.get("status") or "active").strip().lower()

            if not eq_code or not creditor_pid or not debtor_pid:
                continue
            if status != "active":
                continue

            key = f"{creditor_pid}:{debtor_pid}:{eq_code}"
            hist = run._edge_clearing_history.get(key)
            if not hist:
                continue

            # Skip if just cleared this tick
            if hist.last_clearing_tick == tick_index:
                continue

            # Get current limit from scenario (in-memory, kept in sync by growth)
            try:
                current_limit = Decimal(str(tl.get("limit", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            if current_limit <= 0:
                continue

            # Debt for this edge: debt_snapshot key is (debtor_pid, creditor_pid, eq_code)
            debt_amount = debt_snapshot.get((debtor_pid, creditor_pid, eq_code), Decimal("0"))

            ratio = (debt_amount / current_limit) if current_limit > 0 else Decimal("0")
            if ratio < Decimal(str(cfg.overload_threshold)):
                continue

            # Calculate new limit
            decay_mult = Decimal(str(1 - cfg.decay_rate))
            min_ratio = Decimal(str(cfg.min_limit_ratio))

            try:
                original_limit = Decimal(str(hist.original_limit)).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                )
            except Exception:
                continue
            new_limit = max(
                (current_limit * decay_mult),
                (original_limit * min_ratio),
            ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            if new_limit == current_limit:
                continue

            # Resolve UUIDs
            creditor_uuid = pid_to_uuid.get(creditor_pid)
            debtor_uuid = pid_to_uuid.get(debtor_pid)
            if not creditor_uuid or not debtor_uuid:
                continue

            if eq_code not in eq_id_cache:
                eq_row = (
                    await session.execute(
                        select(Equivalent.id).where(Equivalent.code == eq_code)
                    )
                ).scalar_one_or_none()
                if eq_row is None:
                    continue
                eq_id_cache[eq_code] = eq_row

            eq_id = eq_id_cache.get(eq_code)
            if not eq_id:
                continue

            await session.execute(
                update(TrustLine)
                .where(
                    TrustLine.from_participant_id == creditor_uuid,
                    TrustLine.to_participant_id == debtor_uuid,
                    TrustLine.equivalent_id == eq_id,
                )
                .values(limit=new_limit)
            )

            # Update scenario trustlines in-memory
            tl["limit"] = float(new_limit)
            touched_eq_codes.add(eq_code)
            touched_edges_by_eq.setdefault(eq_code, set()).add((creditor_pid, debtor_pid))

            self._logger.info(
                "simulator.real.trust_drift.decay key=%s old=%s new=%s ratio=%.2f",
                key,
                current_limit,
                new_limit,
                float(ratio),
            )
            updated += 1

        if touched_eq_codes:
            for eq in touched_eq_codes:
                PaymentRouter._graph_cache.pop(str(eq).strip().upper(), None)

        return TrustDriftResult(
            updated_count=int(updated),
            touched_equivalents=set(touched_eq_codes),
            touched_edges_by_eq={k: set(v) for k, v in touched_edges_by_eq.items()},
        )

    def broadcast_trust_drift_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        reason: str,
        equivalents: list[str] | set[str],
        edge_patches_by_eq: dict[str, list[dict]] | None = None,
    ) -> None:
        broadcast_trust_drift_changed(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            run_id=run_id,
            run=run,
            reason=reason,
            equivalents=equivalents,
            edge_patches_by_eq=edge_patches_by_eq,
        )
