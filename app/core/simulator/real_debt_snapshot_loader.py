from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent


class RealDebtSnapshotLoader:
    async def load_debt_snapshot_by_pid(
        self,
        *,
        session: Any,
        participants: list[tuple[uuid.UUID, str]],
        equivalents: list[str],
    ) -> dict[tuple[str, str, str], Decimal]:
        """Load current debt amounts keyed by (debtor_pid, creditor_pid, eq_code).

        Used by real-mode payment planning to compute capacity-aware payment amounts.

        Returns
        -------
        dict[(str, str, str), Decimal]
            Mapping from ``(debtor_pid, creditor_pid, eq_code_UPPER)`` to the
            current debt amount on that edge.
        """
        if not participants or not equivalents:
            return {}

        uuid_to_pid: dict[uuid.UUID, str] = {uid: pid for (uid, pid) in participants}
        participant_uuids = list(uuid_to_pid.keys())

        eq_codes_upper = [str(x).strip().upper() for x in equivalents if str(x).strip()]
        if not eq_codes_upper:
            return {}

        # Equivalent UUID → code mapping (one lightweight query).
        eq_rows = (
            await session.execute(
                select(Equivalent.id, Equivalent.code).where(
                    Equivalent.code.in_(eq_codes_upper)
                )
            )
        ).all()
        eq_uuid_to_code: dict[uuid.UUID, str] = {row.id: row.code for row in eq_rows}
        if not eq_uuid_to_code:
            return {}

        eq_uuids = list(eq_uuid_to_code.keys())

        # Single aggregate query for all relevant debts.
        debt_rows = (
            await session.execute(
                select(
                    Debt.debtor_id,
                    Debt.creditor_id,
                    Debt.equivalent_id,
                    func.sum(Debt.amount).label("total"),
                )
                .where(
                    Debt.debtor_id.in_(participant_uuids),
                    Debt.creditor_id.in_(participant_uuids),
                    Debt.equivalent_id.in_(eq_uuids),
                )
                .group_by(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id)
            )
        ).all()

        snapshot: dict[tuple[str, str, str], Decimal] = {}
        for row in debt_rows:
            debtor_pid = uuid_to_pid.get(row.debtor_id)
            creditor_pid = uuid_to_pid.get(row.creditor_id)
            eq_code = eq_uuid_to_code.get(row.equivalent_id)
            if debtor_pid and creditor_pid and eq_code:
                snapshot[(debtor_pid, creditor_pid, eq_code)] = Decimal(
                    str(row.total or 0)
                )

        return snapshot
