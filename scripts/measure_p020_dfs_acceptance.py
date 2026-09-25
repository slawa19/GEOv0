"""Programme 020, stage 2 supplement: acceptance of the single DFS where its amount bound is weak.

WHY (Codex §15 review of stage 2, 2026-09-25, P2-1 and P2-3). The first measurement
(`scripts/measure_p020_detector_cost.py`) planted high-value structures, so on part of its cells the
top-100 cutoff sat above the random bulk and the bound discarded most of the graph. It did not measure a
bulk of eligible edges AT OR ABOVE the cutoff, nor equal-amount plateaus, where strict `amount < floor`
prunes nothing. It also timed the bare detector function, not the public call stage 3 will make, and it
drove the simulator with full-depth discovery, not with the engine's retained ladder and rotation.

WHAT IT MEASURES, all on the single DFS as stage 3 will call it:

* CELLS - the complete wrapper `find_cycles_single_dfs` (equivalent by code, perimeter resolution, relation
  load with the production consent parser, DFS, wire rendering) on the four topologies of the first
  measurement, each in four AMOUNT VARIANTS (below), globally and in the perimeter, at depths 3/4/6/7/10.
  Per cell: cold call, 2 warmups, 20 samples (every sample kept), the full ordered identity list, the cutoff
  (the 100th candidate's amount) and how many eligible edges sit at or above it.
* REPEATED - the stage-3 `auto_clear` (`SingleDfsClearingService`: full depth on every detection, first
  success, re-detect, 101-success ceiling, production execution) on a fresh clone per run: whole-call time,
  detection time, statements.
* SIMULATOR - the REAL `RealClearingEngine.tick_real_mode_clearing` (its short-rung ladder, its per-tick
  priority rotation, its 250 ms budget, the run perimeter), with `SingleDfsClearingService` as its service
  class: per tick the depth and time of every detection (preflight and repeated) and the tick's wall time.

THE FROZEN PART is the block "FROZEN BEFORE MEASUREMENT" below, committed before the first timed run. The
thresholds are the first runner's constants, imported and checked to be unchanged: p95 <= 500 ms for the
200/2 000 topologies (uniform and skewed) at depth <= 6, p95 <= 2 s otherwise, no call > 10 s, a timeout is a
failure. EVERY variant is an acceptance cell, the all-equal control included: the review names it as the case
where the bound is deliberately ineffective, and a detector that cannot answer it in time is not accepted.

WHERE IT RUNS: databases named `geov0_bench_p020s2*` only, created and dropped here (the checks and helpers
of the first runner). Never a test database, never `GEO_TEST_ALLOW_DB_RESET`.

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p020_dfs_acceptance.py
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import math
import multiprocessing as mp  # noqa: F401 - spawn context via the base runner
import random
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import measure_p020_detector_cost as base  # noqa: E402 - sets the bench DATABASE_URL first

# ================================================================ FROZEN BEFORE MEASUREMENT (stage 2 supplement)

#: The four topologies of the first measurement, unchanged: same pairs, statuses, consents, planted positions.
TOPOLOGIES = ("u2k", "h4k", "s2k", "d8k")

#: Amount variants. Every edge - random AND planted - is re-drawn, so no planted structure keeps a superior
#: amount. Draws use `random.Random(f"{SEED}:{family}:{variant}")` over the edges in generator order.
PLATEAU_AMOUNT = "500.00"
PLATEAU_SHARE, ABOVE_SHARE = 0.60, 0.20  # the rest (0.20) below the plateau
NARROW_PALETTE = ("10.00", "10.01", "10.02", "10.03", "10.04")
ALL_EQUAL_AMOUNT = "10.00"
VARIANTS = ("planted", "plateau", "narrow", "allequal")


def draw_amount(variant: str, rnd: random.Random, original: str) -> str:
    if variant == "planted":
        return original  # the first measurement's amounts, re-timed through the wrapper
    if variant == "plateau":
        roll = rnd.random()
        if roll < PLATEAU_SHARE:
            return PLATEAU_AMOUNT
        if roll < PLATEAU_SHARE + ABOVE_SHARE:
            return str(Decimal(rnd.randrange(50001, 100000)) / 100)  # 500.01 .. 999.99
        return str(Decimal(rnd.randrange(1, 50000)) / 100)  # 0.01 .. 499.99
    if variant == "narrow":
        return rnd.choice(NARROW_PALETTE)
    if variant == "allequal":
        return ALL_EQUAL_AMOUNT
    raise ValueError(variant)


DEPTHS = base.DEPTHS  # (3, 4, 6, 7, 10)
SCOPES = base.SCOPES  # ("global", "perimeter")
SAMPLES = 20
WARMUP = 2
CALL_TIMEOUT_S = 10.0
KILL_GRACE_S = 5.0
LIMIT = 100

REPEATED_DEPTH = 6
REPEATED_RUNS = 2
REPEATED_RUN_CAP_S = 300.0
REDIS_LEASE_S = 30.0

SIM_DEPTH = 6
SIM_BUDGET_MS = 250
SIM_TICKS = 5

# The thresholds are the first runner's, unchanged. Checked, not copied.
assert (base.P95_MS_SMALL, base.P95_MS_LARGE, base.MAX_CALL_S) == (500.0, 2000.0, 10.0)
assert (base.SAMPLES, base.WARMUP, base.CALL_TIMEOUT_S) == (SAMPLES, WARMUP, CALL_TIMEOUT_S)

# =============================================================================================== graphs


def variant_graph(family: str, variant: str) -> dict:
    graph = base.generate(family)
    rnd = random.Random(f"{base.SEED}:{family}:{variant}")
    for e in graph["edges"]:
        e["amount"] = draw_amount(variant, rnd, e["amount"])
    amounts = [Decimal(e["amount"]) for e in graph["edges"]]
    graph["manifest"].update(
        {
            "variant": variant,
            "distinct_amounts": len(set(amounts)),
            "edges_at_plateau": sum(1 for a in amounts if a == Decimal(PLATEAU_AMOUNT)),
            "amount_min": str(min(amounts)),
            "amount_max": str(max(amounts)),
        }
    )
    return graph


def db_name(family: str, variant: str) -> str:
    return base.checked_bench_name(f"geov0_bench_p020s2_{family}_{variant}")


def eligible_amounts(graph: dict, scope: str) -> list[Decimal]:
    from app.core.clearing.service import ClearingService

    perimeter = set(graph["perimeter"]) if scope == "perimeter" else None
    out = []
    for e in graph["edges"]:
        if e["status"] not in ("active", "frozen"):
            continue
        c = e["consent"]
        policy = None if c == "<null-policy>" else ({} if c == "<missing-key>" else {"auto_clearing": c})
        if not ClearingService._policy_flag(policy, "auto_clearing", default=True):
            continue
        if perimeter is not None and not (e["debtor"] in perimeter and e["creditor"] in perimeter):
            continue
        out.append(Decimal(e["amount"]))
    return out


# ================================================================================ child: one cell


def child_cell(url, family, depth, scope, perimeter, out) -> None:
    asyncio.run(_child_cell(url, family, depth, scope, perimeter, out))


async def _child_cell(url, family, depth, scope, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from scripts.p020_experimental_detectors import find_cycles_single_dfs

    scope_pids = set(perimeter) if scope == "perimeter" else None
    cold_engine = base._child_engine(url, pooled=False)
    warm_engine = base._child_engine(url, pooled=True)
    counters = {"cold": base._Statements(cold_engine), "warm": base._Statements(warm_engine)}
    out.put({"kind": "ready"})

    async def call(engine, which):
        before = counters[which].count
        t0 = time.perf_counter()
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as s:
            cycles = await find_cycles_single_dfs(s, "BENCH", depth, allowed_participant_pids=scope_pids)
            await s.rollback()
        return (time.perf_counter() - t0) * 1000.0, counters[which].count - before, cycles

    try:
        try:
            ms, stmts, cycles = await call(cold_engine, "cold")
            out.put({"kind": "cold", "ms": ms, "statements": stmts})
            for _ in range(WARMUP):
                ms, stmts, cycles = await call(warm_engine, "warm")
                out.put({"kind": "warmup", "ms": ms, "statements": stmts})
            for _ in range(SAMPLES):
                ms, stmts, cycles = await call(warm_engine, "warm")
                out.put({"kind": "sample", "ms": ms, "statements": stmts})
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed
            out.put({"kind": "timeout" if base._is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
            return
        rows = [
            [str(min(Decimal(e["amount"]) for e in c)), sorted(str(uuid.UUID(e["debt_id"])) for e in c)]
            for c in cycles
        ]
        out.put({"kind": "identities", "rows": rows})
    finally:
        out.put({"kind": "done"})
        await cold_engine.dispose()
        await warm_engine.dispose()


def summarize(family, variant, depth, scope, messages, graph) -> dict:
    samples = [m["ms"] for m in messages if m["kind"] == "sample"]
    all_calls = [m["ms"] for m in messages if m["kind"] in ("cold", "warmup", "sample")]
    cold = next((m for m in messages if m["kind"] == "cold"), None)
    failure = next((m for m in messages if m["kind"] in ("timeout", "error")), None)
    ids = next((m for m in messages if m["kind"] == "identities"), None)
    bar = base.p95_bar_ms(family, depth)
    p95 = round(sorted(samples)[math.ceil(0.95 * len(samples)) - 1], 1) if len(samples) == SAMPLES else None
    maxv = round(max(all_calls), 1) if all_calls else None
    eligible = eligible_amounts(graph, scope)
    cutoff = None
    if ids and len(ids["rows"]) == LIMIT:
        cutoff = ids["rows"][-1][0]
    row = {
        "family": family, "variant": variant, "scope": scope, "depth": depth, "impl": "wrapper",
        "samples_ms": [round(x, 2) for x in samples],
        "cold_ms": round(cold["ms"], 1) if cold else None,
        "p50_ms": round(statistics.median(samples), 1) if samples else None,
        "p95_ms": p95, "max_ms": maxv,
        "statements_per_call": sorted({m["statements"] for m in messages if m["kind"] in ("cold", "warmup", "sample")}),
        "failure": failure["kind"].upper() + ": " + failure["error"] if failure else None,
        "cycles": len(ids["rows"]) if ids else None,
        "cutoff": cutoff,
        "eligible_edges": len(eligible),
        "eligible_at_or_above_cutoff": sum(1 for a in eligible if a >= Decimal(cutoff)) if cutoff else None,
        "cutoff_on_plateau": (cutoff == PLATEAU_AMOUNT) if (cutoff and variant == "plateau") else None,
        "identities": ids["rows"] if ids else None,
        "p95_bar_ms": bar,
    }
    ok = failure is None and p95 is not None and p95 <= bar and maxv is not None and maxv <= base.MAX_CALL_S * 1000.0
    row["verdict"] = "PASS" if ok else "FAIL"
    return row


# ============================================================================ child: repeated auto_clear


def child_repeated(url, family, perimeter, out) -> None:
    asyncio.run(_child_repeated(url, out))


async def _child_repeated(url, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from scripts.p020_experimental_detectors import single_dfs_service_class

    engine = base._child_engine(url, pooled=True)
    stmts = base._Statements(engine)
    out.put({"kind": "ready"})
    det = {"n": 0, "ms": 0.0, "statements": 0, "per_call_ms": []}
    t0 = time.perf_counter()
    result: dict = {}
    try:
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as session:
            service = single_dfs_service_class()(session)
            real_find = service.find_cycles

            async def timed_find(*a, **kw):
                s0, d0 = stmts.count, time.perf_counter()
                try:
                    return await real_find(*a, **kw)
                finally:
                    ms = (time.perf_counter() - d0) * 1000.0
                    det["n"] += 1
                    det["ms"] += ms
                    det["per_call_ms"].append(round(ms, 1))
                    det["statements"] += stmts.count - s0

            service.find_cycles = timed_find
            cleared = await service.auto_clear("BENCH", max_depth=REPEATED_DEPTH)
            result = {"kind": "result", "cleared": cleared}
    except Exception as exc:  # noqa: BLE001
        result = {"kind": "timeout" if base._is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        result.update({"total_ms": (time.perf_counter() - t0) * 1000.0, "detections": det["n"],
                       "detection_ms": det["ms"], "detection_statements": det["statements"],
                       "detection_per_call_ms": det["per_call_ms"], "total_statements": stmts.count})
        out.put(result)
        out.put({"kind": "done"})
        await engine.dispose()


# =============================================================================== child: the real engine


def child_simulator(url, family, perimeter, out) -> None:
    asyncio.run(_child_simulator(url, family, perimeter, out))


async def _child_simulator(url, family, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.simulator.models import RunRecord
    from app.core.simulator.real_clearing_engine import RealClearingEngine
    from scripts.p020_experimental_detectors import single_dfs_service_class
    from tests.unit.test_real_clearing_engine_partial_failure import _EdgePatchBuilder, _SseCapture, _VizHelper

    engine = base._child_engine(url, pooled=True)
    stmts = base._Statements(engine)
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    quiet = logging.getLogger("p020.bench.engine")
    quiet.setLevel(logging.CRITICAL)
    finds: list[dict] = []
    executes = {"n": 0}
    service_base = single_dfs_service_class()

    class Measured(service_base):
        async def find_cycles(self, equivalent_code, max_depth=6, *, allowed_participant_pids=None):
            t0 = time.perf_counter()
            found = await super().find_cycles(equivalent_code, max_depth, allowed_participant_pids=allowed_participant_pids)
            finds.append({"depth": int(max_depth), "ms": round((time.perf_counter() - t0) * 1000.0, 1), "cycles": len(found)})
            return found

        async def execute_clearing_with_amount(self, cycle, *, allowed_participant_pids=None):
            executes["n"] += 1
            return await super().execute_clearing_with_amount(cycle, allowed_participant_pids=allowed_participant_pids)

    run = RunRecord(run_id=f"p020-bench-{family}", scenario_id="bench", mode="real", state="running")
    run._real_participants = [(base._uid(family, "p", p), p) for p in perimeter]
    run._real_viz_by_eq["BENCH"] = _VizHelper()
    run._edges_by_equivalent = {"BENCH": []}
    sim = RealClearingEngine(
        lock=threading.RLock(), sse=_SseCapture(), utc_now=lambda: datetime.now(timezone.utc), logger=quiet,
        edge_patch_builder=_EdgePatchBuilder(), clearing_max_depth_limit=SIM_DEPTH,
        clearing_max_fx_edges_limit=8, real_clearing_time_budget_ms=SIM_BUDGET_MS,
    )

    async def _growth(**_kw):
        return SimpleNamespace(updated_count=0)

    async def _patch(**_kw):
        return []

    out.put({"kind": "ready"})
    try:
        for tick in range(SIM_TICKS):
            run.tick_index = tick
            finds.clear()
            executes["n"] = 0
            s0, t0 = stmts.count, time.perf_counter()
            cleared = await sim.tick_real_mode_clearing(
                None, run_id=run.run_id, run=run, equivalents=["BENCH"], apply_trust_growth=_growth,
                build_edge_patch_for_equivalent=_patch, broadcast_topology_edge_patch=lambda **_kw: None,
                async_session_local=sessions, clearing_service_cls=Measured,
            )
            out.put({"kind": "tick", "tick": tick, "tick_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                     "finds": list(finds), "preflight_ms": finds[0]["ms"] if finds else None,
                     "executes": executes["n"], "cleared_amount": str(cleared.get("BENCH")),
                     "statements": stmts.count - s0})
    except Exception as exc:  # noqa: BLE001
        out.put({"kind": "timeout" if base._is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        out.put({"kind": "done"})
        await engine.dispose()


# ================================================================================================ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=base.DEFAULT_SERVER_URL)
    parser.add_argument("--out", default=str(REPO_ROOT / ".local-run" / "p020s2-accept"))
    parser.add_argument("--topologies", default=",".join(TOPOLOGIES))
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--skip-cells", action="store_true")
    parser.add_argument("--skip-repeated", action="store_true")
    args = parser.parse_args()
    topologies = [t for t in args.topologies.split(",") if t]
    variants = [v for v in args.variants.split(",") if v]
    assert set(topologies) <= set(TOPOLOGIES) and set(variants) <= set(VARIANTS)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict = {"started": stamp, "frozen": {
        "topologies": TOPOLOGIES, "variants": VARIANTS, "plateau": [PLATEAU_AMOUNT, PLATEAU_SHARE, ABOVE_SHARE],
        "narrow": NARROW_PALETTE, "allequal": ALL_EQUAL_AMOUNT, "depths": DEPTHS, "scopes": SCOPES,
        "samples": SAMPLES, "warmup": WARMUP, "call_timeout_s": CALL_TIMEOUT_S, "limit": LIMIT,
        "p95_ms_small": base.P95_MS_SMALL, "p95_ms_large": base.P95_MS_LARGE, "max_call_s": base.MAX_CALL_S},
        "cells": [], "repeated": [], "simulator": [], "manifest": {}}

    def save():
        with gzip.open(out_dir / "results.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(results, fh, default=str)

    created: list[str] = []
    try:
        for family in topologies:
            for variant in variants:
                graph = variant_graph(family, variant)
                name = db_name(family, variant)
                created.append(name)
                t0 = time.perf_counter()
                asyncio.run(base.build(args.server_url, name, graph))
                graph["manifest"]["build_s"] = round(time.perf_counter() - t0, 1)
                results["manifest"][f"{family}_{variant}"] = graph["manifest"]
                url = base.database_url(args.server_url, name)
                print(f"built {name}", flush=True)

                if not args.skip_cells:
                    for scope in SCOPES:
                        for depth in DEPTHS:
                            msgs = base.run_child(child_cell, (url, family, depth, scope, graph["perimeter"]),
                                                  first_timeout=base.CHILD_STARTUP_S, call_timeout=CALL_TIMEOUT_S + KILL_GRACE_S)
                            row = summarize(family, variant, depth, scope, msgs, graph)
                            results["cells"].append(row)
                            print(f"{family} {variant:8} {scope:9} d{depth:<2} {row['verdict']} cold={row['cold_ms']} "
                                  f"p50={row['p50_ms']} p95={row['p95_ms']} max={row['max_ms']} stmts={row['statements_per_call']} "
                                  f"cycles={row['cycles']} cutoff={row['cutoff']} ge={row['eligible_at_or_above_cutoff']}/"
                                  f"{row['eligible_edges']} {row['failure'] or ''}", flush=True)
                    save()

                if not args.skip_repeated:
                    for run_no in range(REPEATED_RUNS + 1):
                        clone = base.checked_bench_name(f"{name}_c")
                        asyncio.run(base.create_db(args.server_url, clone, template=name))
                        created.append(clone)
                        curl = base.database_url(args.server_url, clone)
                        if run_no < REPEATED_RUNS:
                            msgs = base.run_child(child_repeated, (curl, family, graph["perimeter"]),
                                                  first_timeout=base.CHILD_STARTUP_S, call_timeout=REPEATED_RUN_CAP_S)
                            res = next((m for m in msgs if m["kind"] in ("result", "timeout", "error")), msgs[-1])
                            if "total_ms" not in res:
                                # Killed by the parent: the duration is known only as a lower bound.
                                res = {**res, "total_ms_lower_bound": REPEATED_RUN_CAP_S * 1000.0}
                            lease = (res["total_ms"] > REDIS_LEASE_S * 1000.0) if "total_ms" in res else "unknown (lower bound > lease)"
                            entry = {"family": family, "variant": variant, "run": run_no, **res, "exceeds_redis_lease": lease}
                            results["repeated"].append(entry)
                            print(f"repeated {family} {variant} run{run_no} {json.dumps({k: v for k, v in entry.items() if k != 'detection_per_call_ms'}, default=str)}", flush=True)
                        else:
                            msgs = base.run_child(child_simulator, (curl, family, graph["perimeter"]),
                                                  first_timeout=base.CHILD_STARTUP_S, call_timeout=REPEATED_RUN_CAP_S)
                            for m in msgs:
                                if m["kind"] in ("tick", "timeout", "error"):
                                    results["simulator"].append({"family": family, "variant": variant, **m})
                                    print(f"simulator {family} {variant} {json.dumps(m, default=str)}", flush=True)
                        asyncio.run(base.drop_db(args.server_url, clone))
                        created.remove(clone)
                    save()
                asyncio.run(base.drop_db(args.server_url, name))
                created.remove(name)
        save()
        print(f"results: {out_dir / 'results.json.gz'}")
    finally:
        for name in reversed(created):
            try:
                asyncio.run(base.drop_db(args.server_url, name))
            except Exception as exc:  # noqa: BLE001
                print(f"WARNING: could not drop {name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
