"""Programme 023, slice (a): the FROZEN acceptance measurement of the MTCS planner (spec, Verification plan §4).

WHAT IS JUDGED. The complete planning call `app.core.clearing.flow_planner.plan_for_equivalent` - snapshot reads
(equivalent, perimeter, eligible edges with the production consent parser) + capacity-scaling solver +
decomposition + feasibility check + decomposition check + optimality certificate - timed from opening the
session to its rollback, per cell. Nothing is measured without the decomposition and the checks (spec §6).

THE FROZEN PART is the block "FROZEN BEFORE MEASUREMENT" below. It was committed, alone, BEFORE the first timed
run of this runner, and it is not edited afterwards: the thresholds are the spec's text verbatim (§4:
"p95 ≤ 1 000 мс на 400/4 000, ≤ 2 000 мс на 200/8 000, ни один вызов > 5 000 мс"; "таймаут — провал"; "≥ 20
тёплых замеров на ячейку, p95 nearest-rank, холодный отдельно"). Read as:

* a cell's family `h4k` (400 random vertices / 4 000 random edges) has the 1 000 ms p95 bar, `d8k` (200 / 8 000)
  the 2 000 ms bar; the planted structures of the 020 generator come on top of the random edges, as in 020;
* p95 is nearest-rank over the 20 warm samples (the 19th of 20 sorted); EVERY call - cold, warm-up and sample -
  must finish within 5 000 ms; a call that does not report within 5 000 ms + a 5 s kill grace is a TIMEOUT
  and fails its cell; a cell that failed skips its remaining samples (it has already failed);
* a cell passes only if it has no failure, 20 samples, p95 <= its bar and max <= 5 000 ms; failed cells are
  NEVER averaged or pooled with passing ones - each cell has its own verdict, and the run passes only if
  every acceptance cell passes.

THE GRAPHS. The 020 families (`scripts/measure_p020_detector_cost.py::generate`, seed 20200925, imported, not
modified; its `graph_sha256` is recorded): `h4k` and `d8k`, each in the four amount variants of the 020
supplement (`scripts/measure_p020_dfs_acceptance.py::draw_amount`: planted, plateau, narrow, allequal) and one
more, `largeatoms` - every amount redrawn uniformly within 10^16 atoms below the `Numeric(20,8)` maximum
(999 899 999 999.99999999 .. 999 999 999 999.99999999), so capacities are ~10^20 atoms with no common divisor.
Each graph is planned globally and inside the 020 perimeter. 2 families x 5 variants x 2 scopes = 20 cells.

REPORTED, NOT JUDGED. Peak Python memory of one extra call under `tracemalloc` (after the timed samples, never
timed itself), statements per call, cycle count, longest cycle, V_edge and V_cyc (atoms), the plan's digest
(identical on every call, or the cell fails as non-deterministic), eligible edges and vertices. And the
EXECUTION COST (spec §4: "стоимость исполнения длинного цикла — отдельной ячейкой"): on a fresh clone of the
`planted` database of each family, the longest and the shortest planned cycle are executed ONE AT A TIME through
today's production executor (`ClearingService.execute_clearing_with_amount`, which clears the locked minimum -
the declared amount is slice (b)); wall time and statements per execution are reported.

WHERE IT RUNS: databases named `geov0_bench_p023a*` only, checked against that pattern before every CREATE and
DROP, created, migrated (`alembic upgrade head`) and dropped here however the run ends. Never a test database,
never `GEO_TEST_ALLOW_DB_RESET`. Server: `--server-url` (default the `postgres` maintenance database on
127.0.0.1). Output: `--out/<UTC stamp>/results.json` (every raw sample) and `run.log`.

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p023_planner_acceptance.py

WHAT IT DOES NOT SEE. OS-cold caches; other load on the machine; the planner's behaviour under concurrent
mutation (snapshot only, by design); the v2 executor (slice (b)).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import measure_p020_detector_cost as base020  # noqa: E402 - the 020 generator, unmodified
from scripts import measure_p020_dfs_acceptance as acc020  # noqa: E402 - the 020 amount variants, unmodified

# The 020 modules name their own bench database at import; nothing here may reach it. `app.config` is not yet
# imported (neither 020 module imports the application at module level), so this is the value it will read.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_bench_p023a"

# ================================================================ FROZEN BEFORE MEASUREMENT (023 slice (a))

SEED = base020.SEED  # 20200925
FAMILIES = ("h4k", "d8k")  # 400/4 000 and 200/8 000 (020 names)
VARIANTS = ("planted", "plateau", "narrow", "allequal", "largeatoms")
SCOPES = ("global", "perimeter")

MAX_ATOMS = 99999999999999999999  # 999999999999.99999999
LARGE_SPREAD_ATOMS = 10**16

SAMPLES = 20
WARMUP = 2
KILL_GRACE_S = 5.0
CHILD_STARTUP_S = 180.0

#: Spec 023 §4, verbatim: "p95 ≤ 1 000 мс на 400/4 000, ≤ 2 000 мс на 200/8 000, ни один вызов > 5 000 мс".
P95_BAR_MS = {"h4k": 1000.0, "d8k": 2000.0}
MAX_CALL_MS = 5000.0
CALL_TIMEOUT_S = MAX_CALL_MS / 1000.0  # a call silent past this + KILL_GRACE_S is a TIMEOUT: a failure

EXECUTION_VARIANT = "planted"  # the execution-cost cell: reported, not judged
EXECUTION_SCOPE = "global"


def draw_amount(variant: str, rnd: random.Random, original: str) -> str:
    if variant == "largeatoms":
        atoms = MAX_ATOMS - rnd.randrange(LARGE_SPREAD_ATOMS)
        return format(Decimal(atoms).scaleb(-8), "f")
    return acc020.draw_amount(variant, rnd, original)


def precision_of(variant: str) -> int:
    return 8 if variant == "largeatoms" else 2


# ============================================================================================ end of frozen

BENCH_NAME_RE = re.compile(r"^geov0_bench_p023a[a-z0-9_]*$")
DEFAULT_SERVER_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/postgres"


def checked_bench_name(name: str) -> str:
    if not BENCH_NAME_RE.fullmatch(name) or len(name) > 63:
        raise SystemExit(f"refusing database name {name!r}: only geov0_bench_p023a* is ever created or dropped")
    return name


def database_url(server_url: str, name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(server_url).set(database=checked_bench_name(name)).render_as_string(hide_password=False)


async def _drop(conn, name: str) -> None:
    name = checked_bench_name(name)
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()", name
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


async def create_db(server_url: str, name: str, *, template: str | None = None) -> None:
    from tests.migrated_schema import maintenance_connection

    conn = await maintenance_connection(server_url)
    try:
        await _drop(conn, name)
        statement = f'CREATE DATABASE "{checked_bench_name(name)}"'
        if template is not None:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                checked_bench_name(template),
            )
            statement += f' TEMPLATE "{checked_bench_name(template)}"'
        await conn.execute(statement)
    finally:
        await conn.close()


async def drop_db(server_url: str, name: str) -> None:
    from tests.migrated_schema import maintenance_connection

    conn = await maintenance_connection(server_url)
    try:
        await _drop(conn, name)
    finally:
        await conn.close()


# ================================================================================================ graphs


def variant_graph(family: str, variant: str) -> dict:
    graph = base020.generate(family)
    rnd = random.Random(f"{SEED}:{family}:{variant}")
    for e in graph["edges"]:
        e["amount"] = draw_amount(variant, rnd, e["amount"])
    amounts = [Decimal(e["amount"]) for e in graph["edges"]]
    graph["manifest"].update(
        {
            "variant": variant,
            "precision": precision_of(variant),
            "distinct_amounts": len(set(amounts)),
            "amount_min": str(min(amounts)),
            "amount_max": str(max(amounts)),
            "variant_sha256": hashlib.sha256(json.dumps(graph["edges"], sort_keys=True, default=str).encode()).hexdigest(),
        }
    )
    return graph


def db_name(family: str, variant: str) -> str:
    return checked_bench_name(f"geov0_bench_p023a_{family}_{variant}")


async def build(server_url: str, name: str, graph: dict, *, precision: int) -> None:
    """Create, migrate and fill one database: the 020 build with this runner's names, the variant's precision, and
    trust-line limits at the column maximum (the `largeatoms` debts exceed the 020 limit of 1 000 000; the
    planner does not read limits)."""

    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.ledger.book import Book, NewDebt, operation_for
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.migrated_schema import run_alembic_upgrade_head

    uid = base020._uid
    await create_db(server_url, name)
    url = database_url(server_url, name)
    run_alembic_upgrade_head(url)
    engine = create_async_engine(url, poolclass=NullPool)
    family = graph["family"]
    try:
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as session:

            def assign_debt_ids(sync_session, _ctx, _instances):
                for obj in sync_session.new:
                    if isinstance(obj, Debt) and obj.id is None:
                        obj.id = uid(family, "debt", obj.debtor_id, obj.creditor_id)

            event.listen(session.sync_session, "before_flush", assign_debt_ids)
            eq_id = uid(family, "equivalent")
            session.add(Equivalent(id=eq_id, code="BENCH", symbol="B", precision=precision, is_active=True))
            for v in graph["vertices"]:
                session.add(Participant(id=uid(family, "p", v), pid=v, display_name=v, type="person",
                                        public_key=hashlib.sha256(f"{family}:{v}".encode()).hexdigest(),
                                        status="active"))
            await session.flush()
            for e in graph["edges"]:
                c = e["consent"]
                policy = None if c == "<null-policy>" else ({} if c == "<missing-key>" else {"auto_clearing": c})
                session.add(TrustLine(id=uid(family, "tl", e["creditor"], e["debtor"]),
                                      from_participant_id=uid(family, "p", e["creditor"]),
                                      to_participant_id=uid(family, "p", e["debtor"]),
                                      equivalent_id=eq_id, limit=Decimal("999999999999.99999999"), policy=policy,
                                      status=e["status"]))
            await session.flush()
            async with Book.operation(
                session,
                operation_for("SEED", f"p023a_bench:{family}:{SEED}", {"script": "measure_p023_planner_acceptance"},
                              scope_equivalent_ids=None),
            ) as posting:
                for k, e in enumerate(graph["edges"]):
                    await posting.apply(NewDebt(debtor_id=uid(family, "p", e["debtor"]),
                                                creditor_id=uid(family, "p", e["creditor"]),
                                                equivalent_id=eq_id, amount=Decimal(e["amount"])))
                    if k % 500 == 499:
                        await session.flush()
            await session.commit()
            for table in ("debts", "trust_lines", "participants", "equivalents"):
                await session.execute(text(f"ANALYZE {table}"))
            await session.commit()
    finally:
        await engine.dispose()


# ================================================================================= child: one cell


def _plan_digest(plan) -> str:
    rows = [[c.atoms, [str(e.debt_id) for e in c.edges]] for c in plan.cycles]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def child_cell(url, family, variant, scope, perimeter, out) -> None:
    asyncio.run(_child_cell(url, family, variant, scope, perimeter, out))


async def _child_cell(url, family, variant, scope, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.flow_planner import plan_for_equivalent

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
        tracemalloc.start()
        try:
            async with async_sessionmaker(bind=warm_engine, class_=AsyncSession, expire_on_commit=False)() as s:
                await plan_for_equivalent(s, "BENCH", allowed_participant_pids=pids)
                await s.rollback()
            out.put({"kind": "memory", "peak_bytes": tracemalloc.get_traced_memory()[1]})
        finally:
            tracemalloc.stop()
    finally:
        out.put({"kind": "done"})
        await cold_engine.dispose()
        await warm_engine.dispose()


def summarize(family, variant, scope, messages) -> dict:
    samples = [m["ms"] for m in messages if m["kind"] == "sample"]
    calls = [m for m in messages if m["kind"] in ("cold", "warmup", "sample")]
    cold = next((m for m in messages if m["kind"] == "cold"), None)
    failure = next((m for m in messages if m["kind"] in ("timeout", "error")), None)
    plan = next((m for m in messages if m["kind"] == "plan"), None)
    memory = next((m for m in messages if m["kind"] == "memory"), None)
    digests = sorted({m["digest"] for m in calls})
    bar = P95_BAR_MS[family]
    p95 = sorted(samples)[math.ceil(0.95 * len(samples)) - 1] if len(samples) == SAMPLES else None
    maxv = max((m["ms"] for m in calls), default=None)
    over = [round(m["ms"], 1) for m in calls if m["ms"] > MAX_CALL_MS]
    row = {
        "family": family, "variant": variant, "scope": scope,
        "samples_ms": [round(x, 3) for x in samples],
        "warmup_ms": [round(m["ms"], 3) for m in messages if m["kind"] == "warmup"],
        "cold_ms": round(cold["ms"], 3) if cold else None,
        "p50_ms": round(statistics.median(samples), 1) if samples else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "max_ms": round(maxv, 1) if maxv is not None else None,
        "calls_over_max": over,
        "statements_per_call": sorted({m["statements"] for m in calls}),
        "failure": failure["kind"].upper() + ": " + failure["error"] if failure else None,
        "digests": digests,
        "peak_python_bytes": memory["peak_bytes"] if memory else None,
        "p95_bar_ms": bar, "max_call_bar_ms": MAX_CALL_MS,
        **({k: v for k, v in plan.items() if k != "kind"} if plan else {}),
    }
    reasons = []
    if failure:
        reasons.append(row["failure"])
    if len(samples) != SAMPLES:
        reasons.append(f"{len(samples)} of {SAMPLES} samples")
    if p95 is not None and p95 > bar:
        reasons.append(f"p95 {p95:.1f} ms > {bar:.0f} ms")
    if over:
        reasons.append(f"calls over {MAX_CALL_MS:.0f} ms: {over}")
    if len(digests) > 1:
        reasons.append(f"non-deterministic plan: {digests}")
    row["verdict"] = "FAIL" if reasons else "PASS"
    row["fail_reasons"] = reasons
    return row


# ======================================================================== child: execution cost (reported)


def child_execute(url, family, which, out) -> None:
    asyncio.run(_child_execute(url, family, which, out))


async def _child_execute(url, family, which, out) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.flow_planner import plan_for_equivalent
    from app.core.clearing.service import ClearingService
    from app.db.models.participant import Participant
    from app.utils.money import to_money_str

    engine = base020._child_engine(url, pooled=True)
    stmts = base020._Statements(engine)
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    out.put({"kind": "ready"})
    try:
        async with sessions() as s:
            plan = await plan_for_equivalent(s, "BENCH")
            pids = {r.id: r.pid for r in (await s.execute(select(Participant.id, Participant.pid))).all()}
            await s.rollback()
        ordered = sorted(plan.cycles, key=lambda c: (len(c.edges), [str(e.debt_id) for e in c.edges]))
        cycle = ordered[-1] if which == "longest" else ordered[0]
        wire = [
            {"debt_id": str(e.debt_id), "debtor": pids[e.debtor_id], "creditor": pids[e.creditor_id],
             "amount": to_money_str(Decimal(e.atoms).scaleb(-8), 2)}
            for e in cycle.edges
        ]
        async with sessions() as s:
            s0, t0 = stmts.count, time.perf_counter()
            cleared = await ClearingService(s).execute_clearing_with_amount(wire)
            ms = (time.perf_counter() - t0) * 1000.0
        out.put({"kind": "execution", "which": which, "length": len(cycle.edges), "ms": round(ms, 1),
                 "statements": stmts.count - s0, "cleared": None if cleared is None else str(cleared),
                 "planned_amount": str(Decimal(cycle.atoms).scaleb(-8))})
    except Exception as exc:  # noqa: BLE001 - reported
        out.put({"kind": "error", "which": which, "error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        out.put({"kind": "done"})
        await engine.dispose()


# ================================================================================================ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--out", default=str(REPO_ROOT / ".local-run" / "p023a-bench"))
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
        "started": stamp,
        "frozen": {"seed": SEED, "families": FAMILIES, "variants": VARIANTS, "scopes": SCOPES,
                   "samples": SAMPLES, "warmup": WARMUP, "p95_bar_ms": P95_BAR_MS, "max_call_ms": MAX_CALL_MS,
                   "kill_grace_s": KILL_GRACE_S, "large_spread_atoms": LARGE_SPREAD_ATOMS},
        "manifest": {}, "cells": [], "execution": [],
    }

    def save() -> None:
        (out_dir / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    created: list[str] = []
    try:
        for family in FAMILIES:
            for variant in VARIANTS:
                graph = variant_graph(family, variant)
                name = db_name(family, variant)
                created.append(name)
                t0 = time.perf_counter()
                asyncio.run(build(args.server_url, name, graph, precision=precision_of(variant)))
                graph["manifest"]["build_s"] = round(time.perf_counter() - t0, 1)
                results["manifest"][f"{family}_{variant}"] = graph["manifest"]
                url = database_url(args.server_url, name)
                say(f"built {name} in {graph['manifest']['build_s']} s")
                for scope in SCOPES:
                    msgs = base020.run_child(child_cell, (url, family, variant, scope, graph["perimeter"]),
                                             first_timeout=CHILD_STARTUP_S, call_timeout=CALL_TIMEOUT_S + KILL_GRACE_S)
                    row = summarize(family, variant, scope, msgs)
                    results["cells"].append(row)
                    save()
                    say(f"{family} {variant:10} {scope:9} {row['verdict']} cold={row['cold_ms']} p50={row['p50_ms']} "
                        f"p95={row['p95_ms']} max={row['max_ms']} stmts={row['statements_per_call']} "
                        f"cycles={row.get('cycles')} longest={row.get('longest_cycle')} "
                        f"peak={row.get('peak_python_bytes')} {'; '.join(row['fail_reasons'])}")
                if variant == EXECUTION_VARIANT:
                    for which in ("longest", "shortest"):
                        clone = checked_bench_name(f"{name}_x")
                        asyncio.run(create_db(args.server_url, clone, template=name))
                        created.append(clone)
                        msgs = base020.run_child(child_execute, (database_url(args.server_url, clone), family, which),
                                                 first_timeout=CHILD_STARTUP_S, call_timeout=600.0)
                        for m in msgs:
                            if m["kind"] in ("execution", "error", "timeout"):
                                results["execution"].append({"family": family, "variant": variant,
                                                             "scope": EXECUTION_SCOPE, **m})
                                say(f"execution {family} {json.dumps(m, default=str)}")
                        asyncio.run(drop_db(args.server_url, clone))
                        created.remove(clone)
                    save()
                asyncio.run(drop_db(args.server_url, name))
                created.remove(name)
        cells = results["cells"]
        results["verdict"] = "PASS" if cells and all(c["verdict"] == "PASS" for c in cells) else "FAIL"
        results["failed_cells"] = [f"{c['family']}/{c['variant']}/{c['scope']}" for c in cells if c["verdict"] != "PASS"]
        save()
        say(f"VERDICT {results['verdict']}: {len(cells) - len(results['failed_cells'])}/{len(cells)} cells pass; "
            f"failed: {results['failed_cells']}")
        say(f"results: {out_dir / 'results.json'}")
    finally:
        for name in reversed(created):
            try:
                asyncio.run(drop_db(args.server_url, name))
            except Exception as exc:  # noqa: BLE001 - reported, the next name is still dropped
                say(f"WARNING: could not drop {name}: {exc}")
        log.close()


if __name__ == "__main__":
    main()
