"""Named 050 architecture policy guard (AGENTS §11), paired with behavior tests."""

import ast
from pathlib import Path

from app.core.simulator import real_payment_planner


def test_planner_imports_and_calls_shared_policy_without_its_own_rule():
    tree = ast.parse(Path(real_payment_planner.__file__).read_text(encoding="utf-8"))
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               and node.module == "app.core.payments.capacity" for alias in node.names}
    assert {"pair_rules", "route_breaks_policy"} <= imports
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)}
    assert {"pair_rules", "route_breaks_policy"} <= calls
    # These policy field names belong to capacity.py; copying its predicates here
    # would create another owner even if the imported functions remained unused.
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str)}
    assert not {"can_be_intermediate", "max_hop_usage", "blocked_participants"} & literals
