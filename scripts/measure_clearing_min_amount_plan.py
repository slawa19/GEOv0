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
old threshold quantum ``0.01`` - plus planted boundary triangles (below), and the triangle
detector's real SQL, run five times with the predicate and five times without.  It prints
what the predicate's own filter removed, top-level buffer counts and the delta between
them, execution-time min/median/max, the number of cycles each variant finds, and both
full plans.

WHAT NOW GUARDS THE MEASUREMENT (012 / ``T1211`` second review round, finding ``P3``).  A
buffer comparison between "with predicate" and "without predicate" says nothing unless the
predicate actually threw rows away on the population it ran against.  Two things used to be
left to luck, and both are now closed:

* *The population.*  Random edges on the ``0.01`` quantum only matter if they happen to
  land inside a detected cycle.  On the first run they did; nothing made them.  The graph
  now also contains ``BOUNDARY_TRIANGLES`` triangles whose three edges are *all* exactly
  ``0.01``, on participants the random graph never touches, so ``LEAST`` over them is
  ``0.01``, ``> 0.01`` is false, and the predicate has something to discard by construction.
* *The check.*  ``rows_removed_by_least_predicate`` parses ``Rows Removed by Join Filter``
  out of the with-predicate plan and the script exits non-zero when the total is zero (or
  when the predicate does not appear in the plan at all).  A degenerate population now
  fails loudly instead of printing two buffer counts that measure nothing.

WHY THE OLD "rows returned" LINE WAS WRONG.  It claimed the two variants could not return
the same rows, and printed ``with=100 without=100``: both are the detector's real statement,
``LIMIT 100`` and all, so the counts were clamped to the same number and distinguished
nothing.  The plans are still measured with the real ``LIMIT`` - that is the statement whose
cost is in question - and the cycle counts are taken separately by a ``count(*)`` over the
same query with ``ORDER BY``/``LIMIT`` stripped, where the two variants genuinely differ.

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

#: The dropped threshold quantum itself.  An edge worth exactly this much survives
#: ``amount > 0`` but not ``LEAST(...) > 0.01``.
BOUNDARY_AMOUNT = Decimal("0.01")

#: One random edge in fifty sits exactly on the dropped threshold.  This shapes the
#: amount distribution realistically; it does NOT guarantee anything, because whether such
#: an edge lands inside a detected triangle is luck.  The guarantee comes from
#: ``BOUNDARY_TRIANGLES`` below.
BOUNDARY_EVERY = 50

#: Triangles planted with all three edges at ``BOUNDARY_AMOUNT``, on participants outside
#: the random graph's index range.  ``LEAST`` over such a triangle is exactly the threshold,
#: so the predicate must discard it - and each planted triangle is found three times (once
#: per rotation of ``d1``), so this floor is ``3 * BOUNDARY_TRIANGLES`` discarded rows.
BOUNDARY_TRIANGLES = 12


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
{tail}
"""

#: The detector's real tail.  The plans are measured with it, because it is part of the
#: statement whose cost is in question.
REAL_TAIL = "ORDER BY clear_amount DESC\nLIMIT 100"


def triangle_query(extra: str, *, tail: str = REAL_TAIL) -> str:
    return TRIANGLE_SQL.format(
        a1=AUTO_CLEARING.format(a=1),
        a2=AUTO_CLEARING.format(a=2),
        a3=AUTO_CLEARING.format(a=3),
        extra=extra,
        tail=tail,
    )


def cycle_count_query(extra: str) -> str:
    """Count the cycles a variant finds, with ``ORDER BY``/``LIMIT`` stripped.

    Under the detector's ``LIMIT 100`` both variants report 100 and the comparison is
    vacuous; the difference only becomes visible when nothing clamps it.
    """
    return "SELECT count(*) FROM (" + triangle_query(extra, tail="") + ") AS cycles"


def _make_participant(index: int) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        pid=f"geo:p{index}:{uuid.uuid4().hex[:8]}",
        display_name=f"p{index}",
        type="person",
        public_key=uuid.uuid4().hex * 2,
        status="active",
    )


def _edge(
    session: AsyncSession,
    equivalent: Equivalent,
    debtor: Participant,
    creditor: Participant,
    amount: Decimal,
) -> None:
    """One debt plus the active trust line the detector requires to accept it."""
    session.add(
        Debt(
            id=uuid.uuid4(),
            debtor_id=debtor.id,
            creditor_id=creditor.id,
            equivalent_id=equivalent.id,
            amount=amount,
        )
    )
    session.add(
        TrustLine(
            id=uuid.uuid4(),
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
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

    # The random graph uses indices [0, N_PARTICIPANTS); the planted triangles get their
    # own participants after that, so no random edge can join, shadow or duplicate them.
    participants = [_make_participant(i) for i in range(N_PARTICIPANTS + 3 * BOUNDARY_TRIANGLES)]
    for participant in participants:
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
            BOUNDARY_AMOUNT
            if made % BOUNDARY_EVERY == 0
            else Decimal(rnd.randrange(1, 100000)) / Decimal(100)
        )
        _edge(session, equivalent, participants[a], participants[b], amount)
        made += 1
        if made % 500 == 0:
            await session.flush()

    # Planted boundary triangles: x -> y -> z -> x, every edge exactly at the threshold.
    # The detector matches d1.creditor = d2.debtor, d2.creditor = d3.debtor,
    # d3.creditor = d1.debtor, so this closed triple is a cycle it finds - and one whose
    # LEAST is exactly BOUNDARY_AMOUNT, which `> :min_amount` rejects.  This, not the
    # random 1-in-50 edges, is what makes the predicate provably bite.
    for triangle in range(BOUNDARY_TRIANGLES):
        base = N_PARTICIPANTS + 3 * triangle
        ring = participants[base:base + 3]
        for offset in range(3):
            _edge(session, equivalent, ring[offset], ring[(offset + 1) % 3], BOUNDARY_AMOUNT)
    await session.commit()

    for table in ("debts", "trust_lines", "participants"):
        await session.execute(text(f"ANALYZE {table}"))
    await session.commit()
    return equivalent


async def explain(session: AsyncSession, sql: str, params: dict, label: str) -> tuple[str, str]:
    """Run one variant ``REPS`` times.  Returns (summary line, last plan text).

    Nothing is printed here: the anti-vacuum guard must be able to abort before a single
    comparison number reaches the reader.
    """
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
    summary = (
        f"--- {label}: exec ms  min={times[0]:.1f} median={times[len(times) // 2]:.1f} "
        f"max={times[-1]:.1f}  toplevel_{top_buffers}"
    )
    return summary, plan_text


def top_shared_hit(plan_text: str) -> int:
    """Shared buffer hits of the plan's top node - the whole statement's buffer traffic."""
    for line in plan_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Buffers:"):
            for field in stripped[len("Buffers:"):].split():
                if field.startswith("hit="):
                    return int(field[len("hit="):].rstrip(","))
            break
    raise SystemExit("no top-level 'Buffers: shared hit=' line in the plan; nothing to compare")


def rows_removed_by_least_predicate(plan_text: str) -> tuple[int, int]:
    """Read the predicate's own filter counters out of an ``EXPLAIN ANALYZE`` plan.

    Returns ``(nodes carrying the LEAST filter, rows it removed)``.  Text-format EXPLAIN
    omits a ``Rows Removed by ...`` line entirely when the count is zero, so a filter node
    without a counter contributes zero - which is exactly the case the caller must catch.
    """
    nodes = 0
    removed = 0
    pending: str | None = None  # kind of the filter on the current node, if it is ours
    for raw in plan_text.splitlines():
        line = raw.strip()
        if line.startswith("->") or "  ->  " in raw:
            pending = None  # a new plan node begins; counters below belong to it
        if line.startswith("Rows Removed by "):
            label, _, value = line.partition(":")
            if pending is not None and label[len("Rows Removed by "):].strip() == pending:
                removed += int(value.strip().split()[0])
            continue
        for kind in ("Join Filter", "Filter"):
            if line.startswith(kind + ":"):
                if "LEAST(" in line:
                    pending = kind
                    nodes += 1
                break
    return nodes, removed


def assert_predicate_bit(plan_text: str) -> tuple[int, int]:
    """Refuse to report a comparison the population cannot support.

    The whole measurement is "what does the predicate cost".  If the predicate discarded
    nothing on this graph, the two buffer counts differ only by the bookkeeping of an
    expression that never rejected a row, and comparing them measures noise, not the
    predicate.  That is a broken run, not a result, so it exits non-zero.
    """
    nodes, removed = rows_removed_by_least_predicate(plan_text)
    if nodes == 0:
        print(
            "ANTI-VACUUM CHECK FAILED: no plan node carries the LEAST(...) filter.\n"
            "  The predicate under measurement is not in the plan at all, so whatever the\n"
            "  buffer counts below would have compared, it is not this predicate.  Nothing\n"
            "  is reported.  The plan follows.\n\n"
            + plan_text,
            file=sys.stderr,
        )
        raise SystemExit(1)
    if removed == 0:
        print(
            "ANTI-VACUUM CHECK FAILED: Rows Removed by the LEAST(...) filter is 0.\n"
            "  The predicate ran and filtered nothing: no cycle this detector found on this\n"
            "  population sat at or below the threshold.  A with/without buffer comparison on\n"
            "  such a population measures nothing - both variants did the same work on the\n"
            "  same rows, and any difference between them would be noise dressed as a result.\n"
            f"  The population behind this run: BOUNDARY_TRIANGLES={BOUNDARY_TRIANGLES}, "
            f"BOUNDARY_EVERY={BOUNDARY_EVERY},\n"
            f"  BOUNDARY_AMOUNT={BOUNDARY_AMOUNT}.  Those are the CURRENT values, not a\n"
            "  target: if BOUNDARY_TRIANGLES reads 0 it is the cause, because the planted\n"
            "  triangles are the only thing that GUARANTEES the predicate something to\n"
            "  discard - the every-Nth boundary edge is a distribution, not a guarantee.\n"
            "  Give it a boundary population and re-run.  Nothing is reported below.\n\n"
            + plan_text,
            file=sys.stderr,
        )
        raise SystemExit(1)
    return nodes, removed


async def provenance(session: AsyncSession) -> None:
    """Print what a reader needs to judge the numbers below, not only the numbers."""
    version = (await session.execute(text("SELECT version()"))).scalar_one()
    print(f"server:       {version}")
    print(f"graph:        {N_PARTICIPANTS} random participants, {N_EDGES} debt edges, "
          f"1 in {BOUNDARY_EVERY} exactly at the dropped threshold {BOUNDARY_AMOUNT}")
    # The guarantee is a property of the constants, so the header states it only when the
    # constants actually provide it - a header that claims it unconditionally is the same
    # kind of unearned assertion this script exists to replace.
    if BOUNDARY_TRIANGLES:
        print(f"              + {BOUNDARY_TRIANGLES} planted triangles "
              f"({3 * BOUNDARY_TRIANGLES} dedicated participants, {3 * BOUNDARY_TRIANGLES} "
              f"edges) with ALL three")
        print(f"              edges at {BOUNDARY_AMOUNT}, so the predicate provably has cycles "
              f"to discard rather than by luck")
    else:
        print("              NO planted boundary triangles: nothing here guarantees the "
              "predicate has")
        print("              anything to discard, so the anti-vacuum check is the only thing "
              "standing")
    print(f"repetitions:  {REPS} per variant, EXPLAIN (ANALYZE, BUFFERS, TIMING)")
    print("read:         the buffer counts are the result.  They are deterministic for this")
    print("              seeded population and repeat exactly across runs, while the execution")
    print("              times move more between runs of this script than between the two")
    print("              variants, so the times do not discriminate and are printed only as")
    print("              order of magnitude.")
    print()


async def main() -> None:
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    with_predicate = "AND LEAST(d1.amount, d2.amount, d3.amount) > :min_amount"
    async with session_factory() as session:
        await provenance(session)
        equivalent = await build_graph(session)
        params = {"equivalent_id": equivalent.id, "min_amount": BOUNDARY_AMOUNT}
        params_without = {"equivalent_id": equivalent.id}

        summary_with, plan_with = await explain(
            session, triangle_query(with_predicate), params, "WITH min_amount"
        )
        # Before any comparison number is printed: did the predicate actually filter?
        nodes, removed = assert_predicate_bit(plan_with)
        summary_without, plan_without = await explain(
            session, triangle_query(""), params_without, "WITHOUT min_amount"
        )

        print(
            f"anti-vacuum:  the LEAST(...) filter appears on {nodes} plan node(s) and removed "
            f"{removed} rows;"
        )
        print("              the predicate did filter, so the two lines below compare real work.")
        print(summary_with)
        print(summary_without)

        hit_with = top_shared_hit(plan_with)
        hit_without = top_shared_hit(plan_without)
        delta = hit_without - hit_with
        print(
            f"buffer delta: {hit_without} - {hit_with} = {delta} shared hits, "
            f"{100.0 * delta / hit_without:.3f}% of the statement - what dropping the"
        )
        print("              predicate costs, measured on a population where it did filter.")

        cycles_with = (
            await session.execute(text(cycle_count_query(with_predicate)), params)
        ).scalar_one()
        cycles_without = (
            await session.execute(text(cycle_count_query("")), params_without)
        ).scalar_one()
        print(
            f"cycles found (ORDER BY/LIMIT stripped): with={cycles_with} without={cycles_without} "
            f"-> predicate rejects {cycles_without - cycles_with}"
        )
        print("              (the plans above keep the detector's real LIMIT 100, under which")
        print("               both variants return 100 rows and the counts distinguish nothing)")

        print("\n===== PLAN WITH min_amount =====\n" + plan_with)
        print("\n===== PLAN WITHOUT min_amount =====\n" + plan_without)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
