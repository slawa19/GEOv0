from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.schemas.trustline import TrustLine


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_trustline_wire_timestamp_treats_naive_database_value_as_utc(field: str) -> None:
    naive = datetime(2026, 8, 11, 12, 30)
    values = {
        "id": uuid4(),
        "from_pid": "alice",
        "to_pid": "bob",
        "equivalent_code": "USD",
        "limit": Decimal("10"),
        "used": Decimal("2"),
        "available": Decimal("8"),
        "status": "active",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        field: naive,
    }

    parsed = getattr(TrustLine(**values), field)

    assert parsed == naive.replace(tzinfo=timezone.utc)
    assert parsed.utcoffset() == timedelta(0)


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
    )

    assert model.created_at == aware
    assert model.updated_at == aware
