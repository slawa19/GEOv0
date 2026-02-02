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


def parse_amount(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return x if x == x else None
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def quantile(values_sorted: list[float], p: float) -> float:
    if not values_sorted:
        raise ValueError("values_sorted must be non-empty")
    if p <= 0:
        return values_sorted[0]
    if p >= 1:
        return values_sorted[-1]
    n = len(values_sorted)
    i = int(p * (n - 1))
    return values_sorted[i]


def link_width_key(limit: float | None, *, q33: float | None, q66: float | None) -> str:
    if limit is None or q33 is None or q66 is None:
        return "hairline"
    if limit <= q33:
        return "thin"
    if limit <= q66:
        return "mid"
    return "thick"


def link_alpha_key(status: str | None, used: float | None, limit: float | None) -> str:
    if status and status != "active":
        return "muted"
    if used is None or limit is None or limit <= 0:
        return "bg"
    r = abs(used) / limit
    if r >= 0.75:
        return "hi"
    if r >= 0.40:
        return "active"
    if r >= 0.15:
        return "muted"
    return "bg"


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


def find_directed_cycle(edges: Iterable[tuple[str, str]], *, min_len: int = 3, max_len: int = 10) -> DirectedCycle:
    """Deterministically find a directed simple cycle with length in [min_len, max_len].

    The choice is deterministic: nodes and adjacency are iterated in lexicographic order.
    """

    if min_len < 3:
        raise ValueError("min_len must be >= 3")
    if max_len < min_len:
        raise ValueError("max_len must be >= min_len")

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
            if nxt == start and len(path) >= min_len:
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

    raise RuntimeError(f"No directed cycle found in trustlines graph (min_len={min_len}, max_len={max_len})")


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
        type_raw = p.get("type")
        type_norm = str(type_raw or "").strip().lower()
        viz_shape_key = "rounded-rect" if type_norm == "business" else "circle"
        node_ids.add(pid)
        nodes.append(
            {
                "id": pid,
                "name": p.get("display_name"),
                "type": type_raw,
                "status": p.get("status"),
                "net_balance_atoms": p.get("net_balance_atoms"),
                "net_sign": p.get("net_sign"),
                "viz_color_key": p.get("viz_color_key"),
                "viz_shape_key": p.get("viz_shape_key") if isinstance(p.get("viz_shape_key"), str) else viz_shape_key,
                "viz_size": p.get("viz_size"),
                "viz_badge_key": None,
            }
        )

    links: list[dict[str, Any]] = []
    link_stats: list[tuple[dict[str, Any], float | None, float | None]] = []
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

        link = {
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
        links.append(link)

        limit_num = parse_amount(tl.get("limit"))
        used_num = parse_amount(tl.get("used"))
        link_stats.append((link, limit_num, used_num))

    limits = sorted([x for _, x, _ in link_stats if x is not None])
    q33 = quantile(limits, 0.33) if limits else None
    q66 = quantile(limits, 0.66) if limits else None

    for link, limit_num, used_num in link_stats:
        status = link.get("status")
        if not isinstance(status, str):
            status = None
        link["viz_width_key"] = link_width_key(limit_num, q33=q33, q66=q66)
        link["viz_alpha_key"] = link_alpha_key(status, used=used_num, limit=limit_num)

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


def build_demo_tx_events(
    *,
    datasets_root: Path,
    eq: str,
    generated_at: str,
    snapshot_node_ids: set[str],
    snapshot_link_keys: set[str],
) -> list[dict[str, Any]]:
    tx_path = datasets_root / "transactions.json"
    txs = read_json(tx_path)
    if not isinstance(txs, list):
        raise TypeError(f"Expected list in {tx_path}")

    candidates: list[tuple[str, str, list[str]]] = []
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

        # Demo playlists are validated against snapshot.links at runtime.
        # Only keep routes whose hop-by-hop edges exist in the trustline snapshot.
        hop_keys = [f"{a}→{b}" for a, b in zip(path_ids, path_ids[1:])]
        if any(k not in snapshot_link_keys for k in hop_keys):
            continue

        tx_id = str(tx.get("id") or tx.get("tx_id") or "tx.demo")
        ts = str(tx.get("created_at") or generated_at)
        candidates.append((tx_id, ts, path_ids))

    # If the canonical transactions dataset has no snapshot-consistent routes,
    # we will synthesize demo transactions from the trustlines graph below.

    single = [c for c in candidates if len(c[2]) == 2]
    multi = [c for c in candidates if len(c[2]) >= 3]

    picked: list[tuple[str, str, list[str]]] = []
    picked_ids: set[str] = set()

    # Keep the first event single-edge when possible (helps keep existing visuals stable).
    if candidates:
        first = single[0] if single else candidates[0]
        picked.append(first)
        picked_ids.add(first[0])

    # Ensure at least 1–2 multi-hop events exist in the playlist.
    for c in multi:
        if c[0] in picked_ids:
            continue
        picked.append(c)
        picked_ids.add(c[0])
        if len([x for x in picked if len(x[2]) >= 3]) >= 2:
            break

    # Fill up to a compact “rail” playlist size.
    target = 20
    for c in candidates:
        if len(picked) >= target:
            break
        if c[0] in picked_ids:
            continue
        picked.append(c)
        picked_ids.add(c[0])

    # Synthesize missing events from the trustlines graph (snapshot-consistent by definition).
    trustlines_path = datasets_root / "trustlines.json"
    trustlines = read_json(trustlines_path)
    tl_edges: list[tuple[str, str]] = []
    if isinstance(trustlines, list):
        for tl in trustlines:
            if not isinstance(tl, dict):
                continue
            if tl.get("equivalent") != eq:
                continue
            s = str(tl.get("from"))
            t = str(tl.get("to"))
            if s in snapshot_node_ids and t in snapshot_node_ids:
                tl_edges.append((s, t))

    # Deterministic edge order.
    tl_edges = sorted(set(tl_edges))

    # Ensure the playlist is non-empty.
    if not picked and tl_edges:
        s, t = tl_edges[0]
        picked.append(("tx.demo.edge.1", generated_at, [s, t]))
        picked_ids.add("tx.demo.edge.1")

    # Ensure at least 2 multi-hop events exist (when possible).
    need_multi = 2 - len([x for x in picked if len(x[2]) >= 3])
    if need_multi > 0 and tl_edges:
        cyc: DirectedCycle | None = None
        for mx in (6, 8, 10):
            try:
                cyc = find_directed_cycle(tl_edges, min_len=4, max_len=mx)
                break
            except Exception:
                cyc = None

        if cyc is not None:
            nodes = list(cyc.nodes)
            path = nodes[: min(len(nodes), 6)]
            if len(path) >= 3:
                for i in range(need_multi):
                    tx_id = f"tx.demo.multi-hop.{i+1}"
                    if tx_id in picked_ids:
                        continue
                    picked.append((tx_id, generated_at, path))
                    picked_ids.add(tx_id)

    # Top up with deterministic single-edge events.
    if tl_edges:
        synth_idx = 0
        while len(picked) < target and synth_idx < len(tl_edges):
            s, t = tl_edges[synth_idx]
            synth_idx += 1
            tx_id = f"tx.demo.edge.{synth_idx}"
            if tx_id in picked_ids:
                continue
            picked.append((tx_id, generated_at, [s, t]))
            picked_ids.add(tx_id)

    events: list[dict[str, Any]] = []
    for tx_id, ts, path_ids in picked:
        edges: list[dict[str, str]] = []
        for a, b in zip(path_ids, path_ids[1:]):
            edges.append({"from": a, "to": b})
        events.append(
            {
                "event_id": tx_id,
                "ts": ts,
                "type": "tx.updated",
                "equivalent": eq,
                "ttl_ms": 1200,
                "edges": edges,
            }
        )

    return events


def build_demo_clearing_events_from_clearing_cycles(
    *,
    datasets_root: Path,
    eq: str,
    generated_at: str,
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

    def cycle_to_edges(cycle: Any) -> list[dict[str, str]] | None:
        if not isinstance(cycle, list) or len(cycle) < 3:
            return None
        edges: list[dict[str, str]] = []
        for seg in cycle:
            if not isinstance(seg, dict):
                return None
            debtor = seg.get("debtor")
            creditor = seg.get("creditor")
            if not isinstance(debtor, str) or not isinstance(creditor, str):
                return None
            if debtor not in snapshot_node_ids or creditor not in snapshot_node_ids:
                return None
            # TrustLine direction: creditor -> debtor
            edges.append({"from": creditor, "to": debtor})
        return edges

    # Deterministic playlist selection.
    # We prefer multiple distinct cycles over repeating the same cycle multiple times.
    all_cycles_edges: list[list[dict[str, str]]] = []
    for cycle in cycles:
        edges = cycle_to_edges(cycle)
        if edges:
            all_cycles_edges.append(edges)

    if not all_cycles_edges:
        return []

    # Avoid repeating the same directed edge across steps: it makes the demo look like
    # we're "sending" the same mutual-debt closure multiple times between the same nodes.
    used_edge_keys: set[str] = set()
    picked_cycles: list[list[dict[str, str]]] = []

    def edge_key(e: dict[str, str]) -> str:
        return f"{e['from']}→{e['to']}"

    max_steps = 8
    for edges in all_cycles_edges:
        keys = [edge_key(e) for e in edges]
        if any(k in used_edge_keys for k in keys):
            continue
        picked_cycles.append(edges)
        used_edge_keys.update(keys)
        if len(picked_cycles) >= max_steps:
            break

    # If the dataset is small or very overlapping, allow fewer steps (but keep it meaningful).
    # We require at least 3 steps to actually demonstrate "clearing as a process".
    if len(picked_cycles) < 3:
        # Fall back to the first 3 valid cycles, even if they overlap.
        picked_cycles = all_cycles_edges[: min(3, len(all_cycles_edges))]

    plan_id = f"clearing-demo-{eq}"

    step_gap_ms = 900
    steps: list[dict[str, Any]] = []
    for idx, edges in enumerate(picked_cycles):
        steps.append(
            {
                "at_ms": idx * step_gap_ms,
                "highlight_edges": edges,
                "particles_edges": edges,
            }
        )

    plan_evt = {
        "event_id": f"{plan_id}.plan",
        "ts": generated_at,
        "type": "clearing.plan",
        "equivalent": eq,
        "plan_id": plan_id,
        "steps": steps,
    }

    done_evt = {
        "event_id": f"{plan_id}.done",
        "ts": generated_at,
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
    link_keys = {l["id"] for l in snapshot["links"]}
    tx_events = build_demo_tx_events(
        datasets_root=datasets_root,
        eq=args.eq,
        generated_at=generated_at,
        snapshot_node_ids=node_ids,
        snapshot_link_keys=link_keys,
    )
    write_json(eq_out / "events" / "demo-tx.json", tx_events)

    clearing_events = build_demo_clearing_events_from_clearing_cycles(
        datasets_root=datasets_root,
        eq=args.eq,
        generated_at=generated_at,
        snapshot_node_ids=node_ids,
    )
    write_json(eq_out / "events" / "demo-clearing.json", clearing_events)

    print(f"Wrote snapshot: {eq_out / 'snapshot.json'}")
    print(f"Wrote demo-tx:  {eq_out / 'events' / 'demo-tx.json'}")
    print(f"Wrote demo-clr: {eq_out / 'events' / 'demo-clearing.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
