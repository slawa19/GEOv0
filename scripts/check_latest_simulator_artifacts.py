from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / ".local-run" / "simulator" / "runs"


@dataclass(frozen=True)
class RunArtifacts:
    run_id: str
    artifacts_dir: Path
    status_path: Path
    summary_path: Path
    events_path: Path


def _parse_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except json.JSONDecodeError as e:
                raise SystemExit(f"Bad NDJSON in {path} at line {line_no}: {e}")


def _latest_run_dir() -> Path:
    if not RUNS_DIR.exists():
        raise SystemExit(f"Runs dir not found: {RUNS_DIR}")
    run_dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not run_dirs:
        raise SystemExit(f"No run_* dirs found in {RUNS_DIR}")

    # Prefer mtime; name also embeds timestamp but mtime is the simplest signal.
    run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return run_dirs[0]


def _load_run_artifacts(run_dir: Path) -> RunArtifacts:
    artifacts_dir = run_dir / "artifacts"
    if not artifacts_dir.exists():
        raise SystemExit(f"Artifacts dir missing: {artifacts_dir}")

    status_path = artifacts_dir / "status.json"
    summary_path = artifacts_dir / "summary.json"
    events_path = artifacts_dir / "events.ndjson"

    if not status_path.exists():
        raise SystemExit(f"Missing {status_path}")
    if not summary_path.exists():
        raise SystemExit(f"Missing {summary_path}")
    if not events_path.exists():
        raise SystemExit(f"Missing {events_path}")

    status = _parse_json(status_path)
    run_id = str(status.get("run_id") or run_dir.name)
    return RunArtifacts(
        run_id=run_id,
        artifacts_dir=artifacts_dir,
        status_path=status_path,
        summary_path=summary_path,
        events_path=events_path,
    )


def _iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # backend uses utc_now().isoformat() -> usually includes timezone
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> None:
    run_dir = _latest_run_dir()
    ra = _load_run_artifacts(run_dir)

    status = _parse_json(ra.status_path)
    summary = _parse_json(ra.summary_path)

    events = list(_iter_ndjson(ra.events_path))

    counts = Counter(str(e.get("type") or "<missing.type>") for e in events)
    counts_eq = Counter(str(e.get("equivalent") or "<missing.eq>") for e in events)

    plans: dict[str, dict[str, Any]] = {}
    dones: dict[str, dict[str, Any]] = {}

    missing_patches = Counter()
    label_worthy_tx = 0

    for e in events:
        t = str(e.get("type") or "")
        if t == "clearing.plan":
            pid = str(e.get("plan_id") or "")
            if pid:
                plans[pid] = e
        if t == "clearing.done":
            pid = str(e.get("plan_id") or "")
            if pid:
                dones[pid] = e

        if t == "tx.updated":
            # floating labels in real-mode depend on balance patches being present
            node_patch = e.get("node_patch")
            if isinstance(node_patch, list) and len(node_patch) > 0:
                # count how many look like they carry balances
                for p in node_patch:
                    if isinstance(p, dict) and ("net_balance_atoms" in p or "net_sign" in p):
                        label_worthy_tx += 1
                        break
            else:
                missing_patches["tx.updated.node_patch"] += 1

            edge_patch = e.get("edge_patch")
            if not isinstance(edge_patch, list):
                missing_patches["tx.updated.edge_patch"] += 1

    missing_done = sorted(set(plans.keys()) - set(dones.keys()))
    orphan_done = sorted(set(dones.keys()) - set(plans.keys()))

    durations_ms: list[int] = []
    for pid, plan in plans.items():
        done = dones.get(pid)
        if not done:
            continue
        t0 = _iso(str(plan.get("ts") or ""))
        t1 = _iso(str(done.get("ts") or ""))
        if t0 and t1:
            durations_ms.append(int((t1 - t0).total_seconds() * 1000))

    print("=== Latest simulator run artifacts ===")
    print(f"run_dir:      {run_dir}")
    print(f"run_id:       {ra.run_id}")
    print(f"scenario_id:  {status.get('scenario_id')}")
    print(f"mode:         {status.get('mode')}")
    print(f"created_at:   {status.get('created_at')}")
    print(f"events:       {len(events)} lines")
    print()

    print("-- Event counts --")
    for k, v in counts.most_common():
        print(f"{k:16s} {v}")
    print()

    print("-- Equivalents --")
    for k, v in counts_eq.most_common():
        print(f"{k:10s} {v}")
    print()

    print("-- Clearing plan/done integrity --")
    print(f"clearing.plan: {len(plans)}")
    print(f"clearing.done: {len(dones)}")
    if missing_done:
        print(f"WARNING missing clearing.done for {len(missing_done)} plan_id(s)")
        print("  sample:")
        for pid in missing_done[:5]:
            print(f"  - {pid}")
    if orphan_done:
        print(f"WARNING orphan clearing.done without plan for {len(orphan_done)} plan_id(s)")
        print("  sample:")
        for pid in orphan_done[:5]:
            print(f"  - {pid}")
    if durations_ms:
        durations_ms.sort()
        p50 = durations_ms[len(durations_ms) // 2]
        p90 = durations_ms[int(len(durations_ms) * 0.9)]
        print(f"plan→done duration ms: p50={p50}, p90={p90}, max={max(durations_ms)}")
    print()

    print("-- Patch presence (affects viz + floating labels) --")
    if missing_patches:
        for k, v in missing_patches.most_common():
            print(f"{k:24s} missing={v}")
    else:
        print("All tx.updated events had node_patch/edge_patch")
    print(f"tx.updated with balance-ish node_patch: {label_worthy_tx}")

    print()
    print("summary.json keys:", ", ".join(sorted(summary.keys())))


if __name__ == "__main__":
    main()
