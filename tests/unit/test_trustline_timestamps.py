from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.schemas.trustline import TrustLine


def test_trustline_wire_timestamp_preserves_explicit_offset() -> None:
    aware = datetime(2026, 8, 11, 12, 30, tzinfo=timezone(timedelta(hours=3)))
    model = TrustLine(
        id=uuid4(),
        from_pid="alice",
        to_pid="bob",
        equivalent_code="USD",
        limit=Decimal("10"),
        used=Decimal("2"),
        available=Decimal("8"),
        status="active",
        created_at=aware,
        updated_at=aware,
        close_requested_at=aware,  # 026 `T2603.1`: required, no default - every projection must say it
    )

    assert model.created_at == aware
    assert model.updated_at == aware
    assert model.close_requested_at == aware
