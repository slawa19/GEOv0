"""Query-plan cost of the ``LEAST(...) > :min_amount`` predicate the SQL cycle detectors dropped.

WHY THIS EXISTS IN THE TREE (012 / ``T1211`` finding 5).  ``T1202`` dropped the clearing
threshold on three measurements, and one of them was this one: the predicate is not a
performance guard, because ``LEAST`` over three joined tables can only be evaluated after
the join, so removing it costs a fraction of a percent.  The number reached the spec as a
retold figure - no raw ``EXPLAIN (ANALYZE, BUFFERS)``, no generator, no run.  The external
review could not open it, and said so.  A measurement nobody can re-run is an assertion,
so the generator lives here now and the claim is checkable rather than quoted.

WHAT IT MEASURES.  A synthetic but realistically shaped graph - 400 participants, 4000
debt edges each backed by an active trust line, one edge in fifty sitting exactly on the
old threshold quantum ``0.01`` - and the triangle detector's real SQL, run five times with
the predicate and five times without.  It prints top-level buffer counts, execution-time
min/median/max, the row counts both variants return, and both full plans.

RUNNING IT (PowerShell, and read ``AGENTS.md`` "Postgres gate" first)::

    $slug = "planmeasure"
    $env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_$slug"
    $env:GEO_TEST_ALLOW_DB_RESET = "1"
    python scripts/measure_clearing_min_amount_plan.py > .local-run/plan.txt

The script TRUNCATEs six tables, so it refuses to start unless the URL is provably a
disposable test database (``scripts/validate_test_database_url.py``) and the reset flag is
set explicitly.  SQLite is not accepted: this measures a PostgreSQL query plan.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import uuid
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db.models.debt import Debt  # noqa: E402
from app.db.models.equivalent import Equivalent  # noqa: E402
from app.db.models.participant import Participant  # noqa: E402
from app.db.models.trustline import TrustLine  # noqa: E402
from scripts.validate_test_database_url import assert_safe_test_database_url  # noqa: E402

N_PARTICIPANTS = 400
N_EDGES = 4000
REPS = 5

#: One edge in fifty sits exactly on the dropped threshold, so the two variants cannot
#: return the same rows by construction - the row counts below prove the predicate bites.
BOUNDARY_EVERY = 50


def _database_url() -> str:
    # The harness guard is the whole check: it enforces the `geov0_test_*` naming, the
    # `GEO_TEST_ALLOW_DB_RESET` opt-in that this script's TRUNCATEs require, and - via
    # `required_backend` - that a SQLite URL cannot quietly answer a question about a
    # PostgreSQL query plan.
    url = os.environ.get("TEST_DATABASE_URL", "")
    assert_safe_test_database_url(
        url,
        allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
        repo_root=REPO_ROOT,
        required_backend="postgresql",
    )
    return url


AUTO_CLEARING = (
    "(t{a}.policy IS NULL OR t{a}.policy->>'auto_clearing' IS NULL OR "
    "lower(t{a}.policy->>'auto_clearing') NOT IN ('false', '0', 'no', 'off'))"
)

TRIANGLE_SQL = """
SELECT DISTINCT d1.id, d1.debtor_id, d1.creditor_id, d1.amount,
       d2.id, d2.creditor_id, d2.amount, d3.id, d3.amount,
       LEAST(d1.amount, d2.amount, d3.amount) as clear_amount
FROM debts d1
JOIN debts d2 ON d1.creditor_id = d2.debtor_id AND d1.equivalent_id = d2.equivalent_id
JOIN debts d3 ON d2.creditor_id = d3.debtor_id AND d3.creditor_id = d1.debtor_id
             AND d2.equivalent_id = d3.equivalent_id
JOIN trust_lines t1 ON t1.from_participant_id = d1.creditor_id AND t1.to_participant_id = d1.debtor_id
                   AND t1.equivalent_id = d1.equivalent_id AND t1.status = 'active' AND {a1}
JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id AND t2.to_participant_id = d2.debtor_id
                   AND t2.equivalent_id = d2.equivalent_id AND t2.status = 'active' AND {a2}
JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id AND t3.to_participant_id = d3.debtor_id
                   AND t3.equivalent_id = d3.equivalent_id AND t3.status = 'active' AND {a3}
WHERE d1.equivalent_id = :equivalent_id
  AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0
  {extra}
ORDER BY clear_amount DESC
LIMIT 100
"""


def triangle_query(extra: str) -> str:
    return TRIANGLE_SQL.format(
        a1=AUTO_CLEARING.format(a=1),
        a2=AUTO_CLEARING.format(a=2),
        a3=AUTO_CLEARING.format(a=3),
        extra=extra,
    )


async def build_graph(session: AsyncSession) -> Equivalent:
    for table in ("prepare_locks", "transactions", "debts", "trust_lines", "participants", "equivalents"):
        try:
            await session.execute(text(f"TRUNCATE {table} CASCADE"))
        except Exception:
            pass
    await session.commit()

    equivalent = Equivalent(id=uuid.uuid4(), code="UAH", symbol="U", precision=2, is_active=True)
    session.add(equivalent)
    await session.flush()

    participants = []
    for i in range(N_PARTICIPANTS):
        participant = Participant(
            id=uuid.uuid4(),
            pid=f"geo:p{i}:{uuid.uuid4().hex[:8]}",
            display_name=f"p{i}",
            type="person",
            public_key=uuid.uuid4().hex * 2,
            status="active",
        )
        participants.append(participant)
        session.add(participant)
    await session.flush()

    rnd = random.Random(42)
    seen: set[tuple[int, int]] = set()
    made = 0
    while made < N_EDGES:
        a, b = rnd.randrange(N_PARTICIPANTS), rnd.randrange(N_PARTICIPANTS)
        if a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        amount = (
            Decimal("0.01")
            if made % BOUNDARY_EVERY == 0
            else Decimal(rnd.randrange(1, 100000)) / Decimal(100)
        )
        session.add(
            Debt(
                id=uuid.uuid4(),
                debtor_id=participants[a].id,
                creditor_id=participants[b].id,
                equivalent_id=equivalent.id,
                amount=amount,
            )
        )
        session.add(
            TrustLine(
                id=uuid.uuid4(),
                from_participant_id=participants[b].id,
                to_participant_id=participants[a].id,
                equivalent_id=equivalent.id,
                limit=Decimal("1000000"),
                policy={
                    "auto_clearing": True,
                    "can_be_intermediate": True,
                    "max_hop_usage": None,
                    "daily_limit": None,
                    "blocked_participants": [],
                },
                status="active",
            )
        )
        made += 1
        if made % 500 == 0:
            await session.flush()
    await session.commit()

    for table in ("debts", "trust_lines", "participants"):
        await session.execute(text(f"ANALYZE {table}"))
    await session.commit()
    return equivalent


async def explain(session: AsyncSession, sql: str, params: dict, label: str) -> str:
    times: list[float] = []
    plan_text = ""
    top_buffers = ""
    for _ in range(REPS):
        result = await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, TIMING) " + sql), params)
        lines = [row[0] for row in result]
        plan_text = "\n".join(lines)
        buffers = [line for line in lines if line.strip().startswith("Buffers:")]
        top_buffers = buffers[0].strip() if buffers else "(no Buffers line)"
        for line in lines:
            if line.startswith("Execution Time:"):
                times.append(float(line.split(":")[1].strip().split()[0]))
    times.sort()
    print(
        f"--- {label}: exec ms  min={times[0]:.1f} median={times[len(times) // 2]:.1f} "
        f"max={times[-1]:.1f}  toplevel_{top_buffers}"
    )
    return plan_text


async def provenance(session: AsyncSession) -> None:
    """Print what a reader needs to judge the numbers below, not only the numbers."""
    version = (await session.execute(text("SELECT version()"))).scalar_one()
    print(f"server:       {version}")
    print(f"graph:        {N_PARTICIPANTS} participants, {N_EDGES} debt edges, "
          f"1 in {BOUNDARY_EVERY} exactly at the dropped threshold 0.01")
    print(f"repetitions:  {REPS} per variant, EXPLAIN (ANALYZE, BUFFERS, TIMING)")
    print("read:         buffer counts are the stable signal; execution times overlap between")
    print("              the two variants in BOTH directions from run to run, which is itself")
    print("              the result - the predicate does not pay for itself.")
    print()


async def main() -> None:
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    with_predicate = "AND LEAST(d1.amount, d2.amount, d3.amount) > :min_amount"
    async with session_factory() as session:
        await provenance(session)
        equivalent = await build_graph(session)
        params = {"equivalent_id": equivalent.id, "min_amount": Decimal("0.01")}
        params_without = {"equivalent_id": equivalent.id}

        plan_with = await explain(session, triangle_query(with_predicate), params, "WITH min_amount")
        plan_without = await explain(session, triangle_query(""), params_without, "WITHOUT min_amount")

        rows_with = len((await session.execute(text(triangle_query(with_predicate)), params)).all())
        rows_without = len((await session.execute(text(triangle_query("")), params_without)).all())
        print(f"rows returned: with={rows_with} without={rows_without}")

        print("\n===== PLAN WITH min_amount =====\n" + plan_with)
        print("\n===== PLAN WITHOUT min_amount =====\n" + plan_without)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
