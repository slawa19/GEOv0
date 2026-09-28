"""Programme 021, stage 1 (`T2102`): the trust-line batch measurement, before and after.

WHAT IT MEASURES. Four workloads of the simulator's trust-line writers, each on a FRESH isolated database
per sample (a `CREATE DATABASE ... TEMPLATE` copy), with the inputs fixed by the frozen configuration
`scripts/p021_benchmark_config.json`:

* seed   - `RealScenarioSeeder.seed_scenario_into_db` + commit, the 100/523 community, on an empty migrated DB;
* reseed - the same call on a copy of a seeded DB (nothing new to apply);
* growth - `TrustDriftEngine.apply_trust_growth` on 50 UAH lines (it commits itself);
* decay  - the tick's decay step `RealTick.apply_trust_decay_and_broadcast` (`app/core/simulator/tick.py`; until
  021 stage 4 `RealTickTrustDriftCoordinator`, same code) on 25 HOUR + 25 UAH lines.

Per sample: elapsed seconds, client SQL statements (`before_cursor_execute` on the sample's engine, from the
start of the call through its commit), integrity-checkpoint computations (every module attribute bound to
`app.core.integrity.compute_integrity_checkpoint_for_equivalent` is wrapped by a counter that calls through),
applied operations, touched equivalents and the trust-line audit rows written. Then one ROLLBACK PROBE per
workload: a non-database failure after the first mutation, the owner's handling, a later commit on the same
session, and what a fresh session finds persisted.

THE THRESHOLDS ARE NOT HERE. They are read from the frozen configuration, committed before any implementation
and any timed run; this runner only reports each against them. It runs unchanged on the code before and after
stage 1, so both measurements see identical inputs.

WHAT IT DOES NOT SEE. The SSE broadcasts and edge patches of the drift paths (stubbed: they are not the
trust-line batch), the tick around the writers, and any concurrency: every sample is one writer alone.

WHERE IT RUNS: databases named `geov0_bench_p021s1*` only, created and dropped here; every name is checked
against that pattern immediately before the DDL it goes into. Never a test database, never
`GEO_TEST_ALLOW_DB_RESET`.

    D:\\...\\.venv\\Scripts\\python.exe scripts/measure_p021_trust_line_batches.py --label before --out <file.json>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import statistics
import subprocess
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
os.environ["DATABASE_URL"] = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_bench_p021s1"

CONFIG_PATH = REPO_ROOT / "scripts" / "p021_benchmark_config.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
RUN = CONFIG["run"]
INPUTS = CONFIG["inputs"]
WORKLOADS = INPUTS["workloads"]
BENCH_NAME_RE = re.compile(RUN["database_name_pattern"])
NAMESPACE = uuid.UUID("5f0b3c1e-0210-4d21-9a02-0000000f2102")

logging.basicConfig(level=logging.ERROR)
LOGGER = logging.getLogger("p021.bench")


# ================================================================================ databases


def checked_bench_name(name: str) -> str:
    if not BENCH_NAME_RE.fullmatch(name) or len(name) > 63:
        raise SystemExit(f"refusing database name {name!r}: only {RUN['database_name_pattern']} is ever created or dropped")
    return name


def database_url(name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(RUN["server_url"]).set(database=checked_bench_name(name)).render_as_string(hide_password=False)


async def _maintenance():
    from tests.migrated_schema import maintenance_connection

    return await maintenance_connection(RUN["server_url"])


async def _drop(conn, name: str) -> None:
    name = checked_bench_name(name)
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
        name,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


async def create_db(name: str, *, template: str | None = None) -> None:
    conn = await _maintenance()
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


async def drop_db(name: str) -> None:
    conn = await _maintenance()
    try:
        await _drop(conn, name)
    finally:
        await conn.close()


def make_engine(name: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings

    return create_async_engine(
        database_url(name), poolclass=NullPool, isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL
    )


def make_sessionmaker(engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    # The application's own session shape (`app/db/session.py`): no autoflush, no expiry on commit.
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


# ================================================================================ inputs


def load_community() -> dict:
    raw = (REPO_ROOT / INPUTS["community"]).read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != INPUTS["community_sha256"]:
        raise SystemExit(f"community input changed: sha256 {digest} != frozen {INPUTS['community_sha256']}")
    community = json.loads(raw.decode("utf-8"))
    expected = INPUTS["community_expected"]
    assert len(community["participants"]) == expected["participants"], "participants"
    assert len(community["trustlines"]) == expected["trustlines"], "trustlines"
    assert sorted(e["code"] for e in community["equivalents"]) == expected["equivalents"], "equivalents"
    return community


def scenario_of(community: dict) -> dict:
    """The frozen mapping of the community description onto the seeder's scenario shape."""

    pid_of = {p["ref"]: p["pid"] for p in community["participants"]}
    return {
        "scenario_id": "p021-bench-greenfield-100",
        "equivalents": [e["code"] for e in community["equivalents"]],
        "participants": [
            {"id": p["pid"], "name": p["name"], "type": p["type"], "status": p["status"]}
            for p in community["participants"]
        ],
        "trustlines": [
            {
                "from": pid_of[t["from"]],
                "to": pid_of[t["to"]],
                "equivalent": t["equivalent"],
                "limit": t["limit"],
                "status": t["status"],
                "policy": t["policy"],
            }
            for t in community["trustlines"]
        ],
    }


def _uid(*parts) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts))


def participant_id(pid: str) -> uuid.UUID:
    return _uid("participant", pid)


def growth_edges(scenario: dict) -> list[dict]:
    spec = WORKLOADS["growth"]
    active = [t for t in scenario["trustlines"] if t["equivalent"] == spec["equivalent"] and t["status"] == "active"]
    return active[: spec["edges"]]


def decay_edges(scenario: dict) -> list[dict]:
    spec = WORKLOADS["decay"]
    out: list[dict] = []
    for eq in spec["equivalents"]:
        active = [t for t in scenario["trustlines"] if t["equivalent"] == eq and t["status"] == "active"]
        out.extend(active[: spec["edges_per_equivalent"]])
    return out


# ================================================================================ templates


async def build_templates(scenario: dict) -> tuple[str, str]:
    """An empty migrated template and a seeded one. The seeded one is written directly, NOT by the seeder under
    measurement, so the drift workloads get byte-identical inputs before and after stage 1."""

    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from app.core.simulator.real_scenario_seeder import simulated_public_key
    from tests.migrated_schema import run_alembic_upgrade_head

    empty = checked_bench_name("geov0_bench_p021s1_empty")
    seeded = checked_bench_name("geov0_bench_p021s1_seeded")
    await create_db(empty)
    run_alembic_upgrade_head(database_url(empty))
    await create_db(seeded, template=empty)

    engine = make_engine(seeded)
    try:
        async with make_sessionmaker(engine)() as session:
            eq_ids: dict[str, uuid.UUID] = {}
            for code in scenario["equivalents"]:
                eq_ids[code] = _uid("equivalent", code)
                session.add(Equivalent(id=eq_ids[code], code=code, is_active=True, metadata_={}))
            for p in scenario["participants"]:
                session.add(
                    Participant(
                        id=participant_id(p["id"]), pid=p["id"], display_name=p["name"],
                        public_key=simulated_public_key(p["id"]), type=p["type"],
                        status="suspended" if p["status"] == "frozen" else p["status"], profile={},
                    )
                )
            await session.flush()
            for t in scenario["trustlines"]:
                session.add(
                    TrustLine(
                        id=_uid("trustline", t["from"], t["to"], t["equivalent"]),
                        from_participant_id=participant_id(t["from"]), to_participant_id=participant_id(t["to"]),
                        equivalent_id=eq_ids[t["equivalent"]], limit=Decimal(t["limit"]),
                        status=t["status"], policy=t["policy"],
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()
    return empty, seeded


# ================================================================================ instruments


class Statements:
    def __init__(self, engine) -> None:
        from sqlalchemy import event

        self.count = 0
        self.active = False
        self.fail_after_first_trust_line_write = False
        self.failed = False
        event.listen(engine.sync_engine, "before_cursor_execute", self._before)
        event.listen(engine.sync_engine, "after_cursor_execute", self._after)

    def _before(self, *args, **kwargs) -> None:
        if self.active:
            self.count += 1

    def _after(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not self.fail_after_first_trust_line_write or self.failed:
            return
        head = " ".join(str(statement).split()).upper()
        if head.startswith("INSERT INTO TRUST_LINES") or head.startswith("UPDATE TRUST_LINES"):
            self.failed = True
            raise RuntimeError("p021 probe: failure after the first trust-line write statement")


class Checkpoints:
    """Counts every call of the integrity checkpoint function, whichever module calls it."""

    def __init__(self) -> None:
        import app.core.integrity as integrity

        self.original = integrity.compute_integrity_checkpoint_for_equivalent
        self.count = 0
        self.fail_on_call: int | None = None
        self.bound: list[tuple[object, str]] = []

        original = self.original

        async def counting(*args, **kwargs):
            self.count += 1
            if self.fail_on_call is not None and self.count == self.fail_on_call:
                raise RuntimeError("p021 probe: failure in an after-mutation checkpoint")
            return await original(*args, **kwargs)

        self.counting = counting

    def install(self) -> None:
        for module in list(sys.modules.values()):
            if module is None or not getattr(module, "__name__", "").startswith("app."):
                continue
            for attr, value in list(vars(module).items()):
                if value is self.original:
                    setattr(module, attr, self.counting)
                    self.bound.append((module, attr))

    def reset(self) -> None:
        self.count = 0
        self.fail_on_call = None


async def _decay_through_the_tick(session, run, snapshot, scenario, drift_engine) -> None:
    """The tick's decay step (commits the decay) with no edge patches, on the measured session.

    `RealTick` reads its collaborators from the runner at call time; this runner carries only what the decay
    step reads, plus the static intervals `RealTick` captures at construction (unused by the decay step).
    """

    from types import SimpleNamespace

    from app.core.simulator.tick import RealTick

    async def _no_patch(**_kwargs):
        return []

    runner = SimpleNamespace(
        _logger=LOGGER,
        _trust_drift_engine=drift_engine,
        _build_edge_patch_for_equivalent=_no_patch,
        _broadcast_topology_edge_patch=lambda **_kw: None,
        _clearing_every_n_ticks=0,
        _real_clearing_time_budget_ms=250,
        _real_db_metrics_every_n_ticks=1,
        _real_db_bottlenecks_every_n_ticks=1,
        _real_last_tick_write_every_ms=0,
        _real_artifacts_sync_every_ms=0,
    )
    await RealTick(runner).apply_trust_decay_and_broadcast(
        session=session, run_id=run.run_id, run=run, debt_snapshot=snapshot, scenario=scenario
    )


def import_code_under_measurement() -> None:
    # Everything that could bind the checkpoint function by name, imported before the counter installs.
    import app.core.simulator.real_scenario_seeder  # noqa: F401
    import app.core.simulator.tick  # noqa: F401
    import app.core.simulator.trust_drift_engine  # noqa: F401
    import app.core.trustlines.service  # noqa: F401


def code_generation() -> str:
    from app.core.trustlines.service import TrustLineService

    return "internal-path" if hasattr(TrustLineService, "execute_update") else "direct-writes"


# ================================================================================ workloads


class _Sse:
    def next_event_id(self, run) -> str:
        return "evt"

    def broadcast(self, run_id, payload) -> None:
        pass


def _engine_for_drift(scenario: dict):
    from app.core.simulator.trust_drift_engine import TrustDriftEngine

    return TrustDriftEngine(
        sse=_Sse(), utc_now=lambda: datetime.now(timezone.utc), logger=LOGGER,
        get_scenario_raw=lambda _sid: scenario,
    )


def _run_for_drift(scenario: dict, trust_drift: dict):
    from app.core.simulator.models import RunRecord

    run = RunRecord(run_id="p021-bench", scenario_id=scenario["scenario_id"], mode="real", state="running",
                    started_at=datetime.now(timezone.utc))
    run.tick_index = 1
    run._real_seeded = True
    run._real_participants = [(participant_id(p["id"]), p["id"]) for p in scenario["participants"]]
    run._real_equivalents = list(scenario["equivalents"])
    run._scenario_raw = scenario
    scenario.setdefault("settings", {})["trust_drift"] = dict(trust_drift)
    return run


async def _trust_line_audit_rows(session) -> int:
    from sqlalchemy import func, select

    from app.db.models.audit_log import IntegrityAuditLog

    return int(
        await session.scalar(
            select(func.count()).select_from(IntegrityAuditLog).where(
                IntegrityAuditLog.operation_type.in_(("TRUST_LINE_CREATE", "TRUST_LINE_UPDATE", "TRUST_LINE_CLOSE"))
            )
        )
    )


async def _trust_line_state(session) -> dict:
    from sqlalchemy import select

    from app.db.models.equivalent import Equivalent
    from app.db.models.trustline import TrustLine

    rows = (
        await session.execute(
            select(TrustLine.id, TrustLine.limit, TrustLine.status, Equivalent.code).join(
                Equivalent, Equivalent.id == TrustLine.equivalent_id
            )
        )
    ).all()
    return {str(r[0]): (str(r[1]), str(r[2]), str(r[3])) for r in rows}


async def run_workload(workload: str, scenario_template: dict, sample_db: str) -> dict:
    """One sample. Returns the figures; raises only on a broken stand."""

    import copy

    scenario = copy.deepcopy(scenario_template)
    engine = make_engine(sample_db)
    statements = Statements(engine)
    maker = make_sessionmaker(engine)
    try:
        async with maker() as reader:
            state_before = await _trust_line_state(reader)
            audit_before = await _trust_line_audit_rows(reader)

        figures: dict = {}
        async with maker() as session:
            if workload in ("seed", "reseed"):
                from app.core.simulator.real_scenario_seeder import RealScenarioSeeder

                statements.active = True
                t0 = time.perf_counter()
                await RealScenarioSeeder().seed_scenario_into_db(session=session, scenario=scenario)
                await session.commit()
                figures["elapsed_s"] = time.perf_counter() - t0
                statements.active = False
            elif workload == "growth":
                spec = WORKLOADS["growth"]
                engine_ = _engine_for_drift(scenario)
                run = _run_for_drift(scenario, spec["trust_drift"])
                engine_.init_trust_drift(run, scenario)
                edges = {(t["from"], t["to"]) for t in growth_edges(scenario)}
                statements.active = True
                t0 = time.perf_counter()
                res = await engine_.apply_trust_growth(
                    run, session, edges, spec["equivalent"], 1, {e: 10.0 for e in edges}
                )
                figures["elapsed_s"] = time.perf_counter() - t0
                statements.active = False
                figures["result_updated_count"] = int(res.updated_count)
            elif workload == "decay":
                spec = WORKLOADS["decay"]
                engine_ = _engine_for_drift(scenario)
                run = _run_for_drift(scenario, spec["trust_drift"])
                engine_.init_trust_drift(run, scenario)
                ratio = Decimal(spec["debt_snapshot_ratio"])
                snapshot = {
                    (t["to"], t["from"], t["equivalent"]): (Decimal(t["limit"]) * ratio)
                    for t in decay_edges(scenario)
                }

                statements.active = True
                t0 = time.perf_counter()
                await _decay_through_the_tick(session, run, snapshot, scenario, engine_)
                figures["elapsed_s"] = time.perf_counter() - t0
                statements.active = False
            else:
                raise SystemExit(f"unknown workload {workload}")

        async with maker() as reader:
            state_after = await _trust_line_state(reader)
            audit_after = await _trust_line_audit_rows(reader)

        changed = [k for k in state_after if state_before.get(k) != state_after[k]]
        figures["applied_operations"] = len(changed)
        figures["touched_equivalents"] = len({state_after[k][2] for k in changed})
        figures["sql_statements"] = statements.count
        figures["trust_line_audit_rows_written"] = audit_after - audit_before
        return figures
    finally:
        await engine.dispose()


async def run_probe(workload: str, scenario_template: dict, sample_db: str, checkpoints: Checkpoints,
                    touched: int, generation: str) -> dict:
    """One failure after the first mutation; the owner's handling; a later commit; what persisted."""

    import copy

    scenario = copy.deepcopy(scenario_template)
    engine = make_engine(sample_db)
    statements = Statements(engine)
    maker = make_sessionmaker(engine)
    outcome: dict = {"failure_point": None, "raised": None, "later_commit": None}
    try:
        async with maker() as reader:
            state_before = await _trust_line_state(reader)
            audit_before = await _trust_line_audit_rows(reader)

        if generation == "internal-path":
            checkpoints.fail_on_call = touched + 1
            outcome["failure_point"] = f"checkpoint call {touched + 1} (first after-mutation checkpoint)"
        else:
            statements.fail_after_first_trust_line_write = True
            outcome["failure_point"] = "after the first trust-line write statement"

        async with maker() as session:
            try:
                if workload in ("seed", "reseed"):
                    from app.core.simulator.real_scenario_seeder import RealScenarioSeeder

                    await RealScenarioSeeder().seed_scenario_into_db(session=session, scenario=scenario)
                    await session.commit()
                elif workload == "growth":
                    spec = WORKLOADS["growth"]
                    engine_ = _engine_for_drift(scenario)
                    run = _run_for_drift(scenario, spec["trust_drift"])
                    engine_.init_trust_drift(run, scenario)
                    edges = {(t["from"], t["to"]) for t in growth_edges(scenario)}
                    await engine_.apply_trust_growth(run, session, edges, spec["equivalent"], 1, {e: 10.0 for e in edges})
                elif workload == "decay":
                    spec = WORKLOADS["decay"]
                    engine_ = _engine_for_drift(scenario)
                    run = _run_for_drift(scenario, spec["trust_drift"])
                    engine_.init_trust_drift(run, scenario)
                    ratio = Decimal(spec["debt_snapshot_ratio"])
                    snapshot = {
                        (t["to"], t["from"], t["equivalent"]): (Decimal(t["limit"]) * ratio)
                        for t in decay_edges(scenario)
                    }

                    await _decay_through_the_tick(session, run, snapshot, scenario, engine_)
                outcome["raised"] = False
            except RuntimeError as exc:
                outcome["raised"] = True
                outcome["error"] = str(exc)
            finally:
                checkpoints.fail_on_call = None
                statements.fail_after_first_trust_line_write = False
            outcome["failure_fired"] = bool(statements.failed or checkpoints.count >= touched + 1)
            # A LATER COMMIT on the same session - what the tick's tail would do next.
            try:
                await session.commit()
                outcome["later_commit"] = "committed"
            except Exception as exc:  # noqa: BLE001 - recorded, it is the measurement
                outcome["later_commit"] = f"failed: {type(exc).__name__}"
                await session.rollback()

        async with maker() as reader:
            state_after = await _trust_line_state(reader)
            audit_after = await _trust_line_audit_rows(reader)
        changed = [k for k in state_after if state_before.get(k) != state_after[k]]
        outcome["mutations_persisted"] = len(changed)
        outcome["audit_rows_persisted"] = audit_after - audit_before
        outcome["rolled_back_cleanly"] = not changed and audit_after == audit_before
        return outcome
    finally:
        checkpoints.fail_on_call = None
        await engine.dispose()


# ================================================================================ judging


def nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def judge(workload: str, samples: list[dict]) -> dict:
    th = CONFIG["thresholds"]
    sql = th["sql_statements"]
    cp = th["checkpoint_computations"]
    verdicts: list[dict] = []
    for i, s in enumerate(samples):
        budget = sql["per_applied_operation"] * s["applied_operations"] + sql["per_touched_equivalent"] * s[
            "touched_equivalents"
        ] + sql["constant"]
        sql_ok = s["sql_statements"] <= budget
        if s["applied_operations"] > 0:
            expected_cp = cp["per_touched_equivalent"] * s["touched_equivalents"]
            cp_ok = s["checkpoint_computations"] == expected_cp
        else:
            expected_cp = None
            cp_ok = None  # the rule covers nonempty batches only
        verdicts.append({"sample": i, "sql_budget": budget, "sql_ok": sql_ok, "checkpoints_expected": expected_cp,
                         "checkpoints_ok": cp_ok})
    elapsed = [s["elapsed_s"] for s in samples]
    summary = {
        "samples": len(samples),
        "elapsed_p50_s": round(statistics.median(elapsed), 4),
        "elapsed_p95_s": round(nearest_rank(elapsed, 0.95), 4),
        "elapsed_max_s": round(max(elapsed), 4),
        "sql_statements": sorted({s["sql_statements"] for s in samples}),
        "sql_budget": sorted({v["sql_budget"] for v in verdicts}),
        "checkpoint_computations": sorted({s["checkpoint_computations"] for s in samples}),
        "checkpoints_expected": sorted({v["checkpoints_expected"] for v in verdicts if v["checkpoints_expected"] is not None}),
        "applied_operations": sorted({s["applied_operations"] for s in samples}),
        "touched_equivalents": sorted({s["touched_equivalents"] for s in samples}),
        "trust_line_audit_rows_written": sorted({s["trust_line_audit_rows_written"] for s in samples}),
        "sql_pass": all(v["sql_ok"] for v in verdicts),
        "checkpoints_pass": (None if all(v["checkpoints_ok"] is None for v in verdicts)
                             else all(v["checkpoints_ok"] for v in verdicts if v["checkpoints_ok"] is not None)),
    }
    if workload == "seed":
        summary["seed_p95_max_s"] = th["seed_p95_seconds"]["max"]
        summary["seed_p95_pass"] = summary["elapsed_p95_s"] <= th["seed_p95_seconds"]["max"]
    return summary


# ================================================================================ main


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


async def main_async(args) -> dict:
    community = load_community()
    scenario = scenario_of(community)
    import_code_under_measurement()
    checkpoints = Checkpoints()
    checkpoints.install()
    generation = code_generation()

    empty, seeded = await build_templates(scenario)
    created = [empty, seeded]
    report: dict = {
        "label": args.label,
        "git_head": git_head(),
        "code_generation": generation,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "config": str(CONFIG_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "checkpoint_function_bound_in": sorted(f"{m.__name__}.{a}" for m, a in checkpoints.bound),
        "workloads": {},
    }
    try:
        total = RUN["warmup_samples"] + RUN["samples_after_warmup"]
        for workload in args.workloads:
            template = empty if workload == "seed" else seeded
            samples: list[dict] = []
            for i in range(total):
                name = checked_bench_name("geov0_bench_p021s1_sample")
                await create_db(name, template=template)
                try:
                    checkpoints.reset()
                    figures = await run_workload(workload, scenario, name)
                    figures["checkpoint_computations"] = checkpoints.count
                finally:
                    await drop_db(name)
                if i >= RUN["warmup_samples"]:
                    samples.append(figures)
            summary = judge(workload, samples)
            touched = max(summary["touched_equivalents"]) if summary["touched_equivalents"] else 0

            name = checked_bench_name("geov0_bench_p021s1_probe")
            await create_db(name, template=template)
            try:
                checkpoints.reset()
                probe = await run_probe(workload, scenario, name, checkpoints, touched, generation)
            finally:
                await drop_db(name)
            report["workloads"][workload] = {"summary": summary, "rollback_probe": probe, "samples": samples}
            print(json.dumps({workload: summary, "probe": probe}, default=str), flush=True)
    finally:
        for name in created:
            await drop_db(name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--workloads", nargs="+", default=["seed", "reseed", "growth", "decay"])
    args = parser.parse_args()
    report = asyncio.run(main_async(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
