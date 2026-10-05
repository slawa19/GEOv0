"""The committed Simulator demo snapshots of EVERY equivalent are in the form the generator writes.

Programme 029, stage S4, F-029-20. `scripts/sync_demo_fixtures.ps1` regenerated only UAH, so the EUR and
HOUR snapshots stayed in an older form: a signed `net_balance_atoms` ("-150") next to a separate
`net_sign`, and trust lines with `status: frozen`, a value 028 withdrew from the contract (F-028-29).
The generator writes the magnitude and keeps the sign in `net_sign` (see `compute_node_patch` in
`admin-fixtures/tools/generate_simulator_demo_snapshots.py`), so a leading minus in a committed file means
the file was not produced by it.

What this checks, and what it does not:

- every equivalent of the canonical Admin pack (`admin-fixtures/v1/datasets/equivalents.json`) has a
  non-empty demo snapshot, and no JSON file of its directory carries a `net_balance_atoms` with a leading
  `-` or a trust line (an object with `source` and `target`) in status `frozen`. Participant nodes may be
  `frozen`: that is a participant status, not a line status;
- the scan itself is checked first on a synthetic snapshot that holds both defects, otherwise a green run
  would only prove the scan is blind (AGENTS.md section 9, anti-vacuum);
- it does not run the generator and does not compare bytes; that the files are what the generator writes
  is held by regenerating them (`npm --prefix simulator-ui/v2 run sync:demo-fixtures`) and reading the diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "simulator-ui" / "v2" / "public" / "simulator-fixtures" / "v1"
_EQUIVALENTS = _REPO_ROOT / "admin-fixtures" / "v1" / "datasets" / "equivalents.json"


def _defects(node: Any, where: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        net = node.get("net_balance_atoms")
        if isinstance(net, str) and net.startswith("-"):
            found.append(f"{where}: signed net_balance_atoms {net!r}")
        if node.get("status") == "frozen" and "source" in node and "target" in node:
            found.append(f"{where}: trust line {node['source']} -> {node['target']} in status 'frozen'")
        for key, value in node.items():
            found.extend(_defects(value, f"{where}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_defects(value, f"{where}[{index}]"))
    return found


def test_demo_snapshots_of_every_equivalent_have_no_signed_net_and_no_frozen_line() -> None:
    synthetic = {
        "nodes": [{"id": "A", "status": "frozen", "net_balance_atoms": "-5"}],
        "links": [{"source": "A", "target": "B", "status": "frozen"}],
    }
    assert len(_defects(synthetic, "synthetic")) == 2, "the scan no longer sees the defects it exists to find"

    codes = sorted(str(e["code"]) for e in json.loads(_EQUIVALENTS.read_text(encoding="utf-8")))
    assert codes, "the canonical pack names no equivalent: nothing would be checked"

    problems: list[str] = []
    for code in codes:
        snapshot_path = _FIXTURES / code / "snapshot.json"
        if not snapshot_path.is_file():
            problems.append(f"{code}: no demo snapshot (scripts/sync_demo_fixtures.ps1 must write one per equivalent)")
            continue
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not snapshot.get("nodes"):
            problems.append(f"{code}: snapshot has no nodes")
        for path in sorted((_FIXTURES / code).rglob("*.json")):
            problems.extend(_defects(json.loads(path.read_text(encoding="utf-8")), f"{code}/{path.relative_to(_FIXTURES / code).as_posix()}"))

    # Name every kind of defect of every equivalent with its count and two samples: a list cut at N lines would
    # show the first defect class only.
    by_kind: dict[tuple[str, str], list[str]] = {}
    for problem in problems:
        kind = (
            "frozen trust line"
            if "in status 'frozen'" in problem
            else "signed net_balance_atoms"
            if "signed net_balance_atoms" in problem
            else "other"
        )
        by_kind.setdefault((problem.split("/", 1)[0].split(":", 1)[0], kind), []).append(problem)
    summary = [f"{code} {kind} x{len(items)}: {'; '.join(items[:2])}" for (code, kind), items in sorted(by_kind.items())]
    assert not problems, "\n".join(summary)
