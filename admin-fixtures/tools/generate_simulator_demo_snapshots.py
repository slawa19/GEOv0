from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def iso_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_get(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise KeyError(key)
    return d[key]


@dataclass(frozen=True)
class DirectedCycle:
    nodes: tuple[str, ...]

    def edges(self) -> list[tuple[str, str]]:
        if len(self.nodes) < 3:
            raise ValueError("Cycle must have at least 3 nodes")
        pairs: list[tuple[str, str]] = []
        for a, b in zip(self.nodes, self.nodes[1:]):
            pairs.append((a, b))
        pairs.append((self.nodes[-1], self.nodes[0]))
        return pairs


def find_directed_cycle(edges: Iterable[tuple[str, str]], *, max_len: int = 10) -> DirectedCycle:
    """Deterministically find a directed simple cycle up to max_len.

    The choice is deterministic: nodes and adjacency are iterated in lexicographic order.
    """

    if max_len < 3:
        raise ValueError("max_len must be >= 3")

    adj: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)

    nodes = sorted(adj.keys())
    for s in nodes:
        adj[s] = sorted(set(adj[s]))

    def dfs(start: str, current: str, path: list[str], visited: set[str]) -> list[str] | None:
        if len(path) > max_len:
            return None

        for nxt in adj.get(current, []):
            if nxt == start and len(path) >= 3:
                return path

            if nxt in visited:
                continue
            visited.add(nxt)
            path.append(nxt)
            found = dfs(start, nxt, path, visited)
            if found:
                return found
            path.pop()
            visited.remove(nxt)
        return None

    for start in nodes:
        found = dfs(start, start, [start], {start})
        if found and len(found) >= 3:
            # dfs returns the path without repeating the start at the end
            return DirectedCycle(nodes=tuple(found))

    raise RuntimeError(f"No directed cycle found in trustlines graph (max_len={max_len})")


def build_snapshot(
    *,
    datasets_root: Path,
    eq: str,
    generated_at: str,
) -> dict[str, Any]:
    participants_path = datasets_root / f"participants.viz-{eq}.json"
    trustlines_path = datasets_root / "trustlines.json"

    participants = read_json(participants_path)
    trustlines = read_json(trustlines_path)

    if not isinstance(participants, list):
        raise TypeError(f"Expected list in {participants_path}")
    if not isinstance(trustlines, list):
        raise TypeError(f"Expected list in {trustlines_path}")

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    for p in participants:
        if not isinstance(p, dict):
            continue
        pid = str(safe_get(p, "pid"))
        node_ids.add(pid)
        nodes.append(
            {
                "id": pid,
                "name": p.get("display_name"),
                "type": p.get("type"),
                "status": p.get("status"),
                "net_balance_atoms": p.get("net_balance_atoms"),
                "net_sign": p.get("net_sign"),
                "viz_color_key": p.get("viz_color_key"),
                "viz_size": p.get("viz_size"),
                "viz_badge_key": None,
            }
        )

    links: list[dict[str, Any]] = []
    for tl in trustlines:
        if not isinstance(tl, dict):
            continue
        if tl.get("equivalent") != eq:
            continue
        source = str(safe_get(tl, "from"))
        target = str(safe_get(tl, "to"))
        if source not in node_ids or target not in node_ids:
            # Fail-fast: the snapshot must be internally consistent.
            raise RuntimeError(f"Dangling trustline in datasets: {source}->{target} ({eq})")

        links.append(
            {
                "id": f"{source}→{target}",
                "source": source,
                "target": target,
                "trust_limit": tl.get("limit"),
                "used": tl.get("used"),
                "available": tl.get("available"),
                "status": tl.get("status"),
                "viz_color_key": None,
                "viz_width_key": None,
                "viz_alpha_key": None,
            }
        )

    snapshot: dict[str, Any] = {
        "equivalent": eq,
        "generated_at": generated_at,
        "nodes": nodes,
        "links": links,
        "limits": {
            "max_nodes": len(nodes),
            "max_links": len(links),
            "max_particles": 300,
        },
    }
    return snapshot


def build_demo_tx_events(*, datasets_root: Path, eq: str, snapshot_node_ids: set[str]) -> list[dict[str, Any]]:
    tx_path = datasets_root / "transactions.json"
    txs = read_json(tx_path)
    if not isinstance(txs, list):
        raise TypeError(f"Expected list in {tx_path}")

    chosen: dict[str, Any] | None = None
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        payload = tx.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("equivalent") != eq:
            continue
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes:
            continue
        route0 = routes[0]
        if not isinstance(route0, dict):
            continue
        path = route0.get("path")
        if not isinstance(path, list) or len(path) < 2:
            continue
        path_ids = [str(x) for x in path]
        if any(pid not in snapshot_node_ids for pid in path_ids):
            continue
        chosen = tx
        break

    if not chosen:
        raise RuntimeError(f"No suitable transaction found for eq={eq} in {tx_path}")

    payload = chosen["payload"]
    routes = payload["routes"]
    path_ids = [str(x) for x in routes[0]["path"]]
    edges = []
    for a, b in zip(path_ids, path_ids[1:]):
        edges.append({"from": a, "to": b})

    event = {
        "event_id": str(chosen.get("id") or chosen.get("tx_id") or "tx.demo"),
        "ts": str(chosen.get("created_at") or iso_now()),
        "type": "tx.updated",
        "equivalent": eq,
        "ttl_ms": 1200,
        "edges": edges,
    }
    return [event]


def build_demo_clearing_events_from_clearing_cycles(
    *,
    datasets_root: Path,
    eq: str,
    snapshot_node_ids: set[str],
) -> list[dict[str, Any]]:
    """Build clearing demo playlist from canonical clearing-cycles dataset.

    clearing-cycles uses (debtor, creditor). TrustLine direction is creditor->debtor.
    For visualization we highlight edges in trustline direction.

    If there are no cycles for the EQ, returns an empty list (no custom fallbacks).
    """

    cycles_path = datasets_root / "clearing-cycles.json"
    raw = read_json(cycles_path)
    if not isinstance(raw, dict):
        raise TypeError(f"Expected object in {cycles_path}")

    equivalents = raw.get("equivalents")
    if not isinstance(equivalents, dict):
        raise TypeError(f"Expected 'equivalents' object in {cycles_path}")

    eq_obj = equivalents.get(eq)
    if not isinstance(eq_obj, dict):
        return []

    cycles = eq_obj.get("cycles")
    if not isinstance(cycles, list) or len(cycles) == 0:
        return []

    # Deterministic: take the first cycle.
    cycle0 = cycles[0]
    if not isinstance(cycle0, list) or len(cycle0) < 2:
        return []

    highlight_edges: list[dict[str, str]] = []
    for seg in cycle0:
        if not isinstance(seg, dict):
            continue
        debtor = seg.get("debtor")
        creditor = seg.get("creditor")
        if not isinstance(debtor, str) or not isinstance(creditor, str):
            continue
        if debtor not in snapshot_node_ids or creditor not in snapshot_node_ids:
            # Skip segments that do not exist in the snapshot.
            continue
        # TrustLine direction: creditor -> debtor
        highlight_edges.append({"from": creditor, "to": debtor})

    if not highlight_edges:
        return []

    plan_id = f"clearing-demo-{eq}-{highlight_edges[0]['from'][:12]}"

    steps = [
        {
            "at_ms": 0,
            "highlight_edges": highlight_edges,
        },
        {
            "at_ms": 380,
            "flash": {"kind": "clearing"},
        },
        {
            "at_ms": 720,
            "highlight_edges": highlight_edges,
        },
    ]

    plan_evt = {
        "event_id": f"{plan_id}.plan",
        "ts": iso_now(),
        "type": "clearing.plan",
        "equivalent": eq,
        "plan_id": plan_id,
        "steps": steps,
    }

    done_evt = {
        "event_id": f"{plan_id}.done",
        "ts": iso_now(),
        "type": "clearing.done",
        "equivalent": eq,
        "plan_id": plan_id,
    }

    return [plan_evt, done_evt]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate simulator-ui demo snapshot/events from canonical seed-based Admin UI fixtures. "
            "This keeps demo data comparable with Admin UI real mode."
        )
    )
    parser.add_argument(
        "--datasets-root",
        default="admin-ui/public/admin-fixtures/v1/datasets",
        help="Path to datasets root (default: admin-ui/public/admin-fixtures/v1/datasets)",
    )
    parser.add_argument(
        "--out-root",
        default="simulator-ui/v2/public/simulator-fixtures/v1",
        help="Output root for simulator fixtures (default: simulator-ui/v2/public/simulator-fixtures/v1)",
    )
    parser.add_argument("--eq", default="UAH", help="Equivalent code (default: UAH)")

    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    datasets_root = (repo_root / args.datasets_root).resolve()
    out_root = (repo_root / args.out_root).resolve()

    meta_path = datasets_root.parent / "_meta.json"
    generated_at = None
    if meta_path.exists():
        try:
            meta = read_json(meta_path)
            if isinstance(meta, dict) and isinstance(meta.get("generated_at"), str):
                generated_at = meta["generated_at"]
        except Exception:
            generated_at = None

    generated_at = generated_at or iso_now()

    snapshot = build_snapshot(datasets_root=datasets_root, eq=args.eq, generated_at=generated_at)

    eq_out = out_root / args.eq
    write_json(eq_out / "snapshot.json", snapshot)

    node_ids = {n["id"] for n in snapshot["nodes"]}
    tx_events = build_demo_tx_events(datasets_root=datasets_root, eq=args.eq, snapshot_node_ids=node_ids)
    write_json(eq_out / "events" / "demo-tx.json", tx_events)

    clearing_events = build_demo_clearing_events_from_clearing_cycles(
        datasets_root=datasets_root,
        eq=args.eq,
        snapshot_node_ids=node_ids,
    )
    write_json(eq_out / "events" / "demo-clearing.json", clearing_events)

    print(f"Wrote snapshot: {eq_out / 'snapshot.json'}")
    print(f"Wrote demo-tx:  {eq_out / 'events' / 'demo-tx.json'}")
    print(f"Wrote demo-clr: {eq_out / 'events' / 'demo-clearing.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
