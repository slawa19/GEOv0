from decimal import Decimal

from app.core.payments.router import PaymentRouter


def test_find_paths_returns_k_shortest_alternatives_by_hops():
    # Two equal-hop paths A->C->D and A->B->D.
    # Ensure k=2 returns both without duplicates.
    router = PaymentRouter(None)
    router.graph = {
        "A": {"C": Decimal("10"), "B": Decimal("10")},
        "B": {"D": Decimal("10")},
        "C": {"D": Decimal("10")},
        "D": {},
    }
    router.edge_can_be_intermediate = {
        "A": {"C": True, "B": True},
        "B": {"D": True},
        "C": {"D": True},
        "D": {},
    }

    paths = router.find_paths("A", "D", Decimal("1"), max_hops=6, k=2)
    assert paths == [["A", "C", "D"], ["A", "B", "D"]]
