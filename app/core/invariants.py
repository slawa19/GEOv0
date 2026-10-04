from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Mapping, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException

#: A directed debt: (equivalent_id, debtor_id, creditor_id).
DebtEdge = Tuple[UUID, UUID, UUID]

#: What the snapshot says about growth (026 `T2601`, fork 4): it cannot see the state before an
#: operation, so it cannot prove that no debt grew - growth is checked on the write path only.
GROWTH_NOT_VERIFIED_BY_SNAPSHOT: Dict[str, str] = {
    "status": "not_verified",
    "reason": "requires_operation_prestate",
}


def _supporting_line():
    """The creditor's live line toward the debtor of a `Debt` row, at its stored limit (T1543).

    An `active` line supports a debt (since 028 `F-028-29` the only live status); a `closed` or missing line
    supports none (limit 0).
    """

    return and_(
        TrustLine.from_participant_id == Debt.creditor_id,
        TrustLine.to_participant_id == Debt.debtor_id,
        TrustLine.equivalent_id == Debt.equivalent_id,
        TrustLine.status == "active",
    )


def _limit_item(row, before: Optional[Decimal] = None) -> dict:
    limit = row.trust_limit or Decimal("0")
    item = {
        "debtor_id": str(row.debtor_id),
        "creditor_id": str(row.creditor_id),
        "equivalent_id": str(row.equivalent_id),
        "debt_amount": str(row.debt_amount),
        "trust_limit": str(limit),
        "violation_amount": str(row.debt_amount - limit),
    }
    if before is not None:
        item["debt_before"] = format(before, "f")  # a decimal string: `str` spells a zero of scale 8 `0E-8`
    return item


def _trust_limit_violation(message: str, violations: List[dict]) -> IntegrityViolationException:
    return IntegrityViolationException(
        message, details={"invariant": "TRUST_LIMIT_VIOLATION", "violations": violations}
    )


class InvariantChecker:
    """Checks protocol invariants against persisted state."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def check_debt_growth(self, before: Mapping[DebtEdge, Decimal]) -> List[dict]:
        """THE growth rule (026 В3, `T2601`): no directed debt grows above its creditor's limit.

        For every directed debt in `before` - its amount before the operation - the current amount
        `after` must satisfy `after > before => after <= limit` (the supporting line's stored limit,
        0 without one). A debt already above the limit may shrink or stay. Per DIRECTED debt, never
        per `abs(net)`: repaying one direction and creating the reverse lowers `|net|` and is still
        growth of the reverse debt. ONE SELECT over the named debts. The two write-path callers:
        `PaymentService._apply_payment` (`before` = the payment's prestate) and `Book`'s completion
        (`before` = the journal's first `amount_before`), which also covers direct `Book` calls.

        Returns the checked growth transitions (`debt_before`, `debt_amount`, `trust_limit` of every debt that
        grew) for the operation's audit metadata (026 `T2603.1`, the S1 §15 P3) - a diagnostic of THIS
        operation, not a proof about older ones.
        """

        if not before:
            return []
        named = [and_(Debt.equivalent_id == e, Debt.debtor_id == d, Debt.creditor_id == c) for e, d, c in before]
        query = select(Debt.equivalent_id, Debt.debtor_id, Debt.creditor_id, Debt.amount.label("debt_amount"),
                       TrustLine.limit.label("trust_limit"))
        rows = (await self.session.execute(
            query.select_from(Debt).outerjoin(TrustLine, _supporting_line()).where(or_(*named)))).all()
        grown, violations = [], []
        for row in rows:
            was = before[(row.equivalent_id, row.debtor_id, row.creditor_id)]
            if row.debt_amount > was:
                item = _limit_item(row, was)
                if row.debt_amount > (row.trust_limit or Decimal("0")):
                    violations.append(item)
                grown.append({k: v for k, v in item.items() if k != "violation_amount"})
        if violations:
            raise _trust_limit_violation(
                f"Trust limit exceeded: {len(violations)} debt(s) grew above the limit", violations
            )
        return grown

    async def check_trust_limits(
        self,
        *,
        equivalent_id: Optional[UUID] = None,
        participant_pairs: Optional[List[Tuple[UUID, UUID]]] = None,
    ) -> List[dict]:
        """The SNAPSHOT side of the trust limit (026 `T2601`, fork 4): observation, not proof.

        A debt above the stored limit of its supporting live line (`active`) is ALLOWED - the
        limit was lowered under it (owner, В3) - and is returned as an `over_limit_allowed` entry with
        debt, limit and excess. A snapshot cannot see growth (`GROWTH_NOT_VERIFIED_BY_SNAPSHOT`); that is
        `check_debt_growth`'s, on the write path. A debt with NO supporting live line (closed or
        missing) is structural and still raises `TRUST_LIMIT_VIOLATION` against a limit of 0.
        """

        tl = TrustLine

        query = (
            select(
                Debt.debtor_id,
                Debt.creditor_id,
                Debt.equivalent_id,
                Debt.amount.label("debt_amount"),
                tl.id.label("line_id"),
                tl.limit.label("trust_limit"),
            )
            .select_from(Debt)
            .outerjoin(tl, _supporting_line())
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

        order = (Debt.equivalent_id, Debt.debtor_id, Debt.creditor_id)
        rows = (await self.session.execute(query.order_by(*order))).all()
        allowed = []
        for row in rows:
            if row.line_id is not None:
                item = _limit_item(row)
                item["excess"] = item.pop("violation_amount")
                allowed.append(item)
        violations = [_limit_item(row) for row in rows if row.line_id is None]
        if violations:
            # The allowed excess of other pairs stays observable beside the structural violation.
            exc = _trust_limit_violation(
                f"Debt without a supporting live trust line: {len(violations)} debt(s)", violations
            )
            exc.over_limit_allowed = allowed
            raise exc
        return allowed

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

    async def _calculate_net_position(
        self, participant_id: UUID, equivalent_id: UUID, pairs: Optional[set] = None
    ) -> Decimal:
        """Compute participant net position = credits - debts; over the directed debts `pairs` (`(debtor,
        creditor)`) only, when given - an operation's own rows, which its line locks hold (027 stage 2)."""

        scope = [] if pairs is None else [or_(false(), *(and_(Debt.debtor_id == d, Debt.creditor_id == c)
                                                         for d, c in pairs))]
        credits = (
            await self.session.execute(
                select(func.coalesce(func.sum(Debt.amount), Decimal("0"))).where(
                    Debt.creditor_id == participant_id,
                    Debt.equivalent_id == equivalent_id,
                    *scope,
                )
            )
        ).scalar_one()

        debts = (
            await self.session.execute(
                select(func.coalesce(func.sum(Debt.amount), Decimal("0"))).where(
                    Debt.debtor_id == participant_id,
                    Debt.equivalent_id == equivalent_id,
                    *scope,
                )
            )
        ).scalar_one()

        return (credits or Decimal("0")) - (debts or Decimal("0"))

    async def verify_clearing_neutrality(
        self,
        cycle_participant_ids: List[UUID],
        equivalent_id: UUID,
        positions_before: Dict[UUID, Decimal],
        pairs: Optional[set] = None,
    ) -> bool:
        """Verify that clearing didn't change net positions for cycle participants (over `pairs`, as read before)."""

        violations: List[dict] = []
        for pid in cycle_participant_ids:
            position_after = await self._calculate_net_position(pid, equivalent_id, pairs)
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