from __future__ import annotations

import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Optional

from sqlalchemy import func, select

import app.db.session as db_session
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.models import RunRecord, ScenarioRecord
from app.core.simulator.net_balance_utils import atoms_to_net_sign, net_decimal_to_atoms
from app.core.simulator import viz_rules
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.schemas.simulator import (
    SIMULATOR_API_VERSION,
    SimulatorGraphLink,
    SimulatorGraphNode,
    SimulatorGraphSnapshot,
    SimulatorVizSize,
)
from app.utils.exceptions import NotFoundException


class SnapshotBuilder:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        scenarios: dict[str, ScenarioRecord],
        utc_now,
        db_enabled,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._scenarios = scenarios
        self._utc_now = utc_now
        self._db_enabled = db_enabled

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def _get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self._lock:
            rec = self._scenarios.get(scenario_id)
        if rec is None:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return rec

    async def build_graph_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        run = self._get_run(run_id)
        scenario = getattr(run, "_scenario_raw", None) or self._get_scenario(run.scenario_id).raw
        snap = scenario_to_snapshot(scenario, equivalent=equivalent, utc_now=self._utc_now)
        if run.mode != "real" or not self._db_enabled():
            return snap
        return await self._enrich_snapshot_from_db(snap, equivalent=equivalent, session=session)

    async def _enrich_snapshot_from_db(
        self,
        snap: SimulatorGraphSnapshot,
        *,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        """Real-mode only.

        Enrich scenario-topology snapshot with DB-derived state:
        - link.used/available based on Debt amounts
        - node.net_balance_atoms / node.net_sign
        """

        eq_code = str(equivalent or "").strip().upper()
        if not eq_code:
            return snap

        pids = [n.id for n in snap.nodes if str(n.id or "").strip()]
        if not pids:
            return snap

        async def _load_state(session) -> tuple[
            Equivalent,
            dict[str, Participant],
            dict[str, uuid.UUID],
            dict[tuple[uuid.UUID, uuid.UUID], Decimal],
            dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]],
        ] | None:
            eq = (await session.execute(select(Equivalent).where(Equivalent.code == eq_code))).scalar_one_or_none()
            if eq is None:
                return None

            p_rows = (await session.execute(select(Participant).where(Participant.pid.in_(pids)))).scalars().all()
            pid_to_rec = {p.pid: p for p in p_rows}
            pid_to_id = {p.pid: p.id for p in p_rows}
            participant_ids = [p.id for p in p_rows]
            if not participant_ids:
                return None

            # Debts: debtor -> creditor in DB
            debt_rows = (
                await session.execute(
                    select(
                        Debt.creditor_id,
                        Debt.debtor_id,
                        func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                    )
                    .where(
                        Debt.equivalent_id == eq.id,
                        Debt.creditor_id.in_(participant_ids),
                        Debt.debtor_id.in_(participant_ids),
                    )
                    .group_by(Debt.creditor_id, Debt.debtor_id)
                )
            ).all()
            debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {
                (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
            }

            # Trustlines: from (creditor) -> to (debtor)
            tl_rows = (
                await session.execute(
                    select(
                        TrustLine.from_participant_id,
                        TrustLine.to_participant_id,
                        TrustLine.limit,
                        TrustLine.status,
                    ).where(
                        TrustLine.equivalent_id == eq.id,
                        TrustLine.from_participant_id.in_(participant_ids),
                        TrustLine.to_participant_id.in_(participant_ids),
                    )
                )
            ).all()
            tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]] = {
                (r.from_participant_id, r.to_participant_id): (r.limit or Decimal("0"), r.status) for r in tl_rows
            }

            return eq, pid_to_rec, pid_to_id, debt_by_pair, tl_by_pair

        if session is None:
            async with db_session.AsyncSessionLocal() as s:
                loaded = await _load_state(s)
        else:
            loaded = await _load_state(session)

        if loaded is None:
            return snap

        eq, pid_to_rec, pid_to_id, debt_by_pair, tl_by_pair = loaded

        precision = int(getattr(eq, "precision", 2) or 2)
        scale10 = Decimal(10) ** precision
        money_quant = Decimal(1) / scale10

        def _to_money_str(v: Decimal) -> str:
            return format(v.quantize(money_quant, rounding=ROUND_DOWN), "f")

        def _parse_amount(v: object) -> float | None:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                x = float(v)
                return x if x == x else None
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, str):
                s = v.strip().replace(",", "")
                if not s:
                    return None
                try:
                    return float(s)
                except ValueError:
                    return None
            return None

        # Compute per-node totals from debt table.
        credit_sum: dict[uuid.UUID, Decimal] = {}
        debit_sum: dict[uuid.UUID, Decimal] = {}
        for (creditor_id, debtor_id), amt in debt_by_pair.items():
            if amt <= 0:
                continue
            credit_sum[creditor_id] = credit_sum.get(creditor_id, Decimal("0")) + amt
            debit_sum[debtor_id] = debit_sum.get(debtor_id, Decimal("0")) + amt

        # Links
        link_stats: list[tuple[SimulatorGraphLink, float | None, float | None]] = []
        for link in snap.links:
            src_pid = str(link.source or "").strip()
            dst_pid = str(link.target or "").strip()
            if not src_pid or not dst_pid:
                continue

            src_id = pid_to_id.get(src_pid)
            dst_id = pid_to_id.get(dst_pid)
            if src_id is None or dst_id is None:
                continue

            used_amt = debt_by_pair.get((src_id, dst_id), Decimal("0"))
            limit_amt: Decimal
            status: str | None
            if (src_id, dst_id) in tl_by_pair:
                limit_amt, status = tl_by_pair[(src_id, dst_id)]
            else:
                status = link.status
                try:
                    limit_amt = Decimal(str(link.trust_limit or "0"))
                except (InvalidOperation, ValueError):
                    limit_amt = Decimal("0")

            available_amt = limit_amt - used_amt
            if available_amt < 0:
                available_amt = Decimal("0")

            link.trust_limit = _to_money_str(limit_amt)
            link.used = _to_money_str(used_amt)
            link.available = _to_money_str(available_amt)
            if status is not None:
                link.status = str(status)

            link_stats.append((link, _parse_amount(limit_amt), _parse_amount(used_amt)))

        limits = sorted([x for _, x, _ in link_stats if x is not None])
        q33 = viz_rules.quantile(limits, 0.33) if limits else None
        q66 = viz_rules.quantile(limits, 0.66) if limits else None

        for link, limit_num, used_num in link_stats:
            status_key: str | None
            if isinstance(link.status, str):
                status_key = link.status.strip().lower() or None
            else:
                status_key = None
            link.viz_width_key = viz_rules.link_width_key(limit_num, q33=q33, q66=q66)
            link.viz_alpha_key = viz_rules.link_alpha_key(status_key, used=used_num, limit=limit_num)

        # Nodes: net + viz
        atoms_by_pid: dict[str, int] = {}
        mags: list[int] = []
        debt_mags: list[int] = []

        for node in snap.nodes:
            pid = str(node.id or "").strip()
            rec = pid_to_rec.get(pid)
            if rec is None:
                continue

            credit = credit_sum.get(rec.id, Decimal("0"))
            debit = debit_sum.get(rec.id, Decimal("0"))
            net = credit - debit

            atoms = net_decimal_to_atoms(net, precision=precision)
            node.net_balance_atoms = str(abs(atoms))
            node.net_sign = atoms_to_net_sign(atoms)
            # Backend-first: provide signed major-units value so the UI doesn't need to convert atoms.
            node.net_balance = _to_money_str(net)

            atoms_by_pid[pid] = atoms
            mags.append(abs(atoms))
            if atoms < 0:
                debt_mags.append(abs(atoms))

            # Prefer DB metadata for status/type if scenario snapshot lacks them.
            if not (node.status and str(node.status).strip()):
                if getattr(rec, "status", None) is not None:
                    node.status = str(rec.status)
            if not (node.type and str(node.type).strip()):
                if getattr(rec, "type", None) is not None:
                    node.type = str(rec.type)

        mags_sorted = sorted(mags)
        debt_mags_sorted = sorted(debt_mags)
        mn = len(mags_sorted)

        for node in snap.nodes:
            pid = str(node.id or "").strip()
            if not pid:
                continue

            atoms = atoms_by_pid.get(pid)
            if atoms is None:
                continue

            status_key = str(node.status or "").strip().lower()
            type_key = str(node.type or "").strip().lower()

            node.viz_color_key = viz_rules.node_color_key(
                atoms=atoms,
                status_key=status_key,
                type_key=type_key,
                debt_mags_sorted=debt_mags_sorted,
            )
            node.viz_shape_key = viz_rules.node_shape_key(type_key)
            w, h = viz_rules.node_size_wh(atoms_abs=abs(atoms), mags_sorted=mags_sorted, type_key=type_key)
            node.viz_size = SimulatorVizSize(w=float(w), h=float(h))

        return snap

    async def enrich_snapshot_from_db_if_enabled(
        self,
        snap: SimulatorGraphSnapshot,
        *,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        """Best-effort DB enrichment for snapshots.

        If DB is disabled, returns the snapshot unchanged.
        """

        if not self._db_enabled():
            return snap
        return await self._enrich_snapshot_from_db(snap, equivalent=equivalent, session=session)


def scenario_to_snapshot(raw: dict[str, Any], *, equivalent: str, utc_now) -> SimulatorGraphSnapshot:
    participants = raw.get("participants") or []
    trustlines = raw.get("trustlines") or []
    eq_norm = str(equivalent or "").strip().upper()

    # Nodes
    nodes: list[SimulatorGraphNode] = []
    for p in participants:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        node_type = str(p.get("type") or "person").strip().lower()
        # Backend-first: provide visual semantics explicitly.
        if node_type == "business":
            default_size = SimulatorVizSize(w=26.0, h=22.0)
            default_shape_key = "rounded-rect"
        else:
            default_size = SimulatorVizSize(w=16.0, h=16.0)
            default_shape_key = "circle"
        nodes.append(
            SimulatorGraphNode(
                id=pid,
                name=p.get("name"),
                type=p.get("type"),
                status=p.get("status"),
                viz_color_key=_node_color_key(p),
                viz_shape_key=default_shape_key,
                viz_size=default_size,
            )
        )

    # Links (only those matching equivalent)
    links: list[SimulatorGraphLink] = []
    for tl in trustlines:
        tl_eq = effective_equivalent(scenario=raw, payload=(tl or {}))
        if tl_eq != eq_norm:
            continue
        src = str(tl.get("from") or "")
        dst = str(tl.get("to") or "")
        if not src or not dst:
            continue
        limit = tl.get("limit")
        links.append(
            SimulatorGraphLink(
                source=src,
                target=dst,
                trust_limit=limit,
                used=0,
                available=limit,
                status="active",
                viz_width_key="thin",
                viz_alpha_key="active",
            )
        )

    # links_count
    counts: dict[str, int] = {}
    for l in links:
        counts[l.source] = counts.get(l.source, 0) + 1
        counts[l.target] = counts.get(l.target, 0) + 1
    for n in nodes:
        n.links_count = counts.get(n.id)

    return SimulatorGraphSnapshot(
        api_version=SIMULATOR_API_VERSION,
        equivalent=str(equivalent),
        generated_at=utc_now(),
        nodes=nodes,
        links=links,
        palette=None,
        limits=None,
    )


def _node_color_key(p: dict[str, Any]) -> Optional[str]:
    t = str(p.get("type") or "").strip()
    status = str(p.get("status") or "").strip()
    if status in {"suspended", "left", "deleted"}:
        return status
    if t in {"business", "person"}:
        return t
    return None
