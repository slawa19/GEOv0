import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.types import MoneyNumeric, finite_money_clauses

#: `limit` is a reserved word in PostgreSQL, so every SQL text that names the column quotes it.
_LIMIT_SQL = '"limit"'

class TrustLine(Base):
    __tablename__ = "trust_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_participant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    to_participant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    equivalent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('equivalents.id', ondelete='CASCADE'), nullable=False, index=True)
    # `MoneyNumeric` - T1526, the same hole as `debts.amount`: `"limit" >= 0` is TRUE for `NaN`
    # on PostgreSQL, and a `NaN` limit makes every capacity computed from it `NaN`.
    limit: Mapped[Decimal] = mapped_column(MoneyNumeric(20, 8), nullable=False)
    policy: Mapped[dict | None] = mapped_column(JSON, default=lambda: {
        'auto_clearing': True,
        'can_be_intermediate': True,
        'max_hop_usage': None,
        'daily_limit': None,
        'blocked_participants': []
    })
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='active', index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    from_participant = relationship("Participant", foreign_keys=[from_participant_id])
    to_participant = relationship("Participant", foreign_keys=[to_participant_id])
    equivalent = relationship("Equivalent")

    __table_args__ = (
        # Uniqueness holds among LIVE rows only, matching the protocol precondition of
        # TRUST_LINE_CREATE: «Не существует активной линии (from, to, equivalent)»
        # (docs/ru/02-protocol-spec.md:333).  Closing keeps the row (`:379`), so a closed
        # incarnation must not block a new one — see migration
        # 019_trust_lines_partial_unique_live and spec 009 (F-009-3 / F-009-4).
        Index(
            'uq_trust_lines_live_from_to_equivalent',
            'from_participant_id',
            'to_participant_id',
            'equivalent_id',
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
        CheckConstraint("status IN ('active', 'frozen', 'closed')", name='chk_trust_line_status'),
        # T1526, the same three jobs as `chk_debt_amount_positive` (see the comment there and
        # `app/db/types.py::finite_money_clauses`): SIGN, MAGNITUDE, NOT A NUMBER. Only the sign
        # clause differs - zero stays legal, because a limit of zero is a real trust line with no
        # headroom, and `>= 0` alone is TRUE for NaN on PostgreSQL exactly as `> 0` is.
        CheckConstraint(
            f'{_LIMIT_SQL} >= 0 AND {finite_money_clauses(_LIMIT_SQL)}',
            name='chk_trust_line_limit_positive',
        ),
        Index('ix_trust_lines_from_status', 'from_participant_id', 'status'),
    )