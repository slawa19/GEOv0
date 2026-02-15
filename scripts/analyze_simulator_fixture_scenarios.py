from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "fixtures" / "simulator"


@dataclass(frozen=True)
class ScenarioRow:
    scenario_id: str
    rel_path: str
    participants: int
    trustlines: int
    equivalents: list[str]
    manual_mode: bool
    auto_tx_proxy: bool
    warmup_ticks: int | None
    cycle_hint: dict[str, dict[str, int | bool]]


def _iter_fixture_scenarios() -> Iterable[Path]:
    for path in sorted(FIXTURES_DIR.glob("**/scenario.json")):
        if "_archive" in path.parts:
            continue
        if "negative" in path.parts:
            continue
        yield path


def _scc_kosaraju(nodes: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    g: dict[str, list[str]] = {n: [] for n in nodes}
    gr: dict[str, list[str]] = {n: [] for n in nodes}

    for u, v in edges:
        g.setdefault(u, []).append(v)
        gr.setdefault(v, []).append(u)
        g.setdefault(v, [])
        gr.setdefault(u, [])

    seen: set[str] = set()
    order: list[str] = []

    def dfs1(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        seen.add(start)
        while stack:
            u, i = stack[-1]
            if i < len(g[u]):
                v = g[u][i]
                stack[-1] = (u, i + 1)
                if v not in seen:
                    seen.add(v)
                    stack.append((v, 0))
            else:
                stack.pop()
                order.append(u)

    for n in list(g.keys()):
        if n not in seen:
            dfs1(n)

    seen2: set[str] = set()

    def dfs2(start: str) -> list[str]:
        comp: list[str] = []
        stack = [start]
        seen2.add(start)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in gr[u]:
                if v not in seen2:
                    seen2.add(v)
                    stack.append(v)
        return comp

    comps: list[list[str]] = []
    for n in reversed(order):
        if n not in seen2:
            comps.append(dfs2(n))

    return comps


def _scenario_row(path: Path) -> ScenarioRow:
    data = json.loads(path.read_text(encoding="utf-8"))

    scenario_id = str(data.get("scenario_id") or path.parent.name)
    participants = data.get("participants") or []
    trustlines = data.get("trustlines") or []
    equivalents = data.get("equivalents") or []
    events = data.get("events") or []
    settings = data.get("settings") or {}

    manual_mode = any(
        (e.get("type") == "note")
        and (
            e.get("label") == "manual_mode"
            or "ручн" in str(e.get("description", "")).lower()
        )
        for e in events
    ) or ("intensity_percent=0" in str(data.get("description", "")))

    warmup_ticks = None
    if isinstance(settings, dict):
        warmup_ticks = (settings.get("warmup") or {}).get("ticks")

    auto_tx_proxy = False
    for bp in data.get("behaviorProfiles") or []:
        props = (bp.get("props") or {}) if isinstance(bp, dict) else {}
        if "tx_rate" in props:
            auto_tx_proxy = True
            break

    by_eq: dict[str, list[tuple[str, str]]] = {eq: [] for eq in equivalents}
    if not by_eq:
        by_eq = {"<none>": []}

    for tl in trustlines:
        eq = tl.get("equivalent") or (equivalents[0] if equivalents else "<none>")
        by_eq.setdefault(eq, []).append((tl.get("from"), tl.get("to")))

    cycle_hint: dict[str, dict[str, int | bool]] = {}
    for eq, edges_raw in by_eq.items():
        edges = [(u, v) for u, v in edges_raw if isinstance(u, str) and isinstance(v, str)]
        nodes = {u for u, _ in edges} | {v for _, v in edges}
        comps = _scc_kosaraju(nodes, edges)
        cycle_hint[eq] = {
            "nodes": len(nodes),
            "edges": len(edges),
            "max_scc": max((len(c) for c in comps), default=0),
            "scc_ge_3": any(len(c) >= 3 for c in comps),
            "scc_ge_4": any(len(c) >= 4 for c in comps),
        }

    return ScenarioRow(
        scenario_id=scenario_id,
        rel_path=path.relative_to(ROOT).as_posix(),
        participants=len(participants),
        trustlines=len(trustlines),
        equivalents=list(equivalents),
        manual_mode=manual_mode,
        auto_tx_proxy=auto_tx_proxy,
        warmup_ticks=warmup_ticks,
        cycle_hint=cycle_hint,
    )


def main() -> None:
    rows = [_scenario_row(p) for p in _iter_fixture_scenarios()]

    for r in rows:
        eqs = ",".join(r.equivalents) if r.equivalents else "-"
        print(
            f"- {r.scenario_id:35} participants={r.participants:3} trustlines={r.trustlines:4} "
            f"eq={eqs:10} manual={str(r.manual_mode):5} auto_tx={str(r.auto_tx_proxy):5} warmup={r.warmup_ticks}"
        )
        for eq, ch in r.cycle_hint.items():
            print(
                f"    {eq}: nodes={ch['nodes']:3} edges={ch['edges']:4} max_scc={ch['max_scc']:3} "
                f"scc>=3={ch['scc_ge_3']} scc>=4={ch['scc_ge_4']}"
            )


if __name__ == "__main__":
    main()
