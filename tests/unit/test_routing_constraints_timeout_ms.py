from decimal import Decimal

import pytest

from app.core.payments.router import PaymentRouter
from app.utils.exceptions import TimeoutException


def test_find_flow_routes_timeout_ms_is_enforced(monkeypatch):
    router = PaymentRouter(None)
    router.graph = {
        "A": {"B": Decimal("10")},
        "B": {"C": Decimal("10")},
        "C": {},
    }
    router.edge_can_be_intermediate = {
        "A": {"B": True},
        "B": {"C": True},
        "C": {},
    }

    calls = {"n": 0}

    def _fake_perf_counter() -> float:
        calls["n"] += 1
        # 1st call is used to compute deadline, 2nd+ will be used in the loop checks.
        return 0.0 if calls["n"] == 1 else 999.0

    monkeypatch.setattr(
        "app.core.payments.router.time.perf_counter",
        _fake_perf_counter,
        raising=True,
    )

    with pytest.raises(TimeoutException):
        router.find_flow_routes(
            "A",
            "C",
            Decimal("1"),
            max_hops=6,
            max_paths=3,
            timeout_ms=1,
        )

