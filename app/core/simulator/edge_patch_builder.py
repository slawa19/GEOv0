from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.core.simulator import viz_rules
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.models import RunRecord


class EdgePatchBuilder:
    def __init__(self, *, logger: logging.Logger) -> None:
        self._logger = logger

    async def build_edge_patch_for_equivalent(
        self,
        *,
        session,
        run: RunRecord,
        equivalent_code: str,
        only_edges: set[tuple[str, str]] | None = None,
        include_width_keys: bool = True,
    ) -> list[dict[str, Any]]:
        """Compute backend-authoritative edge patches from DB state.

        - When *only_edges* is provided, returns patches only for those (source_pid, target_pid).
        - When *include_width_keys* is False, skips width-key recomputation (useful when limits don't change).
        """

        eq_upper = str(equivalent_code or "").strip().upper()
        if not eq_upper:
            return []

        pid_pairs: set[tuple[str, str]] | None = None
        if only_edges:
            pid_pairs = {
                (str(s).strip(), str(d).strip())
                for s, d in only_edges
                if str(s).strip() and str(d).strip()
            }
            if not pid_pairs:
                return []

        uuid_to_pid: dict[uuid.UUID, str] = {
            uid: pid for uid, pid in (run._real_participants or []) if pid
        }

        eq_row = (
            await session.execute(
                select(Equivalent.id, Equivalent.precision).where(Equivalent.code == eq_upper)
            )
        ).one_or_none()
        if not eq_row:
            return []
        eq_id = eq_row[0]
        precision = int(eq_row[1] or 2)

        scale10 = Decimal(10) ** precision
        money_quant = Decimal(1) / scale10

        def _to_money_str(v: Decimal) -> str:
            return format(v.quantize(money_quant, rounding=ROUND_DOWN), "f")

        # Load trustlines for this equivalent.
        tl_rows = (
            await session.execute(
                select(
                    TrustLine.from_participant_id,
                    TrustLine.to_participant_id,
                    TrustLine.limit,
                    TrustLine.status,
                ).where(TrustLine.equivalent_id == eq_id)
            )
        ).all()

        # Load aggregated debts for this equivalent.
        debt_rows = (
            await session.execute(
                select(
                    Debt.creditor_id,
                    Debt.debtor_id,
                    func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                )
                .where(Debt.equivalent_id == eq_id)
                .group_by(Debt.creditor_id, Debt.debtor_id)
            )
        ).all()
        debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {
            (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
        }

        # Precompute quantiles for width keys (limits only).
        q33: float | None = None
        q66: float | None = None
        if include_width_keys:
            limits: list[float] = []
            for r in tl_rows:
                try:
                    limits.append(float(r.limit or 0))
                except Exception:
                    continue
            limits = sorted([x for x in limits if x == x])
            q33 = viz_rules.quantile(limits, 0.33) if limits else None
            q66 = viz_rules.quantile(limits, 0.66) if limits else None

        patches: list[dict[str, Any]] = []
        for r in tl_rows:
            src_pid = uuid_to_pid.get(r.from_participant_id)
            dst_pid = uuid_to_pid.get(r.to_participant_id)
            if not src_pid or not dst_pid:
                continue
            if pid_pairs is not None and (src_pid, dst_pid) not in pid_pairs:
                continue

            try:
                limit_amt = Decimal(str(r.limit or 0))
            except Exception:
                limit_amt = Decimal("0")

            used_amt = debt_by_pair.get((r.from_participant_id, r.to_participant_id), Decimal("0"))
            try:
                used_amt = Decimal(str(used_amt or 0))
            except Exception:
                used_amt = Decimal("0")

            avail_amt = limit_amt - used_amt
            if avail_amt < 0:
                avail_amt = Decimal("0")

            try:
                limit_num = float(limit_amt)
            except Exception:
                limit_num = None
            try:
                used_num = float(used_amt)
            except Exception:
                used_num = None

            status_key: str | None
            if isinstance(r.status, str):
                status_key = r.status.strip().lower() or None
            else:
                status_key = None

            p: dict[str, Any] = {
                "source": str(src_pid),
                "target": str(dst_pid),
                "trust_limit": _to_money_str(limit_amt),
                "used": _to_money_str(used_amt),
                "available": _to_money_str(avail_amt),
                "viz_alpha_key": viz_rules.link_alpha_key(status_key, used=used_num, limit=limit_num),
            }
            if include_width_keys:
                p["viz_width_key"] = viz_rules.link_width_key(limit_num, q33=q33, q66=q66)

            patches.append(p)

        return patches

    async def build_edge_patch_for_pairs(
        self,
        *,
        session,
        helper: VizPatchHelper,
        edges_pairs: list[tuple[str, str]],
        pid_to_participant: dict[str, Participant],
    ) -> list[dict[str, Any]]:
        """Compute a minimal edge_patch for specific edges using VizPatchHelper.

        This is used by per-tx and clearing.done patch codepaths and intentionally
        mirrors the existing RealRunner inline logic (used/available + viz_* keys).
        """

        if not edges_pairs:
            return []

        id_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
        for src_pid, dst_pid in edges_pairs:
            src_part = pid_to_participant.get(src_pid)
            dst_part = pid_to_participant.get(dst_pid)
            if not src_part or not dst_part:
                continue
            id_pairs.append((src_part.id, dst_part.id))

        debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
        tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]] = {}

        if id_pairs:
            debt_cond = or_(
                *[and_(Debt.creditor_id == a, Debt.debtor_id == b) for a, b in id_pairs]
            )
            debt_rows = (
                await session.execute(
                    select(
                        Debt.creditor_id,
                        Debt.debtor_id,
                        func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                    )
                    .where(
                        Debt.equivalent_id == helper.equivalent_id,
                        debt_cond,
                    )
                    .group_by(Debt.creditor_id, Debt.debtor_id)
                )
            ).all()
            debt_by_pair = {
                (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
            }

            tl_cond = or_(
                *[
                    and_(
                        TrustLine.from_participant_id == a,
                        TrustLine.to_participant_id == b,
                    )
                    for a, b in id_pairs
                ]
            )
            tl_rows = (
                await session.execute(
                    select(
                        TrustLine.from_participant_id,
                        TrustLine.to_participant_id,
                        TrustLine.limit,
                        TrustLine.status,
                    ).where(
                        TrustLine.equivalent_id == helper.equivalent_id,
                        tl_cond,
                    )
                )
            ).all()
            tl_by_pair = {
                (r.from_participant_id, r.to_participant_id): (
                    (r.limit or Decimal("0")),
                    (str(r.status) if r.status is not None else None),
                )
                for r in tl_rows
            }

        edge_patch_list: list[dict[str, Any]] = []
        for src_pid, dst_pid in edges_pairs:
            src_part = pid_to_participant.get(src_pid)
            dst_part = pid_to_participant.get(dst_pid)
            if not src_part or not dst_part:
                continue

            used_amt = debt_by_pair.get((src_part.id, dst_part.id), Decimal("0"))
            limit_amt, tl_status = tl_by_pair.get((src_part.id, dst_part.id), (Decimal("0"), None))
            available_amt = max(Decimal("0"), limit_amt - used_amt)

            edge_viz = helper.edge_viz(status=tl_status, used=used_amt, limit=limit_amt)
            edge_patch_list.append(
                {
                    "source": src_pid,
                    "target": dst_pid,
                    "used": str(used_amt),
                    "available": str(available_amt),
                    **edge_viz,
                }
            )

        return edge_patch_list
