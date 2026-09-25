"""Programme 020, stage 2: the detector cost measurement (spec, Verification plan §5).

WHAT IT MEASURES. The experimental recursive CTE (`scripts/p020_experimental_detectors.py`), its mandatory
comparator - one bounded DFS over the same SQL-filtered edge relation - and the CURRENT `find_cycles`, on the
four graph families the spec lists, globally and inside a perimeter, at depths 3, 4, 6, 7 and 10. Then the
repeated full-depth detection of an `auto_clear`-like loop on a changing graph (with the real
`execute_clearing` and the 101-success ceiling), and a reproduction of the simulator's clearing call scheme
under its per-tick budget. Nothing here is on the production path; nothing here changes a lock.

THE FROZEN PART. Everything in the block "FROZEN BEFORE MEASUREMENT" below - seed, families, degree shape,
edge mix, planted structures, perimeter rule, cells, sample counts, timeout and the acceptance thresholds - was
committed BEFORE the first timed run (spec §5: "генератор, seed, распределения ... и точные ячейки бенчмарка
заморожены до замера времени"; "пороги задним числом не ослабляются"). The thresholds are the spec's text,
verbatim, turned into numbers:

    p95 <= 500 ms for the 200/2 000 cells at depth <= 6; p95 <= 2 s for the larger and deeper declared cells;
    no stress call > 10 s; the benchmark timeout is mandatory and counts as a failure.

Read as: a cell's family with 200 random vertices and 2 000 random edges (uniform AND skewed) at depth 3/4/6
has the 500 ms bar; every other cell has the 2 s bar; EVERY call of a detector under acceptance, cold one
included, must finish within 10 s, and the per-call timeout IS 10 s, so a timeout fails its cell. A cell that
timed out skips its remaining samples (it has already failed); that is declared here, not decided afterwards.
p95 is nearest-rank over the 20 warm samples (the 19th of 20 sorted); the cold call is reported separately and
counts only towards the 10 s ceiling.

WHERE IT RUNS. Never in a test database and never with `GEO_TEST_ALLOW_DB_RESET`: it creates its own
databases, named `geov0_bench_p020s2*` and nothing else - the name is checked against that pattern before
every CREATE and every DROP - migrates them with `alembic upgrade head`, and drops them at the end however the
run ends. The server is reached through a maintenance connection to `postgres` on the URL given by
`--server-url` (default `postgresql+asyncpg://geo:geo@127.0.0.1:5432/postgres`; `127.0.0.1`, not `localhost`,
AGENTS.md §5). The role needs CREATEDB.

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p020_detector_cost.py --out .local-run/p020s2-bench

WHY CHILD PROCESSES. The current `find_cycles` runs a Python DFS that no timeout can interrupt from inside the
event loop. Every (cell, implementation) therefore runs in its own process, which reports each call as it
finishes; the parent kills a child that is silent past the per-call timeout plus a grace and records a
TIMEOUT. The CTE also carries `statement_timeout`, the experimental DFS a deadline - they time out cleanly;
the kill is the backstop, uniform for all three.

DETERMINISM. Participant, trust-line and debt ids are uuid5 of the family and the element. Debts are created
through the book (`Book.operation` + `NewDebt`, programme 018's single writer); the debt id is assigned on the
pending row by a `before_flush` hook (uuid5 of the ordered pair), which only chooses the primary key the
column default would otherwise draw at random.

WHAT IT DOES NOT SEE. Real OS-cold caches (a "cold" call is the first on a fresh process and connection, with
the server's buffers as they are); other load on the machine; a real simulator run (the scheme is reproduced
from `app/core/simulator/real_clearing_engine.py:214-460`, not driven through the engine); Redis (the 30 s
lease of `/clearing/auto` is compared, not taken).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import multiprocessing as mp
import os
import queue
import random
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
# `app.config.settings` refuses to load without a PostgreSQL URL. The application engine is never used here;
# the value names a bench database so that nothing imported can reach any other one.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_bench_p020s2"

# ================================================================ FROZEN BEFORE MEASUREMENT (stage 2, 020)

SEED = 20200925
NAMESPACE = uuid.UUID("5f0b3c1e-0200-4d20-9a02-0000000f2002")

#: The spec's families (§5): 200/2 000, historic 400/4 000, skewed with high-degree vertices, dense 200/8 000.
#: `hubs`/`hub_share`: that many hub vertices, and that share of edges has one hub endpoint (direction random).
FAMILIES: dict[str, dict] = {
    "u2k": {"title": "uniform 200/2000", "vertices": 200, "edges": 2000, "hubs": 0, "hub_share": 0.0},
    "h4k": {"title": "historic 400/4000", "vertices": 400, "edges": 4000, "hubs": 0, "hub_share": 0.0},
    "s2k": {"title": "skewed 200/2000, 10 hubs carry 50% of edges", "vertices": 200, "edges": 2000, "hubs": 10, "hub_share": 0.5},
    "d8k": {"title": "dense stress 200/8000", "vertices": 200, "edges": 8000, "hubs": 0, "hub_share": 0.0},
}

#: Random-edge mix: (share, status, consent). Consent encodings follow `ClearingService._policy_flag`.
EDGE_MIX = [
    (0.82, "active", True),
    (0.06, "frozen", True),
    (0.04, "closed", True),
    (0.04, "active", "REFUSE"),   # one of REFUSING_ENCODINGS, round-robin
    (0.04, "active", "LEGACY"),   # one of LEGACY_CONSENTING_ENCODINGS, round-robin
]
REFUSING_ENCODINGS = [False, "false", "0", "off", 0, " No "]
LEGACY_CONSENTING_ENCODINGS = ["<missing-key>", "<null-policy>", "yes", 1, "on"]

#: Random amounts: integer cents 1..99999 -> 0.01..999.99. Opposing pairs (a->b and b->a) are not generated:
#: the book nets opposing debt, so such a pair is not a state the application produces.
AMOUNT_CENTS = (1, 100000)

#: Planted structures, on dedicated vertices outside the random range (amounts above every random amount).
PLANTED_RING_LENGTHS = tuple(range(3, 11))  # one eligible ring per length, amount 7777.77
PLANTED_RING_AMOUNT = "7777.77"
PLANTED_EXCLUDED_AMOUNT = "8888.88"  # triangle via a closed line; triangle without consent; 5-ring crossing the perimeter
PLANTED_SHARED = {"shared": "9000.00", "triangle_own": "900.00", "five_own": "9000.00"}  # R-020-1 ladder shape
LAYERED = {"layers": 10, "width": 2, "amount": "6000.00"}  # no cycle shorter than 10: 2**10 = 1024 ten-cycles

#: Perimeter: random vertices with an even index, every planted vertex except one vertex of the crossing ring.
DEPTHS = (3, 4, 6, 7, 10)
SCOPES = ("global", "perimeter")
ACCEPTANCE_IMPLEMENTATIONS = ("cte", "dfs")  # the candidate and its comparator, both judged by the same bars
REPORTED_IMPLEMENTATIONS = ("current", "dfs_exhaustive")  # baseline and the unbounded DFS: reported, not judged
LIMIT = 100

SAMPLES = 20
WARMUP = 2
CALL_TIMEOUT_S = 10.0
KILL_GRACE_S = 5.0
CHILD_STARTUP_S = 120.0

P95_MS_SMALL = 500.0     # "p95 <= 500 мс для ячеек 200/2 000 при глубине <= 6"
P95_MS_LARGE = 2000.0    # "p95 <= 2 с для больших и более глубоких объявленных ячеек"
MAX_CALL_S = 10.0        # "ни один стресс-вызов > 10 с"; applied to every call of a judged implementation
REGRESSION_FACTOR = 2.0  # "регресс больше 2x базовой линии расследуется до удаления" - a flag, not a bar

#: Repeated full-depth detection through an auto_clear-like loop (spec §5, "до переключения").
REPEATED_DEPTH = 6
REPEATED_RUNS = 3
REPEATED_RUN_CAP_S = 300.0
REDIS_LEASE_S = 30.0  # `/clearing/auto` lease, `app/api/v1/clearing.py:53`, no renewal - compared, not taken

#: The simulator's clearing scheme (`real_clearing_engine.py`): depth 6 default, 250 ms per-tick budget.
SIM_DEPTH = 6
SIM_BUDGET_MS = 250
SIM_TICKS = 5


def small_cell(family: str, depth: int) -> bool:
    spec = FAMILIES[family]
    return spec["vertices"] == 200 and spec["edges"] == 2000 and depth <= 6


def p95_bar_ms(family: str, depth: int) -> float:
    return P95_MS_SMALL if small_cell(family, depth) else P95_MS_LARGE


# ======================================================================================= database names

BENCH_NAME_RE = re.compile(r"^geov0_bench_p020s2[a-z0-9_]*$")
DEFAULT_SERVER_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/postgres"


def checked_bench_name(name: str) -> str:
    if not BENCH_NAME_RE.fullmatch(name) or len(name) > 63:
        raise SystemExit(f"refusing database name {name!r}: only geov0_bench_p020s2* is ever created or dropped")
    return name


def database_url(server_url: str, name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(server_url).set(database=checked_bench_name(name)).render_as_string(hide_password=False)


async def create_db(server_url: str, name: str, *, template: str | None = None) -> None:
    from tests.migrated_schema import create_database, drop_database, maintenance_connection

    checked_bench_name(name)
    if template is not None:
        checked_bench_name(template)
    conn = await maintenance_connection(server_url)
    try:
        await drop_database(conn, checked_bench_name(name))
        await create_database(conn, checked_bench_name(name), template=template)
    finally:
        await conn.close()


async def drop_db(server_url: str, name: str) -> None:
    from tests.migrated_schema import drop_database, maintenance_connection

    conn = await maintenance_connection(server_url)
    try:
        await drop_database(conn, checked_bench_name(name))
    finally:
        await conn.close()


# ============================================================================================ generator


def _uid(*parts) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts))


def generate(family: str) -> dict:
    """The graph of one family as plain data, plus its manifest. Deterministic in `SEED` and `family`."""

    spec = FAMILIES[family]
    rnd = random.Random(f"{SEED}:{family}")
    n = spec["vertices"]
    vertices = [f"{family}v{i:03d}" for i in range(n)]
    hubs = vertices[: spec["hubs"]]
    edges: list[dict] = []
    pairs: set[tuple[str, str]] = set()
    refuse_i = legacy_i = 0

    def mix():
        nonlocal refuse_i, legacy_i
        roll, acc = rnd.random(), 0.0
        for share, status, consent in EDGE_MIX:
            acc += share
            if roll < acc:
                break
        if consent == "REFUSE":
            consent = REFUSING_ENCODINGS[refuse_i % len(REFUSING_ENCODINGS)]
            refuse_i += 1
        elif consent == "LEGACY":
            consent = LEGACY_CONSENTING_ENCODINGS[legacy_i % len(LEGACY_CONSENTING_ENCODINGS)]
            legacy_i += 1
        return status, consent

    while len(edges) < spec["edges"]:
        if hubs and rnd.random() < spec["hub_share"]:
            h, o = rnd.choice(hubs), rnd.choice(vertices)
            a, b = (h, o) if rnd.random() < 0.5 else (o, h)
        else:
            a, b = rnd.choice(vertices), rnd.choice(vertices)
        if a == b or (a, b) in pairs or (b, a) in pairs:
            continue
        pairs.add((a, b))
        status, consent = mix()
        cents = rnd.randrange(*AMOUNT_CENTS)
        edges.append({"debtor": a, "creditor": b, "amount": str(Decimal(cents) / 100), "status": status,
                      "consent": consent, "planted": None})

    planted_vertices: list[str] = []

    def ring(tag, pids, amounts, *, status="active", consent=True, statuses=None):
        planted_vertices.extend(p for p in pids if p not in planted_vertices)
        for i, p in enumerate(pids):
            edges.append({"debtor": p, "creditor": pids[(i + 1) % len(pids)], "amount": amounts[i],
                          "status": (statuses[i] if statuses else status), "consent": consent, "planted": tag})

    for length in PLANTED_RING_LENGTHS:
        ring(f"eligible_ring_{length}", [f"{family}r{length}x{k}" for k in range(length)], [PLANTED_RING_AMOUNT] * length)
    ring("excluded_closed_line", [f"{family}xc{k}" for k in range(3)], [PLANTED_EXCLUDED_AMOUNT] * 3,
         statuses=["closed", "active", "active"])
    ring("excluded_no_consent", [f"{family}xn{k}" for k in range(3)], [PLANTED_EXCLUDED_AMOUNT] * 3, consent=False)
    crossing = [f"{family}xp{k}" for k in range(5)]
    ring("excluded_in_perimeter_crossing", crossing, [PLANTED_EXCLUDED_AMOUNT] * 5)
    a, b = f"{family}sha", f"{family}shb"
    ring("shared_triangle", [a, b, f"{family}shc"],
         [PLANTED_SHARED["shared"], PLANTED_SHARED["triangle_own"], PLANTED_SHARED["triangle_own"]])
    five = [a, b] + [f"{family}shf{k}" for k in range(3)]
    ring("shared_five", five, [PLANTED_SHARED["five_own"]] * 5)
    edges.pop(-5)  # the five-ring's a->b duplicates the triangle's shared edge: one debt row
    layers = [[f"{family}l{i}w{j}" for j in range(LAYERED["width"])] for i in range(LAYERED["layers"])]
    for i, layer in enumerate(layers):
        nxt = layers[(i + 1) % len(layers)]
        for u in layer:
            for w in nxt:
                edges.append({"debtor": u, "creditor": w, "amount": LAYERED["amount"], "status": "active",
                              "consent": True, "planted": "layered_no_short_cycles"})
                if u not in planted_vertices:
                    planted_vertices.append(u)

    outside = crossing[2]
    perimeter = sorted({v for i, v in enumerate(vertices) if i % 2 == 0} | (set(planted_vertices) - {outside}))

    all_vertices = vertices + planted_vertices
    outdeg = {v: 0 for v in all_vertices}
    indeg = {v: 0 for v in all_vertices}
    for e in edges:
        outdeg[e["debtor"]] += 1
        indeg[e["creditor"]] += 1

    def dist(values):
        s = sorted(values)
        return {"min": s[0], "p50": s[len(s) // 2], "p95": s[math.ceil(0.95 * len(s)) - 1], "max": s[-1],
                "mean": round(sum(s) / len(s), 2)}

    def consents(c):
        from app.core.clearing.service import ClearingService

        policy = None if c == "<null-policy>" else ({} if c == "<missing-key>" else {"auto_clearing": c})
        return ClearingService._policy_flag(policy, "auto_clearing", default=True)

    excluded = {"closed_line": 0, "consent_refused": 0}
    eligible = 0
    for e in edges:
        if e["status"] == "closed":
            excluded["closed_line"] += 1
        elif not consents(e["consent"]):
            excluded["consent_refused"] += 1
        else:
            eligible += 1
    random_vertices = set(vertices)
    manifest = {
        "family": family,
        "title": spec["title"],
        "seed": SEED,
        "random_vertices": n,
        "random_edges": spec["edges"],
        "planted_vertices": len(planted_vertices),
        "planted_edges": sum(1 for e in edges if e["planted"]),
        "vertices_total": len(all_vertices),
        "edges_total": len(edges),
        "eligible_edges": eligible,
        "excluded_edges": excluded,
        "out_degree_random": dist([outdeg[v] for v in random_vertices]),
        "in_degree_random": dist([indeg[v] for v in random_vertices]),
        "perimeter_vertices": len(perimeter),
        "planted": sorted({e["planted"] for e in edges if e["planted"]}),
        "hubs": hubs,
        "graph_sha256": hashlib.sha256(json.dumps(edges, sort_keys=True, default=str).encode()).hexdigest(),
    }
    return {"family": family, "edges": edges, "vertices": all_vertices, "perimeter": perimeter, "manifest": manifest}


async def build(server_url: str, name: str, graph: dict) -> None:
    """Create, migrate and fill one family database."""

    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.ledger.book import Book, NewDebt, operation_for
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.migrated_schema import run_alembic_upgrade_head

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
                        obj.id = _uid(family, "debt", obj.debtor_id, obj.creditor_id)

            event.listen(session.sync_session, "before_flush", assign_debt_ids)
            eq_id = _uid(family, "equivalent")
            session.add(Equivalent(id=eq_id, code="BENCH", symbol="B", precision=2, is_active=True))
            for v in graph["vertices"]:
                session.add(Participant(id=_uid(family, "p", v), pid=v, display_name=v, type="person",
                                        public_key=hashlib.sha256(f"{family}:{v}".encode()).hexdigest(),
                                        status="active"))
            await session.flush()
            for e in graph["edges"]:
                c = e["consent"]
                policy = None if c == "<null-policy>" else ({} if c == "<missing-key>" else {"auto_clearing": c})
                session.add(TrustLine(id=_uid(family, "tl", e["creditor"], e["debtor"]),
                                      from_participant_id=_uid(family, "p", e["creditor"]),
                                      to_participant_id=_uid(family, "p", e["debtor"]),
                                      equivalent_id=eq_id, limit=Decimal("1000000"), policy=policy,
                                      status=e["status"]))
            await session.flush()
            async with Book.operation(
                session,
                operation_for("SEED", f"p020_bench:{family}:{SEED}", {"script": "measure_p020_detector_cost"},
                              scope_equivalent_ids=None),
            ) as posting:
                for k, e in enumerate(graph["edges"]):
                    await posting.apply(NewDebt(debtor_id=_uid(family, "p", e["debtor"]),
                                                creditor_id=_uid(family, "p", e["creditor"]),
                                                equivalent_id=eq_id, amount=Decimal(e["amount"])))
                    if k % 500 == 499:
                        await session.flush()
            await session.commit()
            for table in ("debts", "trust_lines", "participants"):
                await session.execute(text(f"ANALYZE {table}"))
            await session.commit()
    finally:
        await engine.dispose()


# ================================================================================ child-side measuring


def _child_engine(url: str, *, pooled: bool):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings

    kwargs = {"isolation_level": settings.DB_POSTGRES_ISOLATION_LEVEL,
              "connect_args": {"server_settings": {"statement_timeout": str(int(CALL_TIMEOUT_S * 1000))}}}
    if pooled:
        kwargs.update(pool_size=1, max_overflow=0)
    else:
        kwargs["poolclass"] = NullPool
    return create_async_engine(url, **kwargs)


class _Statements:
    def __init__(self, engine):
        from sqlalchemy import event

        self.count = 0
        event.listen(engine.sync_engine, "before_cursor_execute", self._hit)

    def _hit(self, *args, **kwargs):
        self.count += 1


def _is_timeout(exc: BaseException) -> bool:
    from scripts.p020_experimental_detectors import DetectorTimeout

    if isinstance(exc, DetectorTimeout):
        return True
    text = f"{type(exc).__name__}: {exc}"
    return "statement timeout" in text or "QueryCanceled" in text or "canceling statement" in text


async def _detect_once(impl: str, session, eq_id, depth: int, scope_ids, scope_pids):
    """One detection. Returns the ordered identities."""

    from app.core.clearing.service import ClearingService
    from scripts.p020_experimental_detectors import detect_cte, detect_dfs

    if impl == "cte":
        cycles = await detect_cte(session, eq_id, depth, scope_ids=scope_ids, limit=LIMIT)
        return [c.identity for c in cycles]
    if impl in ("dfs", "dfs_exhaustive"):
        cycles = await detect_dfs(session, eq_id, depth, scope_ids=scope_ids, limit=LIMIT,
                                  bounded=impl == "dfs", deadline=time.monotonic() + CALL_TIMEOUT_S)
        return [c.identity for c in cycles]
    if impl == "current":
        cycles = await ClearingService(session).find_cycles("BENCH", max_depth=depth, allowed_participant_pids=scope_pids)
        return [tuple(sorted(str(uuid.UUID(str(e["debt_id"]))) for e in c)) for c in cycles]
    raise ValueError(impl)


def child_cell(url: str, family: str, impl: str, depth: int, scope: str, perimeter: list[str], out) -> None:
    asyncio.run(_child_cell(url, family, impl, depth, scope, perimeter, out))


async def _child_cell(url, family, impl, depth, scope, perimeter, out) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from scripts.p020_experimental_detectors import cte_sql

    eq_id = _uid(family, "equivalent")
    scope_pids = set(perimeter) if scope == "perimeter" else None
    scope_ids = {_uid(family, "p", p) for p in perimeter} if scope == "perimeter" else None
    cold_engine = _child_engine(url, pooled=False)
    warm_engine = _child_engine(url, pooled=True)
    counters = {"cold": _Statements(cold_engine), "warm": _Statements(warm_engine)}
    out.put({"kind": "ready"})

    async def call(engine, which):
        before = counters[which].count
        t0 = time.perf_counter()
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as s:
            ids = await _detect_once(impl, s, eq_id, depth, scope_ids, scope_pids)
            await s.rollback()
        return (time.perf_counter() - t0) * 1000.0, counters[which].count - before, ids

    try:
        try:
            ms, stmts, ids = await call(cold_engine, "cold")
            out.put({"kind": "cold", "ms": ms, "statements": stmts})
            for _ in range(WARMUP):
                ms, stmts, ids = await call(warm_engine, "warm")
                out.put({"kind": "warmup", "ms": ms, "statements": stmts})
            for _ in range(SAMPLES):
                ms, stmts, ids = await call(warm_engine, "warm")
                out.put({"kind": "sample", "ms": ms, "statements": stmts})
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
            out.put({"kind": "timeout" if _is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
            return
        out.put({"kind": "identities", "ids": [list(i) for i in ids],
                 "lengths": sorted({len(i) for i in ids})})
        if impl == "cte":
            params = {"equivalent_id": eq_id, "max_depth": depth, "limit": LIMIT}
            if scope_ids is not None:
                params["scope"] = sorted(scope_ids)
            try:
                async with async_sessionmaker(bind=warm_engine, class_=AsyncSession)() as s:
                    plan = (await s.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                                                 + cte_sql(scoped=scope_ids is not None)), params)).scalar_one()
                    await s.rollback()
                out.put({"kind": "plan", "plan": plan})
            except Exception as exc:  # noqa: BLE001
                out.put({"kind": "plan_error", "error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        out.put({"kind": "done"})
        await cold_engine.dispose()
        await warm_engine.dispose()


def _plan_figures(plan) -> dict:
    root = plan[0] if isinstance(plan, list) else plan
    figures = {"execution_ms": root.get("Execution Time"), "planning_ms": root.get("Planning Time")}
    top = root["Plan"]
    for key in ("Shared Hit Blocks", "Shared Read Blocks", "Temp Read Blocks", "Temp Written Blocks"):
        figures[key.lower().replace(" ", "_")] = top.get(key)
    recursive = []

    def walk(node):
        if node.get("Node Type") == "Recursive Union":
            recursive.append(node.get("Actual Rows", 0) * node.get("Actual Loops", 1))
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(top)
    figures["recursive_union_rows"] = recursive[0] if recursive else None
    return figures


# =========================================================================== parent-side cell running


def run_child(target, args, *, first_timeout: float, call_timeout: float) -> list[dict]:
    """Run `target(*args, out)` in a child; kill it when silent too long. Returns its messages."""

    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    proc = ctx.Process(target=target, args=(*args, out), daemon=True)
    proc.start()
    messages: list[dict] = []
    timeout = first_timeout
    try:
        while True:
            try:
                msg = out.get(timeout=timeout)
            except queue.Empty:
                messages.append({"kind": "timeout", "error": f"killed: silent for {timeout:.0f} s"})
                proc.kill()
                break
            messages.append(msg)
            if msg["kind"] == "done":
                break
            timeout = call_timeout
    finally:
        proc.join(timeout=10)
        if proc.is_alive():
            proc.kill()
    return messages


def summarize(family, impl, depth, scope, messages) -> dict:
    samples = [m["ms"] for m in messages if m["kind"] == "sample"]
    statements = sorted({m["statements"] for m in messages if m["kind"] in ("sample", "cold", "warmup")})
    cold = next((m for m in messages if m["kind"] == "cold"), None)
    failure = next((m for m in messages if m["kind"] in ("timeout", "error")), None)
    ids = next((m for m in messages if m["kind"] == "identities"), None)
    plan = next((m for m in messages if m["kind"] == "plan"), None)
    all_calls = [m["ms"] for m in messages if m["kind"] in ("cold", "warmup", "sample")]
    row = {
        "family": family, "impl": impl, "depth": depth, "scope": scope,
        "samples": len(samples),
        "cold_ms": round(cold["ms"], 1) if cold else None,
        "p50_ms": round(statistics.median(samples), 1) if samples else None,
        "p95_ms": round(sorted(samples)[math.ceil(0.95 * len(samples)) - 1], 1) if len(samples) == SAMPLES else None,
        "max_ms": round(max(all_calls), 1) if all_calls else None,
        "statements_per_call": statements,
        "failure": failure["kind"].upper() + ": " + failure["error"] if failure else None,
        "cycles": len(ids["ids"]) if ids else None,
        "lengths": ids["lengths"] if ids else None,
        "identities_sha": hashlib.sha256(json.dumps(ids["ids"]).encode()).hexdigest()[:16] if ids else None,
        "_ids": ids["ids"] if ids else None,
    }
    if plan:
        row["plan"] = _plan_figures(plan["plan"])
        row["_plan_raw"] = plan["plan"]
    if impl in ACCEPTANCE_IMPLEMENTATIONS:
        bar = p95_bar_ms(family, depth)
        ok = (failure is None and row["p95_ms"] is not None and row["p95_ms"] <= bar
              and row["max_ms"] is not None and row["max_ms"] <= MAX_CALL_S * 1000.0)
        row["p95_bar_ms"] = bar
        row["verdict"] = "PASS" if ok else "FAIL"
    else:
        row["verdict"] = "reported"
    return row


# ======================================================================= repeated calls and simulator


def child_repeated(url, family, detector, perimeter, out) -> None:
    asyncio.run(_child_repeated(url, family, detector, perimeter, out))


async def _child_repeated(url, family, detector, perimeter, out) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.service import ClearingService
    from scripts.p020_experimental_detectors import detect_cte, detect_dfs, render_for_find_cycles

    engine = _child_engine(url, pooled=True)
    stmts = _Statements(engine)
    eq_id = _uid(family, "equivalent")
    out.put({"kind": "ready"})
    detections = {"n": 0, "ms": 0.0, "statements": 0}
    t0 = time.perf_counter()
    result: dict = {}
    try:
        async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as session:
            service = ClearingService(session)
            if detector == "current":
                real_find = service.find_cycles

                async def timed_find(*a, **kw):
                    s0, d0 = stmts.count, time.perf_counter()
                    try:
                        return await real_find(*a, **kw)
                    finally:
                        detections["n"] += 1
                        detections["ms"] += (time.perf_counter() - d0) * 1000.0
                        detections["statements"] += stmts.count - s0

                service.find_cycles = timed_find
                cleared = await service.auto_clear("BENCH", max_depth=REPEATED_DEPTH)
            else:
                detect = detect_cte if detector == "cte" else detect_dfs
                cleared = 0
                while True:
                    s0, d0 = stmts.count, time.perf_counter()
                    kwargs = {"limit": LIMIT}
                    if detector == "dfs":
                        kwargs["deadline"] = time.monotonic() + CALL_TIMEOUT_S
                    cycles = await detect(session, eq_id, REPEATED_DEPTH, **kwargs)
                    rendered = await render_for_find_cycles(session, cycles, precision=2)
                    await session.rollback()
                    detections["n"] += 1
                    detections["ms"] += (time.perf_counter() - d0) * 1000.0
                    detections["statements"] += stmts.count - s0
                    executed = False
                    for cycle in rendered:
                        if await service.execute_clearing(cycle):
                            executed = True
                            cleared += 1
                            break
                    if not executed or cleared > 100:
                        break
            result = {"kind": "result", "cleared": cleared}
    except Exception as exc:  # noqa: BLE001
        result = {"kind": "timeout" if _is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        total_ms = (time.perf_counter() - t0) * 1000.0
        result.update({"total_ms": total_ms, "detections": detections["n"], "detection_ms": detections["ms"],
                       "detection_statements": detections["statements"], "total_statements": stmts.count})
        out.put(result)
        out.put({"kind": "done"})
        await engine.dispose()


def child_simulator(url, family, detector, perimeter, out) -> None:
    asyncio.run(_child_simulator(url, family, detector, perimeter, out))


async def _child_simulator(url, family, detector, perimeter, out) -> None:
    """The scheme of `real_clearing_engine.py:214-460`, reproduced: preflight detection, then execute and
    re-detect under the per-tick budget, checked between iterations. `current` keeps the engine's short-rung
    ladder and its per-tick priority rotation; the amount-first variants detect at full depth and do not
    rotate (the replacement 021 is to make). The run perimeter is applied to detection and execution."""

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.clearing.service import ClearingService
    from scripts.p020_experimental_detectors import detect_cte, detect_dfs, render_for_find_cycles

    engine = _child_engine(url, pooled=True)
    stmts = _Statements(engine)
    eq_id = _uid(family, "equivalent")
    scope_ids = {_uid(family, "p", p) for p in perimeter}
    pids = set(perimeter)
    out.put({"kind": "ready"})
    try:
        for tick in range(SIM_TICKS):
            async with async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)() as session:
                service = ClearingService(session)

                async def find():
                    if detector == "current":
                        found = await service.find_cycles("BENCH", max_depth=min(SIM_DEPTH, 4), allowed_participant_pids=pids)
                        if not found and SIM_DEPTH > 4:
                            found = await service.find_cycles("BENCH", max_depth=SIM_DEPTH, allowed_participant_pids=pids)
                        if len(found) > 1:
                            digest = hashlib.sha256(f"bench:{tick}:BENCH".encode()).digest()
                            idx = int.from_bytes(digest[:4], "big") % len(found)
                            found = [found[idx], *found[:idx], *found[idx + 1:]]
                        return found
                    detect = detect_cte if detector == "cte" else detect_dfs
                    kwargs = {"scope_ids": scope_ids, "limit": LIMIT}
                    if detector == "dfs":
                        kwargs["deadline"] = time.monotonic() + CALL_TIMEOUT_S
                    cycles = await detect(session, eq_id, SIM_DEPTH, **kwargs)
                    rendered = await render_for_find_cycles(session, cycles, precision=2)
                    await session.rollback()
                    return rendered

                s0, t0 = stmts.count, time.perf_counter()
                cycles = await find()
                preflight_ms = (time.perf_counter() - t0) * 1000.0
                started = time.perf_counter()
                cleared, detections, consumed = 0, 1, False
                while True:
                    if (time.perf_counter() - started) * 1000.0 >= SIM_BUDGET_MS:
                        break
                    if consumed:
                        cycles = await find()
                        detections += 1
                        if not cycles:
                            break
                    else:
                        consumed = True
                    executed = False
                    for cycle in cycles:
                        amount = await service.execute_clearing_with_amount(cycle, allowed_participant_pids=pids)
                        if amount is not None:
                            cleared += 1
                            executed = True
                            break
                    if not executed or cleared > 100:
                        break
                loop_ms = (time.perf_counter() - started) * 1000.0
                out.put({"kind": "tick", "tick": tick, "preflight_ms": preflight_ms, "loop_ms": loop_ms,
                         "over_budget_ms": max(0.0, loop_ms - SIM_BUDGET_MS), "cleared": cleared,
                         "detections": detections, "statements": stmts.count - s0})
    except Exception as exc:  # noqa: BLE001
        out.put({"kind": "timeout" if _is_timeout(exc) else "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
    finally:
        out.put({"kind": "done"})
        await engine.dispose()


# ================================================================================================ main


def compare(rows: list[dict]) -> list[dict]:
    """Where the outputs differ, per cell: cte vs dfs must be identical (same contract); current is reported."""

    by_cell: dict = {}
    for r in rows:
        by_cell.setdefault((r["family"], r["depth"], r["scope"]), {})[r["impl"]] = r
    out = []
    for (family, depth, scope), impls in sorted(by_cell.items()):
        cte, dfs, cur, dfx = (impls.get(k) for k in ("cte", "dfs", "current", "dfs_exhaustive"))
        entry = {"family": family, "depth": depth, "scope": scope}
        for name, other in (("dfs", dfs), ("dfs_exhaustive", dfx), ("current", cur)):
            if cte and other and cte["_ids"] is not None and other["_ids"] is not None:
                a, b = cte["_ids"], other["_ids"]
                entry[f"cte_vs_{name}"] = "identical" if a == b else (
                    f"differ: {len(a)} vs {len(b)} cycles, overlap {len(set(map(tuple, a)) & set(map(tuple, b)))}, "
                    f"same order {a[:len(b)] == b[:len(a)]}")
            elif dfs and other and name != "dfs" and dfs["_ids"] is not None and other["_ids"] is not None:
                a, b = dfs["_ids"], other["_ids"]
                entry[f"dfs_vs_{name}"] = "identical" if a == b else (
                    f"differ: {len(a)} vs {len(b)} cycles, overlap {len(set(map(tuple, a)) & set(map(tuple, b)))}")
            else:
                entry[f"cte_vs_{name}"] = "not comparable (a side failed)"
        if cur and cur["lengths"]:
            entry["current_lengths"] = cur["lengths"]
        out.append(entry)
    return out


async def _prepare(server_url: str, out_dir: Path) -> dict:
    graphs = {}
    for family in FAMILIES:
        graph = generate(family)
        name = checked_bench_name(f"geov0_bench_p020s2_{family}")
        t0 = time.perf_counter()
        await build(server_url, name, graph)
        graph["manifest"]["build_s"] = round(time.perf_counter() - t0, 1)
        graphs[family] = graph
        print(f"built {name}: {graph['manifest']['edges_total']} edges in {graph['manifest']['build_s']} s", flush=True)
    (out_dir / "manifest.json").write_text(json.dumps({f: g["manifest"] for f, g in graphs.items()}, indent=2))
    return graphs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--out", default=str(REPO_ROOT / ".local-run" / "p020s2-bench"))
    parser.add_argument("--families", default=",".join(FAMILIES))
    parser.add_argument("--skip-cells", action="store_true")
    parser.add_argument("--skip-repeated", action="store_true")
    args = parser.parse_args()
    families = [f for f in args.families.split(",") if f]
    for f in families:
        if f not in FAMILIES:
            raise SystemExit(f"unknown family {f!r}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    (out_dir / "plans").mkdir(parents=True, exist_ok=True)
    from scripts.p020_experimental_detectors import cte_sql

    (out_dir / "cte_global.sql").write_text(cte_sql(scoped=False))
    (out_dir / "cte_perimeter.sql").write_text(cte_sql(scoped=True))

    created: list[str] = []
    results: dict = {"started": stamp, "frozen": {
        "seed": SEED, "families": FAMILIES, "depths": DEPTHS, "scopes": SCOPES, "samples": SAMPLES,
        "warmup": WARMUP, "call_timeout_s": CALL_TIMEOUT_S, "p95_ms_small": P95_MS_SMALL,
        "p95_ms_large": P95_MS_LARGE, "max_call_s": MAX_CALL_S, "limit": LIMIT}}
    try:
        graphs = asyncio.run(_prepare(args.server_url, out_dir))
        created += [f"geov0_bench_p020s2_{f}" for f in graphs]
        results["manifest"] = {f: g["manifest"] for f, g in graphs.items()}

        rows = []
        if not args.skip_cells:
            for family in families:
                url = database_url(args.server_url, f"geov0_bench_p020s2_{family}")
                for scope in SCOPES:
                    for depth in DEPTHS:
                        for impl in ACCEPTANCE_IMPLEMENTATIONS + REPORTED_IMPLEMENTATIONS:
                            messages = run_child(child_cell, (url, family, impl, depth, scope, graphs[family]["perimeter"]),
                                                 first_timeout=CHILD_STARTUP_S, call_timeout=CALL_TIMEOUT_S + KILL_GRACE_S)
                            row = summarize(family, impl, depth, scope, messages)
                            if "_plan_raw" in row:
                                (out_dir / "plans" / f"{family}_{scope}_d{depth}.json").write_text(json.dumps(row.pop("_plan_raw")))
                            rows.append(row)
                            print(f"{family} {scope:9} d{depth:<2} {impl:14} {row['verdict']:8} cold={row['cold_ms']} "
                                  f"p50={row['p50_ms']} p95={row['p95_ms']} max={row['max_ms']} stmts={row['statements_per_call']} "
                                  f"cycles={row['cycles']} {row['failure'] or ''}", flush=True)
            results["comparison"] = compare(rows)
            results["cells"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
            (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))

        if not args.skip_repeated:
            repeated, ticks = [], []
            for family in families:
                template = f"geov0_bench_p020s2_{family}"
                for detector in ("cte", "dfs", "current"):
                    for run in range(REPEATED_RUNS + 1):
                        clone = checked_bench_name(f"{template}_c")
                        asyncio.run(create_db(args.server_url, clone, template=template))
                        created.append(clone)
                        url = database_url(args.server_url, clone)
                        if run < REPEATED_RUNS:
                            msgs = run_child(child_repeated, (url, family, detector, graphs[family]["perimeter"]),
                                             first_timeout=CHILD_STARTUP_S, call_timeout=REPEATED_RUN_CAP_S)
                            res = next((m for m in msgs if m["kind"] in ("result", "timeout", "error")), msgs[-1])
                            entry = {"family": family, "detector": detector, "run": run, **res,
                                     "exceeds_redis_lease": res.get("total_ms", 0) > REDIS_LEASE_S * 1000.0}
                            repeated.append(entry)
                            print(f"repeated {family} {detector:7} run{run} {json.dumps(res, default=str)}", flush=True)
                        else:
                            msgs = run_child(child_simulator, (url, family, detector, graphs[family]["perimeter"]),
                                             first_timeout=CHILD_STARTUP_S, call_timeout=REPEATED_RUN_CAP_S)
                            for m in msgs:
                                if m["kind"] in ("tick", "timeout", "error"):
                                    ticks.append({"family": family, "detector": detector, **m})
                                    print(f"simulator {family} {detector:7} {json.dumps(m, default=str)}", flush=True)
                        asyncio.run(drop_db(args.server_url, clone))
                        created.remove(clone)
            results["repeated"] = repeated
            results["simulator"] = ticks
            (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
        print(f"results: {out_dir / 'results.json'}")
    finally:
        for name in reversed(created):
            try:
                asyncio.run(drop_db(args.server_url, name))
            except Exception as exc:  # noqa: BLE001 - reported, the next name is still dropped
                print(f"WARNING: could not drop {name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
