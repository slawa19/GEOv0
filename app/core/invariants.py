from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException


class InvariantChecker:
    """Checks protocol invariants against persisted state."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def check_zero_sum(self, *, equivalent_id: Optional[UUID] = None) -> Dict[UUID, Decimal]:
        """Check zero-sum invariant per equivalent.

        In the current Debt edge model, the sum of all participant net balances is expected
        to be algebraically zero; this check serves as a smoke-test for inconsistency.
        """

        if equivalent_id is not None:
            imbalance = await self._compute_imbalance(equivalent_id)
            if imbalance != Decimal("0"):
                raise IntegrityViolationException(
                    "Zero-sum invariant violated for equivalent",
                    details={
                        "invariant": "ZERO_SUM_VIOLATION",
                        "violations": {str(equivalent_id): str(imbalance)},
                    },
                )
            return {}

        eq_ids = (
            await self.session.execute(select(Debt.equivalent_id).distinct())
        ).scalars().all()

        violations: Dict[UUID, Decimal] = {}
        for eq_id in eq_ids:
            imbalance = await self._compute_imbalance(eq_id)
            if imbalance != Decimal("0"):
                violations[eq_id] = imbalance

        if violations:
            raise IntegrityViolationException(
                f"Zero-sum invariant violated for {len(violations)} equivalent(s)",
                details={
                    "invariant": "ZERO_SUM_VIOLATION",
                    "violations": {str(k): str(v) for k, v in violations.items()},
                },
            )

        return {}

    async def _compute_imbalance(self, equivalent_id: UUID) -> Decimal:
        credits_query = (
            select(Debt.creditor_id, func.sum(Debt.amount).label("total"))
            .where(Debt.equivalent_id == equivalent_id)
            .group_by(Debt.creditor_id)
        )
        debts_query = (
            select(Debt.debtor_id, func.sum(Debt.amount).label("total"))
            .where(Debt.equivalent_id == equivalent_id)
            .group_by(Debt.debtor_id)
        )

        credits_rows = (await self.session.execute(credits_query)).all()
        debts_rows = (await self.session.execute(debts_query)).all()

        credits = {pid: (total or Decimal("0")) for pid, total in credits_rows}
        debts = {pid: (total or Decimal("0")) for pid, total in debts_rows}

        all_participants = set(credits.keys()) | set(debts.keys())
        total = Decimal("0")
        for pid in all_participants:
            total += (credits.get(pid, Decimal("0")) - debts.get(pid, Decimal("0")))

        return total

    async def check_trust_limits(
        self,
        *,
        equivalent_id: Optional[UUID] = None,
        participant_pairs: Optional[List[Tuple[UUID, UUID]]] = None,
    ) -> List[dict]:
        """Check trust limit invariant.

        Invariant: debt[debtor→creditor, E] ≤ trustline[creditor→debtor, E].limit
        (active trustline only; missing trustline treated as limit 0).
        """

        tl = TrustLine

        query = (
            select(
                Debt.debtor_id,
                Debt.creditor_id,
                Debt.equivalent_id,
                Debt.amount.label("debt_amount"),
                func.coalesce(tl.limit, Decimal("0")).label("trust_limit"),
            )
            .select_from(Debt)
            .outerjoin(
                tl,
                and_(
                    tl.from_participant_id == Debt.creditor_id,
                    tl.to_participant_id == Debt.debtor_id,
                    tl.equivalent_id == Debt.equivalent_id,
                    tl.status == "active",
                ),
            )
            .where(Debt.amount > func.coalesce(tl.limit, Decimal("0")))
        )

        if equivalent_id is not None:
            query = query.where(Debt.equivalent_id == equivalent_id)

        if participant_pairs:
            pair_conditions = [
                and_(Debt.debtor_id == debtor_id, Debt.creditor_id == creditor_id)
                for debtor_id, creditor_id in participant_pairs
            ]
            query = query.where(or_(*pair_conditions))

        rows = (await self.session.execute(query)).all()
        violations: List[dict] = []

        for row in rows:
            violations.append(
                {
                    "debtor_id": str(row.debtor_id),
                    "creditor_id": str(row.creditor_id),
                    "equivalent_id": str(row.equivalent_id),
                    "debt_amount": str(row.debt_amount),
                    "trust_limit": str(row.trust_limit),
                    "violation_amount": str(row.debt_amount - row.trust_limit),
                }
            )

        if violations:
            raise IntegrityViolationException(
                f"Trust limit exceeded for {len(violations)} debt(s)",
                details={"invariant": "TRUST_LIMIT_VIOLATION", "violations": violations},
            )

        return []

    async def check_debt_symmetry(
        self,
        *,
        equivalent_id: Optional[UUID] = None,
        participant_pairs: Optional[List[tuple[UUID, UUID]]] = None,
    ) -> List[dict]:
        """Check debt symmetry invariant.

        Invariant: NOT (debt[A→B, E] > 0 AND debt[B→A, E] > 0)
        """

        d1 = aliased(Debt, name="d1")
        d2 = aliased(Debt, name="d2")

        query = (
            select(
                d1.debtor_id.label("participant_a"),
                d1.creditor_id.label("participant_b"),
                d1.equivalent_id,
                d1.amount.label("debt_a_to_b"),
                d2.amount.label("debt_b_to_a"),
            )
            .select_from(d1)
            .join(
                d2,
                and_(
                    d1.debtor_id == d2.creditor_id,
                    d1.creditor_id == d2.debtor_id,
                    d1.equivalent_id == d2.equivalent_id,
                ),
            )
            .where(
                and_(
                    d1.amount > 0,
                    d2.amount > 0,
                    d1.debtor_id < d1.creditor_id,
                )
            )
        )

        if equivalent_id is not None:
            query = query.where(d1.equivalent_id == equivalent_id)

        if participant_pairs:
            # Limit the symmetry check to pairs relevant for the current operation.
            # This avoids failing a payment because of unrelated pre-existing debt
            # symmetry violations elsewhere in the graph.
            pair_conds = []
            for a, b in participant_pairs:
                pair_conds.append(
                    or_(
                        and_(d1.debtor_id == a, d1.creditor_id == b),
                        and_(d1.debtor_id == b, d1.creditor_id == a),
                    )
                )
            if pair_conds:
                query = query.where(or_(*pair_conds))

        rows = (await self.session.execute(query)).all()
        violations: List[dict] = []
        for row in rows:
            debt_a = Decimal(str(row.debt_a_to_b))
            debt_b = Decimal(str(row.debt_b_to_a))
            violations.append(
                {
                    "participant_a": str(row.participant_a),
                    "participant_b": str(row.participant_b),
                    "equivalent_id": str(row.equivalent_id),
                    "debt_a_to_b": str(row.debt_a_to_b),
                    "debt_b_to_a": str(row.debt_b_to_a),
                    "net_debt": str(abs(debt_a - debt_b)),
                }
            )

        if violations:
            raise IntegrityViolationException(
                f"Mutual debts found for {len(violations)} pair(s)",
                details={"invariant": "DEBT_SYMMETRY_VIOLATION", "violations": violations},
            )

        return []

    async def _calculate_net_position(self, participant_id: UUID, equivalent_id: UUID) -> Decimal:
        """Compute participant net position = credits - debts."""

        credits = (
            await self.session.execute(
                select(func.coalesce(func.sum(Debt.amount), Decimal("0"))).where(
                    Debt.creditor_id == participant_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
        ).scalar_one()

        debts = (
            await self.session.execute(
                select(func.coalesce(func.sum(Debt.amount), Decimal("0"))).where(
                    Debt.debtor_id == participant_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
        ).scalar_one()

        return (credits or Decimal("0")) - (debts or Decimal("0"))

    async def verify_clearing_neutrality(
        self,
        cycle_participant_ids: List[UUID],
        equivalent_id: UUID,
        positions_before: Dict[UUID, Decimal],
    ) -> bool:
        """Verify that clearing didn't change net positions for cycle participants."""

        violations: List[dict] = []
        for pid in cycle_participant_ids:
            position_after = await self._calculate_net_position(pid, equivalent_id)
            position_before = positions_before.get(pid, Decimal("0"))

            if position_before != position_after:
                violations.append(
                    {
                        "participant_id": str(pid),
                        "before": str(position_before),
                        "after": str(position_after),
                        "delta": str(position_after - position_before),
                    }
                )

        if violations:
            raise IntegrityViolationException(
                f"Clearing changed net positions for {len(violations)} participant(s)",
                details={"invariant": "CLEARING_NEUTRALITY_VIOLATION", "violations": violations},
            )

        return True