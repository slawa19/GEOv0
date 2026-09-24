import uuid
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, SmallInteger, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, validates
from app.db.base import Base
from app.utils.validation import validate_equivalent_code, validate_equivalent_precision

class Equivalent(Base):
    __tablename__ = "equivalents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    precision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # THE INTEGRITY HOLD (programme 015, step 5c, `T1546`). Not NULL = money does not move in this
    # equivalent: payment prepare and commit, clearing and real-simulator writes are refused at the
    # T1544 boundary (`MoneyBoundary.refuse_inactive_equivalents`, which reads this column in the same
    # statement as `is_active`). Set only by the scheduled reaction to a CONFIRMED `FAILED`
    # (`app/core/ledger/reconciliation.py`, `react_to_failed`), pointing at that result row; cleared only
    # by `POST /admin/equivalents/{code}/integrity-hold/clear` after a later `PASSED`. A separate field
    # from `is_active` on purpose: deactivation hides an equivalent and permits its deletion, a hold
    # keeps it visible. Never exposed on a read response.
    #
    # ON DELETE RESTRICT (step 5c review, 2026-09-14): the evidence row of a hold cannot be deleted while
    # the hold points at it, on either dialect. A delete of it - accidental or maintenance - would
    # otherwise release containment with no later PASSED, no reason and no audit. The only ways a hold
    # ends are the admin clear and deleting the equivalent row itself (whose CASCADE removes its results
    # and is accepted by both dialects, measured). Tests do not delete result rows in teardown: since
    # 018 stage B they dispose of their data by dropping a cloned database; a test that must remove a
    # hold's evidence nulls the hold first (`tests/integration/test_p015_step5c_hold_races_postgres.py`).
    # `use_alter`: the result table already references `equivalents`, so the pair is a cycle; it orders
    # PostgreSQL's `create_all`/`drop_all`, but NOT SQLite's `drop_all` over rows, which still fails with
    # `FOREIGN KEY constraint failed` (measured; `PRAGMA defer_foreign_keys` does not help a RESTRICT).
    integrity_hold_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "debt_reconciliation_results.id",
            name="fk_equivalents_integrity_hold_result",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("code = upper(code)", name="chk_equivalents_code_upper"),
    )

    @validates("code")
    def validate_code(self, _key: str, value: str) -> str:
        validate_equivalent_code(value)
        return value

    @validates("precision")
    def validate_precision(self, _key: str, value: int) -> int:
        return validate_equivalent_precision(value)
