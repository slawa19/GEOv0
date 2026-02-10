from app.core.simulator.runtime_utils import edges_by_equivalent


def test_edges_by_equivalent_filters_non_active_trustlines() -> None:
    raw = {
        "trustlines": [
            {"from": "A", "to": "B", "equivalent": "EUR", "status": "active"},
            {"from": "A", "to": "C", "equivalent": "EUR", "status": "frozen"},
            {"from": "B", "to": "C", "equivalent": "EUR", "status": "deleted"},
            # Missing status should be treated as active (backward-compatible).
            {"from": "C", "to": "D", "equivalent": "EUR"},
        ]
    }

    out = edges_by_equivalent(raw)
    assert out == {"EUR": [("A", "B"), ("C", "D")]}
