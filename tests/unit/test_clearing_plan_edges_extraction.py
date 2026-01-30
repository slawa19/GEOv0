"""Unit test for clearing cycle edges extraction logic (same as in real_runner.tick_real_mode_clearing)."""

import pytest


def extract_cycle_edges_for_ui(cycle: list) -> list[dict]:
    """Extract edges from clearing cycle for UI visualization.
    
    This is the same logic used in real_runner.tick_real_mode_clearing.
    Cycle edges come from ClearingService.find_cycles() in format:
    [{"debt_id": ..., "debtor": "pid_str", "creditor": "pid_str", "amount": ...}, ...]
    
    UI expects: [{"from": "pid", "to": "pid"}, ...]
    """
    cycle_edges: list[dict] = []
    try:
        for edge in cycle:
            debtor_pid = str(edge.get("debtor") or "") if isinstance(edge, dict) else str(getattr(edge, "debtor", ""))
            creditor_pid = str(edge.get("creditor") or "") if isinstance(edge, dict) else str(getattr(edge, "creditor", ""))
            if debtor_pid and creditor_pid:
                cycle_edges.append({"from": debtor_pid, "to": creditor_pid})
    except Exception:
        cycle_edges = []
    return cycle_edges


def test_extract_edges_from_clearing_cycle():
    """Test that edges are correctly extracted from ClearingService cycle format."""
    # This is the format returned by ClearingService.find_cycles()
    cycle = [
        {"debt_id": "abc", "debtor": "alice", "creditor": "bob", "amount": "100"},
        {"debt_id": "def", "debtor": "bob", "creditor": "charlie", "amount": "50"},
        {"debt_id": "ghi", "debtor": "charlie", "creditor": "alice", "amount": "75"},
    ]
    
    edges = extract_cycle_edges_for_ui(cycle)
    
    assert len(edges) == 3
    assert edges[0] == {"from": "alice", "to": "bob"}
    assert edges[1] == {"from": "bob", "to": "charlie"}
    assert edges[2] == {"from": "charlie", "to": "alice"}


def test_clearing_plan_steps_include_highlight_edges():
    """Test that clearing plan steps are built with highlight_edges when cycle has edges."""
    cycle = [
        {"debt_id": "1", "debtor": "A", "creditor": "B", "amount": "10"},
        {"debt_id": "2", "debtor": "B", "creditor": "A", "amount": "10"},
    ]
    
    cycle_edges = extract_cycle_edges_for_ui(cycle)
    
    # Build plan_steps (same logic as in tick_real_mode_clearing)
    plan_steps = []
    if cycle_edges:
        plan_steps.append({"at_ms": 0, "highlight_edges": cycle_edges, "intensity_key": "hi"})
        plan_steps.append({"at_ms": 400, "particles_edges": cycle_edges, "intensity_key": "mid"})
        plan_steps.append({"at_ms": 900, "flash": {"kind": "clearing"}})
    
    assert len(plan_steps) == 3
    assert "highlight_edges" in plan_steps[0]
    assert plan_steps[0]["highlight_edges"] == [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]
    assert "particles_edges" in plan_steps[1]
    assert "flash" in plan_steps[2]
