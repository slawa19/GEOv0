"""Programme 023, slice (a), optimisation round: THE ONE PROFILE of the planner (spec 023, addendum to the stop condition).

Profiles the complete `plan_for_equivalent` on the three worst failing cells of run `20260926T052821Z` - h4k
largeatoms global, d8k largeatoms global, h4k narrow global - on the frozen generator's graphs (version 1's
`variant_graph` and `build`, unmodified). Per cell: one untraced warm-up call; a stage breakdown (snapshot read,
indexing, solver, feasibility, decomposition, decomposition check, certificate - each timed once, directly on
the module's functions and the same data); one `cProfile` of the whole call (its call counts give the number of
Dijkstra heap operations), top functions by own time and by cumulative time. This is a
diagnostic, not acceptance: nothing here is judged. Databases `geov0_bench_p023a*` only, dropped at the end.

    D:\\...\\.venv\\Scripts\\python.exe scripts/profile_p023_planner.py --out <file>
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import measure_p023_planner_acceptance as v1  # noqa: E402 - sets the p023a bench DATABASE_URL
from scripts.measure_p023_planner_acceptance import base020  # noqa: E402

CELLS = (("h4k", "largeatoms"), ("d8k", "largeatoms"), ("h4k", "narrow"))


async def profile_cell(url: str, lines: list[str]) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.core.clearing.flow_planner as fp

    engine = base020._child_engine(url, pooled=True)
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as s:
            await fp.plan_for_equivalent(s, "BENCH")
            await s.rollback()

        stages: list[tuple[str, float]] = []

        def timed(name, fn, *a):
            t0 = time.perf_counter()
            r = fn(*a)
            stages.append((name, (time.perf_counter() - t0) * 1000.0))
            return r

        t0 = time.perf_counter()
        async with sessions() as s:
            edges = await fp.load_snapshot(s, "BENCH")
            await s.rollback()
        stages.append(("load_snapshot (3 statements, incl. session)", (time.perf_counter() - t0) * 1000.0))
        edges = timed("sort edges by str(uuid)", lambda: tuple(sorted(edges, key=lambda e: str(e.debt_id))))
        vertices, tails, heads, caps = timed("_index (validation + indexing)", fp._index, edges)
        flow, pi = timed("solve_transshipment", fp.solve_transshipment, len(vertices), tails, heads, caps)
        remaining = {e.debt_id: flow[k] for k, e in enumerate(edges)}
        potentials = {v: pi[i] for i, v in enumerate(vertices)}
        timed("check_feasible", fp.check_feasible, edges, remaining)
        cycles = timed("decompose", fp.decompose, edges, remaining)
        timed("check_decomposition", fp.check_decomposition, edges, remaining, cycles)
        timed("check_certificate", fp.check_certificate, edges, remaining, potentials)
        lines.append(f"edges={len(edges)} vertices={len(vertices)} cycles={len(cycles)} "
                     f"U.bit_length={max(max(caps), 1).bit_length()}")
        lines.append("stage breakdown (one run each, ms):")
        for name, ms in stages:
            lines.append(f"  {ms:10.1f}  {name}")
        lines.append(f"  {sum(ms for _, ms in stages):10.1f}  total")

        profiler = cProfile.Profile()
        async with sessions() as s:
            profiler.enable()
            await fp.plan_for_equivalent(s, "BENCH")
            profiler.disable()
            await s.rollback()
        for key in ("tottime", "cumulative"):
            buf = io.StringIO()
            pstats.Stats(profiler, stream=buf).strip_dirs().sort_stats(key).print_stats(25)
            lines.append(f"cProfile, whole plan_for_equivalent, sorted by {key} (top 25):")
            lines.extend("  " + ln for ln in buf.getvalue().splitlines() if ln.strip())
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=v1.DEFAULT_SERVER_URL)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    lines: list[str] = [f"# profile_p023_planner.py, cells {CELLS}"]
    created: list[str] = []
    try:
        for family, variant in CELLS:
            graph = v1.variant_graph(family, variant)
            name = v1.db_name(family, variant)
            created.append(name)
            asyncio.run(v1.build(args.server_url, name, graph, precision=v1.precision_of(variant)))
            lines.append("")
            lines.append(f"==================== {family} {variant} global")
            asyncio.run(profile_cell(v1.database_url(args.server_url, name), lines))
            asyncio.run(v1.drop_db(args.server_url, name))
            created.remove(name)
            print(f"profiled {family} {variant}", flush=True)
    finally:
        for name in reversed(created):
            asyncio.run(v1.drop_db(args.server_url, name))
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
