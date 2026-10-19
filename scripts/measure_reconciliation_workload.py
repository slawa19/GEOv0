"""Prepare a debt-reconciliation workload on a disposable dev database (programme 047, closes BACKLOG 047-3).

The integrity deadlines (`INTEGRITY_ATTEMPT_TIMEOUT_SECONDS`, `INTEGRITY_DB_*_TIMEOUT_MS`, `app/config.py`) rest on how
long the scheduled reconciliation takes for a given history. This seeds a community by its recipe, in ONE process,
with `--payments` extra direct payments placed BEFORE the recipe's commands - 1.00 at a time, alternately payer ->
payee and back, so they net to zero and the recipe then runs as written - and prints the counts the reconciliation
will read. It times nothing: the measurement is the operator's own command over the result, which is the same
verification the integrity loop runs:

    $env:ENV = "dev"; $env:DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_<slug>"
    python scripts/dev_database.py create
    python -m alembic -c migrations/alembic.ini upgrade head
    python scripts/measure_reconciliation_workload.py --community greenfield-village-100 --payments 8000
    Measure-Command { python scripts/reconcile_debts.py --equivalent UAH }

Only a disposable empty database is accepted (`scripts/seed_recipe.py`, the same guards as the seed). The recipe's
acceptance checks are not run: the reconciliation the command then runs is the check that matters here.

`--measure CODE` (047 `T4703`) seeds nothing: over the database `DATABASE_URL` names, it runs the verifier
(`verify_journal_equals_change`) of that equivalent in one verification snapshot `--repeat` times and prints, per
run, the verdict, `entries_read`, the wall time, and the PEAK of Python allocations (`tracemalloc`, started after a
warm-up run, so compiled statements and lazy imports are not counted). The timed runs are without `tracemalloc`;
the peak runs are separate, because tracing slows the run. It reads only; it never publishes a result or holds:

    python scripts/measure_reconciliation_workload.py --measure UAH --repeat 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "seeds", "communities"))

#: The pair of the first command of `greenfield-village-100` (a direct payment on an open trust line).
_DEFAULT_PAYER, _DEFAULT_PAYEE = "greenfield_procurement_desk", "anton_petrenko"


def _extra_payments(count: int, *, payer: str, payee: str, equivalent: str) -> list[dict]:
    commands = []
    for index in range(count):
        frm, to = (payer, payee) if index % 2 == 0 else (payee, payer)
        commands.append({
            "id": f"measure.{index:06d}", "op": "payment", "equivalent": equivalent, "payer": frm, "payee": to,
            "amount": "1.00", "routing": "direct", "why": "047 measurement workload",
            "expect": "COMMITTED; the pair nets to zero",
        })
    return commands


async def _run(args) -> int:
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal, engine
    from scripts import seed_recipe as seed

    try:
        seed.assert_environment_is_safe()
        seed.assert_target_is_disposable(await seed.database_url(AsyncSessionLocal))
        async with AsyncSessionLocal() as session:
            await seed.assert_database_is_empty(session)
        community = seed.community_schema.load_community(args.community)
        recipe = seed.recipe_schema.load_recipe(args.community)
        recipe["commands"] = _extra_payments(
            args.payments, payer=args.payer, payee=args.payee, equivalent=args.equivalent
        ) + recipe["commands"]
        run = seed._Run(AsyncSessionLocal, community, recipe)
        await run.create_equivalents()
        await run.create_participants()
        await run.create_trustlines()
        await run.take_baselines()
        await run.run_commands()
        async with AsyncSessionLocal() as session:
            counts = (await session.execute(text(
                "SELECT eq.code,"
                " (SELECT count(*) FROM transactions t WHERE t.type = 'PAYMENT' AND t.state = 'COMMITTED'"
                "   AND t.payload->>'equivalent' = eq.code),"
                " (SELECT count(*) FROM debt_journal_entries j WHERE j.equivalent_id = eq.id)"
                " FROM equivalents eq ORDER BY eq.code"
            ))).all()
    except seed.SeedRefusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    print(f"seeded {args.community} with {args.payments} extra payments ({run.report.summary()})")
    for code, payments, entries in counts:
        print(f"{code}: committed payments={payments} journal entries={entries}")
    return 0


async def _measure(code: str, repeat: int) -> int:
    import json
    import time
    import tracemalloc

    from sqlalchemy import select

    from app.core.ledger.reconciliation import bound_the_waits, open_verification_snapshot, verify_journal_equals_change
    from app.db.models.equivalent import Equivalent
    from app.db.session import AsyncSessionLocal, engine

    async def _once(equivalent_id):
        async with AsyncSessionLocal() as session:
            await open_verification_snapshot(session)
            await bound_the_waits(session)
            started = time.perf_counter()
            outcome = await verify_journal_equals_change(session, equivalent_id)
            elapsed = time.perf_counter() - started
            await session.rollback()
        return outcome, elapsed

    try:
        async with AsyncSessionLocal() as session:
            equivalent_id = (await session.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
        await _once(equivalent_id)  # warm-up: statement compilation and lazy imports are not the history
        for index in range(repeat):
            outcome, elapsed = await _once(equivalent_id)
            tracemalloc.start()
            tracemalloc.reset_peak()
            _traced, traced_elapsed = await _once(equivalent_id)
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            print(json.dumps({
                "run": index + 1, "equivalent": code, "status": outcome.status, "entries_read": outcome.entries_read,
                "operations_examined": outcome.detail()["criterion_b"]["operations_examined"],
                "transactions_read": outcome.transactions_read, "wall_s": round(elapsed, 3),
                "peak_mib": round(peak / 2**20, 2), "wall_s_under_tracemalloc": round(traced_elapsed, 3),
            }))
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--community", help="a community under seeds/communities with a recipe")
    parser.add_argument("--payments", type=int, help="extra direct payments before the recipe")
    parser.add_argument("--measure", metavar="CODE", help="seed nothing; measure the verifier of this equivalent")
    parser.add_argument("--repeat", type=int, default=3, help="with --measure: how many runs")
    parser.add_argument("--equivalent", default="UAH")
    parser.add_argument("--payer", default=_DEFAULT_PAYER, help="community ref")
    parser.add_argument("--payee", default=_DEFAULT_PAYEE, help="community ref; trusts the payer")
    args = parser.parse_args(argv)
    if args.measure:
        if args.community or args.payments is not None:
            parser.error("--measure seeds nothing: no --community or --payments")
        if args.repeat < 1:
            parser.error("--repeat must be >= 1")
        return asyncio.run(_measure(args.measure, args.repeat))
    if not args.community or args.payments is None:
        parser.error("--community and --payments are required to seed")
    if args.payments < 0:
        parser.error("--payments must be >= 0")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
