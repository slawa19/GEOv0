"""Programme 023, slice (a): the planner acceptance, PROTOCOL VERSION 3 (spec 023, Verification plan §4, and the
section «Проспективная правка приёмки», consultation `2026-09-26-023-acceptance`).

WHAT CHANGES AGAINST VERSION 2. Only the gate and the matrix:

* THE ONLY TIMING GATE: every planning call - cold, warm-up and sample - finishes within 5 000 ms. p50 and p95
  are reported, not judged; 500 ms p95 at target scale is an OBSERVATIONAL target (reported as met / not met);
  there is no 10 s allowance. A call silent past 5 s + the 5 s kill grace is a TIMEOUT and fails its cell.
* A cell passes only with: no error or timeout (a `PlanIntegrityError` of the feasibility, reconstruction or
  certificate check inside the judged call is an error), a cold call, 20 of 20 warm samples, every call
  <= 5 000 ms, one plan digest across all calls, memory MEASURED (the v2 memory child) planning the same plan,
  and for the `empty` perimeter an empty plan. `largeatoms` is judged by exactly these rules. The run passes only
  if every cell passes; cells are never pooled.
* THE MATRIX: the 20 stress cells, unchanged (the 020 generator, seed 20200925, h4k and d8k, the five v1 variants,
  global and perimeter - built by the v1 functions), plus the TARGET matrix of `scripts/p023_target_family.py`
  (4 sizes x 2 degree distributions x 6 amount variants x 4 scopes = 192 cells). Manifest first, timing after.

WHAT DOES NOT CHANGE, asserted at import: the judged call is version 2's child process itself
(`JUDGED_CHILD is v2.child_cell`, whose judged block v2 checks byte for byte against v1 at its own import), the
memory child is v2's, v1 and v2 sources hash as at `ef3c640`, and the planner
`app/core/clearing/flow_planner.py` hashes as at `ef3c640` - an edited planner refuses to run.

WHERE IT RUNS: databases `geov0_bench_p023a3_*` only (the v1 name guard, checked before every CREATE and DROP),
created, migrated and dropped here however the run ends; never a test database. Output:
`--out/<UTC stamp>/manifest.json` (written before the first timed call), `results.json` (every raw sample),
`run.log`. The PostgreSQL postmaster start time is read before and after: a change means the server restarted
under the run (a disturbance - the whole protocol is then run again, and the disturbed run kept).

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p023_planner_acceptance_v3.py
    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p023_planner_acceptance_v3.py --manifest-only

WHAT IT DOES NOT SEE: OS-cold caches; other load on the machine; planning off the event loop, lease renewal and
cancellation (slice (c)); concurrent mutation (snapshot only, by design); the v2 executor (slice (b)).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import measure_p023_planner_acceptance as v1  # noqa: E402 - sets the p023a bench DATABASE_URL
from scripts import measure_p023_planner_acceptance_v2 as v2  # noqa: E402 - runs v2's checks against v1
from scripts import p023_target_family as target  # noqa: E402
from scripts.measure_p023_planner_acceptance import base020  # noqa: E402

# ================================================================ FROZEN BEFORE MEASUREMENT (023 protocol v3)

PROTOCOL_VERSION = 3
MAX_CALL_MS = 5000.0
OBSERVED_P95_TARGET_MS = 500.0  # target scale only; reported, never judged
BASELINE_COMMIT = "ef3c640"
#: sha256 of the LF-normalised sources at `ef3c640` (`git show ef3c640:<path> | sha256sum`).
FROZEN_SOURCES = {
    "app/core/clearing/flow_planner.py": "96f84572df4d8885cb8be95a2095fab2fde361232edbe3805f8fbaa216fa6774",
    "scripts/measure_p023_planner_acceptance.py": "e16e891acbfea0c74140d77e0503e18a096ce0f6e65d1d7b933d1511ee8dee0b",
    "scripts/measure_p023_planner_acceptance_v2.py": "8586e2c81730f8ea1b48ddbcab799b4c7f05e5124a1b601b2a9213a30c89d186",
}
STRESS_FAMILIES = v1.FAMILIES  # ("h4k", "d8k")
STRESS_VARIANTS = v1.VARIANTS  # planted, plateau, narrow, allequal, largeatoms
STRESS_SCOPES = v1.SCOPES  # global, perimeter
TARGET_GRAPHS = tuple(target.graph_ids())
TARGET_VARIANTS = v1.VARIANTS + (target.MIXED,)
TARGET_SCOPES = target.SCOPES  # global, perim_hubs, perim_nohubs, empty
EMPTY_CONTROL_SCOPE = "empty"
NAME_PREFIX = "geov0_bench_p023a3"

JUDGED_CHILD = v2.child_cell
MEMORY_CHILD = v2.child_memory

# ============================================================================================ end of frozen


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def check_sources(read=lambda rel: (REPO_ROOT / rel).read_text(encoding="utf-8")) -> None:
    for rel, expected in FROZEN_SOURCES.items():
        actual = source_sha256(read(rel))
        if actual != expected:
            raise SystemExit(f"protocol v3 refuses to run: {rel} is not the {BASELINE_COMMIT} source "
                             f"(sha256 {actual}, frozen {expected})")


check_sources()
assert JUDGED_CHILD is v2.child_cell and MEMORY_CHILD is v2.child_memory
assert v2._judged_block(v2.inspect.getsource(v1._child_cell)) == v2._judged_block(v2.inspect.getsource(v2._child_cell))
assert v1.MAX_CALL_MS == MAX_CALL_MS and v1.CALL_TIMEOUT_S == MAX_CALL_MS / 1000.0
assert (v1.SAMPLES, v1.WARMUP, v1.KILL_GRACE_S, v1.SEED) == (20, 2, 5.0, 20200925)
assert len(TARGET_GRAPHS) * len(TARGET_VARIANTS) * len(TARGET_SCOPES) == 192


# ================================================================================================ the gate


def judge(messages: list[dict], memory: dict, *, empty_control: bool = False) -> dict:
    """The v3 verdict of one cell from the judged child's messages and `v2.memory_outcome(...)`."""

    samples = [m["ms"] for m in messages if m["kind"] == "sample"]
    calls = [m for m in messages if m["kind"] in ("cold", "warmup", "sample")]
    cold = next((m for m in messages if m["kind"] == "cold"), None)
    failure = next((m for m in messages if m["kind"] in ("timeout", "error")), None)
    plan = next((m for m in messages if m["kind"] == "plan"), None)
    digests = sorted({m["digest"] for m in calls})
    p95 = sorted(samples)[math.ceil(0.95 * len(samples)) - 1] if samples else None
    over = [round(m["ms"], 1) for m in calls if m["ms"] > MAX_CALL_MS]
    row = {
        "samples_ms": [round(x, 3) for x in samples],
        "warmup_ms": [round(m["ms"], 3) for m in messages if m["kind"] == "warmup"],
        "cold_ms": round(cold["ms"], 3) if cold else None,
        "p50_ms": round(statistics.median(samples), 1) if samples else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "max_ms": round(max(m["ms"] for m in calls), 1) if calls else None,
        "calls": len(calls),
        "calls_over_max": over,
        "max_call_bar_ms": MAX_CALL_MS,
        "statements_per_call": sorted({m["statements"] for m in calls}),
        "failure": failure["kind"].upper() + ": " + failure["error"] if failure else None,
        "digests": digests,
        **({k: v for k, v in plan.items() if k != "kind"} if plan else {}),
        **memory,
    }
    reasons = []
    if failure:
        reasons.append(row["failure"])
    if cold is None:
        reasons.append("no cold call")
    if len(samples) != v1.SAMPLES:
        reasons.append(f"{len(samples)} of {v1.SAMPLES} samples")
    if over:
        reasons.append(f"calls over {MAX_CALL_MS:.0f} ms: {over}")
    if len(digests) > 1:
        reasons.append(f"non-deterministic plan: {digests}")
    if plan is None and not failure:
        reasons.append("no plan reported")
    if memory.get("memory") != "MEASURED":
        reasons.append(f"memory NOT MEASURED: {memory.get('memory_reason')}")
    elif memory.get("memory_digest") not in digests:
        reasons.append(f"memory call planned a different plan: {memory.get('memory_digest')}")
    if empty_control and plan is not None and (plan.get("eligible_edges") or plan.get("cycles")):
        reasons.append(f"empty perimeter planned {plan.get('eligible_edges')} edges, {plan.get('cycles')} cycles")
    row["fail_reasons"] = reasons
    row["verdict"] = "FAIL" if reasons else "PASS"
    row["protocol_version"] = PROTOCOL_VERSION
    return row


# ============================================================================================== manifest


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()


def stress_graph(family: str, variant: str) -> dict:
    return v1.variant_graph(family, variant)


def target_graph(graph_id: str, variant: str) -> dict:
    return target.variant_graph(graph_id, variant, v1.draw_amount, v1.precision_of)


def build_manifest() -> dict:
    """Every graph of the run, before any timing: counts, degrees, cyclic structure, exclusions, hashes."""

    out: dict = {"protocol_version": PROTOCOL_VERSION, "baseline_commit": BASELINE_COMMIT,
                 "frozen_sources": FROZEN_SOURCES, "stress": {}, "target": {}}
    for family in STRESS_FAMILIES:
        for variant in STRESS_VARIANTS:
            m = stress_graph(family, variant)["manifest"]
            out["stress"][f"{family}_{variant}"] = {**m, "manifest_sha256": manifest_sha256(m)}
    for graph_id in TARGET_GRAPHS:
        for variant in TARGET_VARIANTS:
            m = target_graph(graph_id, variant)["manifest"]
            out["target"][f"{graph_id}_{variant}"] = {**m, "manifest_sha256": manifest_sha256(m)}
    return out


# ================================================================================================ main


async def _postmaster_start(server_url: str) -> str:
    from tests.migrated_schema import maintenance_connection

    conn = await maintenance_connection(server_url)
    try:
        return str(await conn.fetchval("SELECT pg_postmaster_start_time()"))
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=v1.DEFAULT_SERVER_URL)
    parser.add_argument("--out", default=str(REPO_ROOT / ".local-run" / "p023a3-bench"))
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    print(f"manifest: {out_dir / 'manifest.json'}", flush=True)
    if args.manifest_only:
        return
    log = open(out_dir / "run.log", "w", encoding="utf-8")

    def say(line: str) -> None:
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {line}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    postmaster_before = asyncio.run(_postmaster_start(args.server_url))
    results: dict = {
        "started": stamp, "protocol_version": PROTOCOL_VERSION, "baseline_commit": BASELINE_COMMIT,
        "machine": {"platform": platform.platform(), "python": sys.version.split()[0],
                    "postmaster_start_before": postmaster_before},
        "frozen": {"max_call_ms": MAX_CALL_MS, "observed_p95_target_ms": OBSERVED_P95_TARGET_MS,
                   "samples": v1.SAMPLES, "warmup": v1.WARMUP, "kill_grace_s": v1.KILL_GRACE_S,
                   "memory_watchdog_s": v2.MEMORY_WATCHDOG_S, "stress_seed": v1.SEED,
                   "target_seed": target.TARGET_SEED, "stress_families": STRESS_FAMILIES,
                   "stress_variants": STRESS_VARIANTS, "stress_scopes": STRESS_SCOPES,
                   "target_graphs": TARGET_GRAPHS, "target_variants": TARGET_VARIANTS, "target_scopes": TARGET_SCOPES,
                   "frozen_sources": FROZEN_SOURCES},
        "cells": [], "execution": [],
    }

    def save() -> None:
        (out_dir / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    def cell(tier, family, variant, scope, url, perimeter, manifest_hash) -> None:
        child_scope = "global" if perimeter is None else "perimeter"
        members = list(perimeter or [])
        msgs = base020.run_child(JUDGED_CHILD, (url, family, variant, child_scope, members),
                                 first_timeout=v1.CHILD_STARTUP_S, call_timeout=v1.CALL_TIMEOUT_S + v1.KILL_GRACE_S)
        mem_msgs = base020.run_child(MEMORY_CHILD, (url, child_scope, members),
                                     first_timeout=v1.CHILD_STARTUP_S, call_timeout=v2.MEMORY_WATCHDOG_S)
        row = {"tier": tier, "family": family, "variant": variant, "scope": scope, "manifest_sha256": manifest_hash,
               **judge(msgs, v2.memory_outcome(mem_msgs), empty_control=(scope == EMPTY_CONTROL_SCOPE))}
        if tier == "target" and row["p95_ms"] is not None:
            row["observed_p95_target_met"] = row["p95_ms"] <= OBSERVED_P95_TARGET_MS
        results["cells"].append(row)
        save()
        say(f"{tier} {family} {variant:10} {scope:12} {row['verdict']} cold={row['cold_ms']} p50={row['p50_ms']} "
            f"p95={row['p95_ms']} max={row['max_ms']} stmts={row['statements_per_call']} edges={row.get('eligible_edges')} "
            f"cycles={row.get('cycles')} longest={row.get('longest_cycle')} memory={row['memory']} "
            f"peak={row.get('peak_python_bytes')} {'; '.join(row['fail_reasons'])}")

    created: list[str] = []

    def built(name, graph) -> str:
        created.append(name)
        t0 = time.perf_counter()
        asyncio.run(v1.build(args.server_url, name, graph, precision=graph["manifest"]["precision"]))
        say(f"built {name} in {round(time.perf_counter() - t0, 1)} s")
        return v1.database_url(args.server_url, name)

    def dropped(name) -> None:
        asyncio.run(v1.drop_db(args.server_url, name))
        created.remove(name)

    try:
        for graph_id in TARGET_GRAPHS:
            for variant in TARGET_VARIANTS:
                graph = target_graph(graph_id, variant)
                key = f"{graph_id}_{variant}"
                assert manifest_sha256(graph["manifest"]) == manifest["target"][key]["manifest_sha256"]
                name = v1.checked_bench_name(f"{NAME_PREFIX}_{key}")
                url = built(name, graph)
                for scope in TARGET_SCOPES:
                    cell("target", graph_id, variant, scope, url, graph["perimeters"][scope],
                         manifest["target"][key]["manifest_sha256"])
                dropped(name)
        for family in STRESS_FAMILIES:
            for variant in STRESS_VARIANTS:
                graph = stress_graph(family, variant)
                key = f"{family}_{variant}"
                assert manifest_sha256(graph["manifest"]) == manifest["stress"][key]["manifest_sha256"]
                assert graph["manifest"]["precision"] == v1.precision_of(variant)
                name = v1.checked_bench_name(f"{NAME_PREFIX}_{key}")
                url = built(name, graph)
                for scope in STRESS_SCOPES:
                    cell("stress", family, variant, scope, url, graph["perimeter"] if scope == "perimeter" else None,
                         manifest["stress"][key]["manifest_sha256"])
                if variant == v1.EXECUTION_VARIANT:
                    for which in ("longest", "shortest"):
                        clone = v1.checked_bench_name(f"{name}_x")
                        asyncio.run(v1.create_db(args.server_url, clone, template=name))
                        created.append(clone)
                        msgs = base020.run_child(v1.child_execute, (v1.database_url(args.server_url, clone), family, which),
                                                 first_timeout=v1.CHILD_STARTUP_S, call_timeout=600.0)
                        for m in msgs:
                            if m["kind"] in ("execution", "error", "timeout"):
                                results["execution"].append({"family": family, "variant": variant,
                                                             "scope": v1.EXECUTION_SCOPE, **m})
                                say(f"execution {family} {json.dumps(m, default=str)}")
                        dropped(clone)
                    save()
                dropped(name)
        cells = results["cells"]
        passed = [c for c in cells if c["verdict"] == "PASS"]
        postmaster_after = asyncio.run(_postmaster_start(args.server_url))
        results["machine"]["postmaster_start_after"] = postmaster_after
        results["machine"]["postgres_restarted_during_run"] = postmaster_after != postmaster_before
        results["verdict"] = "PASS" if len(cells) == 212 and len(passed) == len(cells) else "FAIL"
        results["failed_cells"] = [f"{c['tier']}/{c['family']}/{c['variant']}/{c['scope']}: {'; '.join(c['fail_reasons'])}"
                                   for c in cells if c["verdict"] != "PASS"]
        save()
        tiers = {t: (sum(c["verdict"] == "PASS" for c in cells if c["tier"] == t), sum(c["tier"] == t for c in cells))
                 for t in ("target", "stress")}
        say(f"VERDICT {results['verdict']}: {len(passed)} of {len(cells)} cells PASSED "
            f"(target {tiers['target'][0]}/{tiers['target'][1]}, stress {tiers['stress'][0]}/{tiers['stress'][1]}); "
            f"calls over {MAX_CALL_MS:.0f} ms {sum(len(c['calls_over_max']) for c in cells)}; "
            f"errors/timeouts {sum(c['failure'] is not None for c in cells)}; "
            f"memory not measured {sum(c['memory'] != 'MEASURED' for c in cells)}; "
            f"postgres restarted {results['machine']['postgres_restarted_during_run']}")
        for f in results["failed_cells"]:
            say(f"FAILED {f}")
        say(f"results: {out_dir / 'results.json'}")
    finally:
        for name in reversed(created):
            try:
                asyncio.run(v1.drop_db(args.server_url, name))
            except Exception as exc:  # noqa: BLE001 - reported, the next name is still dropped
                say(f"WARNING: could not drop {name}: {exc}")
        log.close()


if __name__ == "__main__":
    main()
