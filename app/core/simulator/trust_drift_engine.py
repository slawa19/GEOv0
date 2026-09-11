from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Callable

from sqlalchemy import select, update

from app.core.payments.router import PaymentRouter
from app.core.simulator.commit_resolution import resolve_commit_under_cancellation
from app.utils.validation import MONEY_MAX_SCALE

# The grain of the ledger, not of a currency's display. `trust_lines.limit` and `debts.amount` are
# `Numeric(20, 8)`, and since 012/T1201 the money door refuses anything the column cannot hold
# unchanged - so eight fraction digits are legitimate content that the production core writes.
#
# This engine used to quantise to `Decimal("0.01")` on both the read and the write of a stored
# limit, so a limit the column held exactly was flattened to cents on the first drift tick and
# every later tick started from the flattened number: the limit walked away from what was agreed.
# `Equivalent.precision` is deliberately NOT used as the quantum here - it is a DISPLAY minimum
# (`app/utils/money.py`), not the ledger's grain, and rounding stored money to it would be the same
# defect wearing a different constant.
_LEDGER_QUANTUM = Decimal(1).scaleb(-MONEY_MAX_SCALE)

from app.core.simulator.models import (
    EdgeClearingHistory,
    RunRecord,
    TrustDriftConfig,
    TrustDriftLimitUpdate,
    TrustDriftResult,
)
from app.core.simulator.scenario_equivalent import effective_equivalent
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

    def apply_committed_effects(
        self,
        *,
        scenario: dict[str, Any],
        result: TrustDriftResult,
    ) -> None:
        """Publish staged limit changes to in-memory owners after DB commit."""

        updates = tuple(result.committed_limit_updates or ())
        if not updates:
            return

        trustlines = scenario.get("trustlines") or []
        for update_item in updates:
            for trustline in trustlines:
                if (
                    str(trustline.get("from") or "").strip()
                    == update_item.creditor_pid
                    and str(trustline.get("to") or "").strip()
                    == update_item.debtor_pid
                    and str(effective_equivalent(scenario, trustline) or "")
                    .strip()
                    .upper()
                    == update_item.equivalent
                ):
                    # T1514: NOT `float`. This entry is what the decay path reads on the next
                    # tick and turns into a DB write, so a limit the column holds exactly was
                    # being round-tripped through binary floating point between the two halves of
                    # one feature. A string is what every reader of this dict already expects -
                    # they all do `Decimal(str(...))` - and it is the money form 012/`T1207`
                    # settled on. `SimulatorGraphLink.trust_limit` is `NumberOrString`, so the
                    # snapshot accepts it unchanged.
                    trustline["limit"] = str(update_item.new_limit)
                    break

        for equivalent in result.touched_equivalents:
            PaymentRouter._graph_cache.pop(str(equivalent).strip().upper(), None)

    def init_trust_drift(self, run: RunRecord, scenario: dict[str, Any]) -> None:
        """Initialize trust drift config and edge clearing history from scenario."""

        run._trust_drift_config = TrustDriftConfig.from_scenario(scenario)
        if run._edge_clearing_history:
            return  # already populated (e.g. by inject adding edges)

        trustlines = scenario.get("trustlines") or []
        for tl in trustlines:
            eq = str(effective_equivalent(scenario, tl) or "").strip().upper()
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
        committed_limit_updates: list[TrustDriftLimitUpdate] = []
        scenario = getattr(run, "_scenario_raw", None) or self._get_scenario_raw(
            run.scenario_id
        )
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

            # Get current limit from DB.  Drift applies to the ACTIVE line only: a
            # closed incarnation is history and a frozen one is quarantined, and since
            # migration 019 both may coexist with the active row.
            tl_limit_row = (
                await clearing_session.execute(
                    select(TrustLine.limit).where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                        TrustLine.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if tl_limit_row is None:
                continue

            try:
                # T1514: read the stored limit AS STORED. Truncating it to cents here was the
                # first half of the walk - the multiplication below then started from a number
                # the database never held.
                current_limit = Decimal(str(tl_limit_row))
            except Exception:
                continue

            rate_mult = (Decimal("1") + Decimal(str(cfg.growth_rate))).quantize(
                Decimal("0.0000001")
            )
            max_growth = Decimal(str(cfg.max_growth))
            # T1514: quantise to the LEDGER's grain, not to cents. The multiplication can
            # produce more digits than the column holds, so a quantum is required here - it is
            # the size of it that was wrong. `ROUND_DOWN` is kept: it keeps the result under both
            # bounds of the `min`, which is the direction a ceiling must round.
            new_limit = min(
                (current_limit * rate_mult),
                (original_limit * max_growth),
            ).quantize(_LEDGER_QUANTUM, rounding=ROUND_DOWN)

            if new_limit != current_limit:
                await clearing_session.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == creditor_uuid,
                        TrustLine.to_participant_id == debtor_uuid,
                        TrustLine.equivalent_id == eq_id,
                        # ACTIVE only: migration 019 lets a closed incarnation coexist,
                        # and drift must never rewrite history.
                        TrustLine.status == "active",
                    )
                    .values(limit=new_limit)
                )

                committed_limit_updates.append(
                    TrustDriftLimitUpdate(
                        creditor_pid=creditor_pid,
                        debtor_pid=debtor_pid,
                        equivalent=eq_upper,
                        new_limit=new_limit,
                    )
                )

                self._logger.info(
                    "simulator.real.trust_drift.growth key=%s old=%s new=%s",
                    key,
                    current_limit,
                    new_limit,
                )
                updated += 1
                updated_edges.add((creditor_pid, debtor_pid))

        touched_eqs = {eq_upper} if updated_edges else set()
        touched_edges_by_eq = {eq_upper: updated_edges} if updated_edges else {}
        result = TrustDriftResult(
            updated_count=int(updated),
            touched_equivalents=touched_eqs,
            touched_edges_by_eq=touched_edges_by_eq,
            committed_limit_updates=tuple(committed_limit_updates),
        )
        if updated:
            await resolve_commit_under_cancellation(
                commit=clearing_session.commit,
                rollback=clearing_session.rollback,
                on_commit=lambda: self.apply_committed_effects(
                    scenario=scenario,
                    result=result,
                ),
                on_rollback=lambda: None,
                on_unknown=lambda: None,
                logger=self._logger,
            )
        return result

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
        committed_limit_updates: list[TrustDriftLimitUpdate] = []
        trustlines = scenario.get("trustlines") or []

        for tl in trustlines:
            eq_code = str(effective_equivalent(scenario, tl) or "").strip().upper()
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

            # Get current limit from scenario (in-memory, kept in sync by growth).
            #
            # T1514: no re-quantisation. This entry is the DB limit round-tripped through
            # `apply_committed_effects`, so truncating it here is the same rewrite as on the
            # growth side, one hop removed - and the value computed from it IS written back to
            # `trust_lines.limit` below.
            try:
                current_limit = Decimal(str(tl.get("limit", 0)))
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

            # Guardrail: trust drift must never shrink limit below already-used debt,
            # otherwise we can create a TRUST_LIMIT_VIOLATION without any new payment.
            try:
                # T1514: the floor is DB money - `sum(debts.amount)` - and it can WIN the `max`
                # below, in which case it becomes the written limit. At the ledger's own grain
                # this rounding is an identity on a stored value; `ROUND_UP` is kept because a
                # floor must never be understated, and it is the one place in this file where
                # rounding up is the safe direction (see `F-015-16` on the rounding-mode split).
                debt_floor = Decimal(str(debt_amount)).quantize(
                    _LEDGER_QUANTUM, rounding=ROUND_UP
                )
            except Exception:
                debt_floor = Decimal("0")
            # T1514: the ledger's grain, as on the growth side. `ROUND_DOWN` is kept, and it
            # is safe against the floor: `debt_floor` is already at this grain, so rounding the
            # `max` down cannot take the result below it.
            new_limit = max(
                (current_limit * decay_mult),
                (original_limit * min_ratio),
                debt_floor,
            ).quantize(_LEDGER_QUANTUM, rounding=ROUND_DOWN)

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
                    # ACTIVE only: migration 019 lets a closed incarnation coexist,
                    # and drift must never rewrite history.
                    TrustLine.status == "active",
                )
                .values(limit=new_limit)
            )

            committed_limit_updates.append(
                TrustDriftLimitUpdate(
                    creditor_pid=creditor_pid,
                    debtor_pid=debtor_pid,
                    equivalent=eq_code,
                    new_limit=new_limit,
                )
            )
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

        return TrustDriftResult(
            updated_count=int(updated),
            touched_equivalents=set(touched_eq_codes),
            touched_edges_by_eq={k: set(v) for k, v in touched_edges_by_eq.items()},
            committed_limit_updates=tuple(committed_limit_updates),
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
