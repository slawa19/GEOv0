"""Programme 023, slice (a), optimisation round: the planner acceptance, DIAGNOSTIC PROTOCOL VERSION 2.

WHY A SECOND VERSION (spec 023, addendum to the stop condition; `specs/BACKLOG.md`, the `(T)` defect). Version 1
(`scripts/measure_p023_planner_acceptance.py`, frozen at `45c4e7d`, run `20260926T052821Z`, FAIL - kept as
evidence, not edited) measured peak memory with one extra `tracemalloc` call INSIDE the child that runs the judged
calls. Under tracing that call was silent for more than 10 s, the parent's watchdog killed the child with
`TIMEOUT: killed: silent for 10 s`, and the cell was failed for it. Version 2 changes ONLY that:

* the judged calls run in a child that does nothing else - the code is version 1's, byte for byte (checked at
  import below against version 1's source), with the memory block removed;
* peak memory is measured in a SEPARATE child with its own watchdog (`MEMORY_WATCHDOG_S`): one untraced warm-up
  call, then one call under `tracemalloc`. Its outcome never enters the timing verdict. A memory measurement
  that fails is recorded as NOT MEASURED with its reason - never as zero - and a cell whose memory is not
  measured is not reported as fully accepted (`PASS` requires both).

UNCHANGED, and asserted in code against version 1 (not copied): seed, families, variants, scopes, the amount
generator (the same function objects), sample and warm-up counts, the p95 bars, the 5 000 ms call ceiling, the
kill grace, the judged call, the per-cell summary of timings. The build and the execution-cost cell are version
1's functions, called as they are.

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p023_planner_acceptance_v2.py
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import measure_p023_planner_acceptance as v1  # noqa: E402 - sets the p023a bench DATABASE_URL
from scripts.measure_p023_planner_acceptance import base020  # noqa: E402

PROTOCOL_VERSION = 2
MEMORY_WATCHDOG_S = 600.0  # the memory child's own watchdog; its verdict is never the judged call's

# ------------------------------------------------ the frozen part of version 1, checked unchanged (not copied)

assert v1.SEED == 20200925
assert v1.FAMILIES == ("h4k", "d8k")
assert v1.VARIANTS == ("planted", "plateau", "narrow", "allequal", "largeatoms")
assert v1.SCOPES == ("global", "perimeter")
assert (v1.SAMPLES, v1.WARMUP) == (20, 2)
assert v1.P95_BAR_MS == {"h4k": 1000.0, "d8k": 2000.0}
assert (v1.MAX_CALL_MS, v1.CALL_TIMEOUT_S, v1.KILL_GRACE_S) == (5000.0, 5.0, 5.0)
assert (v1.MAX_ATOMS, v1.LARGE_SPREAD_ATOMS) == (99999999999999999999, 10**16)
assert (v1.EXECUTION_VARIANT, v1.EXECUTION_SCOPE) == ("planted", "global")


def _judged_block(source: str) -> str:
    start = source.index("    async def call(engine, which):")
    end = source.index("        vertices = {v for e in plan.edges")
    return source[start:end]


# ============================================================================== child: the judged calls only


def child_cell(url, family, variant, scope, perimeter, out) -> None:
    asyncio.run(_child_cell(url, family, variant, scope, perimeter, out))


async def _child_cell(url, family, variant, scope, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.flow_planner import plan_for_equivalent

    _plan_digest = v1._plan_digest
    SAMPLES, WARMUP = v1.SAMPLES, v1.WARMUP
    pids = set(perimeter) if scope == "perimeter" else None
    cold_engine = base020._child_engine(url, pooled=False)
    warm_engine = base020._child_engine(url, pooled=True)
    counters = {"cold": base020._Statements(cold_engine), "warm": base020._Statements(warm_engine)}
    out.put({"kind": "ready"})

    async def call(engine, which):
        before = counters[which].count
        t0 = time.perf_counter()
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as s:
            plan = await plan_for_equivalent(s, "BENCH", allowed_participant_pids=pids)
            await s.rollback()
        return (time.perf_counter() - t0) * 1000.0, counters[which].count - before, plan

    try:
        try:
            ms, stmts, plan = await call(cold_engine, "cold")
            out.put({"kind": "cold", "ms": ms, "statements": stmts, "digest": _plan_digest(plan)})
            for _ in range(WARMUP):
                ms, stmts, plan = await call(warm_engine, "warm")
                out.put({"kind": "warmup", "ms": ms, "statements": stmts, "digest": _plan_digest(plan)})
            for _ in range(SAMPLES):
                ms, stmts, plan = await call(warm_engine, "warm")
                out.put({"kind": "sample", "ms": ms, "statements": stmts, "digest": _plan_digest(plan)})
        except Exception as exc:  # noqa: BLE001 - classified by the parent as a failure, never swallowed
            out.put({"kind": "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
            return
        vertices = {v for e in plan.edges for v in (e.debtor_id, e.creditor_id)}
        lengths = sorted(len(c.edges) for c in plan.cycles)
        out.put({
            "kind": "plan", "cycles": len(plan.cycles), "longest_cycle": plan.longest_cycle,
            "cycle_lengths": {str(k): lengths.count(k) for k in sorted(set(lengths))},
            "v_edge_atoms": str(plan.v_edge), "v_cyc_atoms": str(plan.v_cyc),
            "eligible_edges": len(plan.edges), "vertices": len(vertices),
            "cleared_edges": sum(1 for e in plan.edges if plan.remaining[e.debt_id] < e.atoms),
        })
    finally:
        out.put({"kind": "done"})
        await cold_engine.dispose()
        await warm_engine.dispose()


# The judged calls are version 1's, byte for byte.
assert _judged_block(inspect.getsource(v1._child_cell)) == _judged_block(inspect.getsource(_child_cell)), (
    "version 2 must time exactly version 1's judged call"
)


# ============================================================================ child: memory, its own watchdog


def child_memory(url, scope, perimeter, out) -> None:
    asyncio.run(_child_memory(url, scope, perimeter, out))


async def _child_memory(url, scope, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.flow_planner import plan_for_equivalent

    pids = set(perimeter) if scope == "perimeter" else None
    engine = base020._child_engine(url, pooled=True)
    out.put({"kind": "ready"})
    try:
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as s:
            await plan_for_equivalent(s, "BENCH", allowed_participant_pids=pids)  # untraced warm-up
            await s.rollback()
        tracemalloc.start()
        try:
            t0 = time.perf_counter()
            async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as s:
                plan = await plan_for_equivalent(s, "BENCH", allowed_participant_pids=pids)
                await s.rollback()
            out.put({"kind": "memory", "peak_bytes": tracemalloc.get_traced_memory()[1],
                     "traced_ms": round((time.perf_counter() - t0) * 1000.0, 1), "digest": v1._plan_digest(plan)})
        finally:
            tracemalloc.stop()
    except Exception as exc:  # noqa: BLE001 - reported as NOT MEASURED, never as zero
        out.put({"kind": "memory_error", "error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        out.put({"kind": "done"})
        await engine.dispose()


def memory_outcome(messages) -> dict:
    m = next((x for x in messages if x["kind"] == "memory"), None)
    if m is not None:
        return {"memory": "MEASURED", "peak_python_bytes": m["peak_bytes"], "memory_traced_ms": m["traced_ms"],
                "memory_digest": m["digest"]}
    bad = next((x for x in messages if x["kind"] in ("memory_error", "timeout", "error")), None)
    return {"memory": "NOT MEASURED", "peak_python_bytes": None,
            "memory_reason": (bad or {}).get("error", "no memory message")}


def verdict(timing_row: dict, memory: dict) -> dict:
    row = {**timing_row, **memory}
    row["timing_verdict"] = timing_row["verdict"]  # version 1's summary of the judged calls, unchanged
    reasons = list(timing_row["fail_reasons"])
    if memory["memory"] != "MEASURED":
        reasons.append(f"memory NOT MEASURED: {memory.get('memory_reason')}")
    elif memory["memory_digest"] not in timing_row["digests"]:
        reasons.append(f"memory call planned a different plan: {memory['memory_digest']}")
    row["fail_reasons"] = reasons
    row["verdict"] = "PASS" if not reasons else "FAIL"
    row["protocol_version"] = PROTOCOL_VERSION
    return row


# ================================================================================================ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=v1.DEFAULT_SERVER_URL)
    parser.add_argument("--out", default=str(REPO_ROOT / ".local-run" / "p023a2-bench"))
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    log = open(out_dir / "run.log", "w", encoding="utf-8")

    def say(line: str) -> None:
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    results: dict = {
        "started": stamp, "protocol_version": PROTOCOL_VERSION,
        "frozen": {"seed": v1.SEED, "families": v1.FAMILIES, "variants": v1.VARIANTS, "scopes": v1.SCOPES,
                   "samples": v1.SAMPLES, "warmup": v1.WARMUP, "p95_bar_ms": v1.P95_BAR_MS,
                   "max_call_ms": v1.MAX_CALL_MS, "kill_grace_s": v1.KILL_GRACE_S,
                   "large_spread_atoms": v1.LARGE_SPREAD_ATOMS, "memory_watchdog_s": MEMORY_WATCHDOG_S},
        "manifest": {}, "cells": [], "execution": [],
    }

    def save() -> None:
        (out_dir / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    created: list[str] = []
    try:
        for family in v1.FAMILIES:
            for variant in v1.VARIANTS:
                graph = v1.variant_graph(family, variant)
                name = v1.db_name(family, variant)
                created.append(name)
                t0 = time.perf_counter()
                asyncio.run(v1.build(args.server_url, name, graph, precision=v1.precision_of(variant)))
                graph["manifest"]["build_s"] = round(time.perf_counter() - t0, 1)
                results["manifest"][f"{family}_{variant}"] = graph["manifest"]
                url = v1.database_url(args.server_url, name)
                say(f"built {name} in {graph['manifest']['build_s']} s")
                for scope in v1.SCOPES:
                    msgs = base020.run_child(child_cell, (url, family, variant, scope, graph["perimeter"]),
                                             first_timeout=v1.CHILD_STARTUP_S,
                                             call_timeout=v1.CALL_TIMEOUT_S + v1.KILL_GRACE_S)
                    timing = v1.summarize(family, variant, scope, msgs)
                    mem_msgs = base020.run_child(child_memory, (url, scope, graph["perimeter"]),
                                                 first_timeout=v1.CHILD_STARTUP_S, call_timeout=MEMORY_WATCHDOG_S)
                    row = verdict(timing, memory_outcome(mem_msgs))
                    results["cells"].append(row)
                    save()
                    say(f"{family} {variant:10} {scope:9} {row['verdict']} timing={row['timing_verdict']} "
                        f"cold={row['cold_ms']} p50={row['p50_ms']} p95={row['p95_ms']} max={row['max_ms']} "
                        f"stmts={row['statements_per_call']} cycles={row.get('cycles')} "
                        f"longest={row.get('longest_cycle')} memory={row['memory']} peak={row['peak_python_bytes']} "
                        f"{'; '.join(row['fail_reasons'])}")
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
                        asyncio.run(v1.drop_db(args.server_url, clone))
                        created.remove(clone)
                    save()
                asyncio.run(v1.drop_db(args.server_url, name))
                created.remove(name)
        cells = results["cells"]
        passed = [c for c in cells if c["verdict"] == "PASS"]
        results["verdict"] = "PASS" if cells and len(passed) == len(cells) else "FAIL"
        results["failed_cells"] = [f"{c['family']}/{c['variant']}/{c['scope']}" for c in cells if c["verdict"] != "PASS"]
        save()
        say(f"VERDICT {results['verdict']}: {len(passed)} of {len(cells)} cells PASSED; "
            f"timing failures {sum(c['timing_verdict'] != 'PASS' for c in cells)}; "
            f"memory not measured {sum(c['memory'] != 'MEASURED' for c in cells)}; failed: {results['failed_cells']}")
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
