from __future__ import annotations

import bisect
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from sqlalchemy import func, select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@dataclass
class VizPatchHelper:
    """Best-effort incremental viz_* calculator for real-mode SSE patches.

    This mirrors the logic in SnapshotBuilder, but avoids full snapshot rebuilds.
    Quantiles are cached and refreshed periodically; node patches are computed
    on-demand for affected participants.
    """

    equivalent_code: str
    equivalent_id: uuid.UUID
    precision: int
    refresh_every_ticks: int = 10

    _mags_sorted: list[int] = None  # type: ignore[assignment]
    _debt_mags_sorted: list[int] = None  # type: ignore[assignment]
    _limit_q33: float | None = None
    _limit_q66: float | None = None
    _last_refresh_tick: int = -10**9

    def __post_init__(self) -> None:
        if self._mags_sorted is None:
            self._mags_sorted = []
        if self._debt_mags_sorted is None:
            self._debt_mags_sorted = []

    @classmethod
    async def create(
        cls,
        session,
        *,
        equivalent_code: str,
        refresh_every_ticks: int = 10,
    ) -> "VizPatchHelper":
        eq_code = str(equivalent_code or "").strip().upper()
        eq = (await session.execute(select(Equivalent).where(Equivalent.code == eq_code))).scalar_one_or_none()
        if eq is None:
            raise ValueError(f"Equivalent {eq_code} not found")
        precision = int(getattr(eq, "precision", 2) or 2)
        r = int(refresh_every_ticks or 0)
        if r <= 0:
            r = 10
        return cls(
            equivalent_code=eq_code,
            equivalent_id=eq.id,
            precision=precision,
            refresh_every_ticks=r,
        )

    async def maybe_refresh_quantiles(self, session, *, tick_index: int, participant_ids: list[uuid.UUID]) -> None:
        if not participant_ids:
            return
        if tick_index - self._last_refresh_tick < self.refresh_every_ticks:
            return

        scale10 = Decimal(10) ** int(self.precision)

        debt_rows = (
            await session.execute(
                select(
                    Debt.creditor_id,
                    Debt.debtor_id,
                    func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                )
                .where(
                    Debt.equivalent_id == self.equivalent_id,
                    Debt.creditor_id.in_(participant_ids),
                    Debt.debtor_id.in_(participant_ids),
                )
                .group_by(Debt.creditor_id, Debt.debtor_id)
            )
        ).all()

        credit_sum: dict[uuid.UUID, Decimal] = {}
        debit_sum: dict[uuid.UUID, Decimal] = {}
        for r in debt_rows:
            amt = r.amount or Decimal("0")
            if amt <= 0:
                continue
            credit_sum[r.creditor_id] = credit_sum.get(r.creditor_id, Decimal("0")) + amt
            debit_sum[r.debtor_id] = debit_sum.get(r.debtor_id, Decimal("0")) + amt

        mags: list[int] = []
        debt_mags: list[int] = []
        for pid in participant_ids:
            net = credit_sum.get(pid, Decimal("0")) - debit_sum.get(pid, Decimal("0"))
            atoms = int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))
            mag = abs(atoms)
            mags.append(mag)
            if atoms < 0:
                debt_mags.append(mag)

        self._mags_sorted = sorted(mags)
        self._debt_mags_sorted = sorted(debt_mags)

        limit_rows = (
            await session.execute(
                select(TrustLine.limit)
                .where(
                    TrustLine.equivalent_id == self.equivalent_id,
                    TrustLine.from_participant_id.in_(participant_ids),
                    TrustLine.to_participant_id.in_(participant_ids),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
        limits = sorted([float((r[0] or Decimal("0"))) for r in limit_rows if r[0] is not None])

        def _quantile(values_sorted: list[float], p: float) -> float | None:
            if not values_sorted:
                return None
            if p <= 0:
                return values_sorted[0]
            if p >= 1:
                return values_sorted[-1]
            n = len(values_sorted)
            i = int(p * (n - 1))
            return values_sorted[i]

        self._limit_q33 = _quantile(limits, 0.33)
        self._limit_q66 = _quantile(limits, 0.66)

        self._last_refresh_tick = int(tick_index)

    def _percentile_rank_int(self, sorted_mags: list[int], mag: int) -> float:
        n = len(sorted_mags)
        if n <= 1:
            return 0.0
        i = bisect.bisect_right(sorted_mags, mag) - 1
        i = max(0, min(i, n - 1))
        return i / (n - 1)

    def _scale_from_pct(self, pct: float, *, max_scale: float = 1.90, gamma: float = 0.75) -> float:
        if pct <= 0:
            return 1.0
        if pct >= 1:
            return max_scale
        return 1.0 + (max_scale - 1.0) * (pct**gamma)

    def _debt_bin(self, mag: int) -> int:
        DEBT_BINS = 9
        dn = len(self._debt_mags_sorted)
        if dn <= 1:
            return 0
        i = bisect.bisect_right(self._debt_mags_sorted, mag) - 1
        i = max(0, min(i, dn - 1))
        pct = i / (dn - 1)
        b = int(round(pct * (DEBT_BINS - 1)))
        return max(0, min(b, DEBT_BINS - 1))

    def _node_color_key(self, *, atoms: int, status: str | None, type_: str | None) -> str:
        status_key = str(status or "").strip().lower()
        type_key = str(type_ or "").strip().lower()

        if status_key in {"suspended", "frozen"}:
            return "suspended"
        if status_key == "left":
            return "left"
        if status_key in {"deleted", "banned"}:
            return "deleted"

        if atoms < 0:
            return f"debt-{self._debt_bin(abs(atoms))}"
        return "business" if type_key == "business" else "person"

    def _node_size(self, *, atoms: int, type_: str | None) -> dict[str, int]:
        type_key = str(type_ or "").strip().lower()
        pct = self._percentile_rank_int(self._mags_sorted, abs(atoms)) if self._mags_sorted else 0.0
        s = self._scale_from_pct(pct)
        if type_key == "business":
            w0, h0 = 26, 22
        else:
            w0, h0 = 16, 16
        return {"w": int(round(w0 * s)), "h": int(round(h0 * s))}

    def edge_viz(self, *, status: str | None, used: Decimal, limit: Decimal) -> dict[str, str]:
        status_key = str(status or "").strip().lower() or None

        # Mirror SnapshotBuilder._link_alpha_key
        if status_key and status_key != "active":
            alpha = "muted"
        elif limit <= 0:
            alpha = "bg"
        else:
            try:
                r = float(abs(used) / limit)
            except Exception:
                r = 0.0
            if r >= 0.75:
                alpha = "hi"
            elif r >= 0.40:
                alpha = "active"
            elif r >= 0.15:
                alpha = "muted"
            else:
                alpha = "bg"

        # Mirror SnapshotBuilder._link_width_key
        limit_num: float | None
        try:
            limit_num = float(limit)
        except Exception:
            limit_num = None

        if limit_num is None or self._limit_q33 is None or self._limit_q66 is None:
            width = "hairline"
        elif limit_num <= self._limit_q33:
            width = "thin"
        elif limit_num <= self._limit_q66:
            width = "mid"
        else:
            width = "thick"

        return {"viz_alpha_key": alpha, "viz_width_key": width}

    async def compute_node_patches(
        self,
        session,
        *,
        pid_to_participant: dict[str, Participant],
        pids: Iterable[str],
    ) -> list[dict[str, Any]]:
        pid_list = [str(pid).strip() for pid in pids if str(pid).strip()]
        if not pid_list:
            return []

        # Compute net balances for these participants in 2 grouped queries.
        part_ids = [pid_to_participant[pid].id for pid in pid_list if pid in pid_to_participant]
        if not part_ids:
            return []

        credit_rows = (
            await session.execute(
                select(Debt.creditor_id, func.coalesce(func.sum(Debt.amount), 0).label("amount"))
                .where(Debt.equivalent_id == self.equivalent_id, Debt.creditor_id.in_(part_ids))
                .group_by(Debt.creditor_id)
            )
        ).all()
        debit_rows = (
            await session.execute(
                select(Debt.debtor_id, func.coalesce(func.sum(Debt.amount), 0).label("amount"))
                .where(Debt.equivalent_id == self.equivalent_id, Debt.debtor_id.in_(part_ids))
                .group_by(Debt.debtor_id)
            )
        ).all()

        credit_sum = {r.creditor_id: (r.amount or Decimal("0")) for r in credit_rows}
        debit_sum = {r.debtor_id: (r.amount or Decimal("0")) for r in debit_rows}

        scale10 = Decimal(10) ** int(self.precision)
        out: list[dict[str, Any]] = []
        for pid in pid_list:
            p = pid_to_participant.get(pid)
            if p is None:
                continue

            net = credit_sum.get(p.id, Decimal("0")) - debit_sum.get(p.id, Decimal("0"))
            atoms = int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))
            if atoms < 0:
                net_sign: int = -1
            elif atoms > 0:
                net_sign = 1
            else:
                net_sign = 0

            out.append(
                {
                    "id": pid,
                    "net_balance_atoms": str(atoms),
                    "net_sign": net_sign,
                    "viz_color_key": self._node_color_key(atoms=atoms, status=getattr(p, "status", None), type_=getattr(p, "type", None)),
                    "viz_size": self._node_size(atoms=atoms, type_=getattr(p, "type", None)),
                }
            )

        return out
