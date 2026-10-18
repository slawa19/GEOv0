"""Run the debt reconciliation now - the same verification the integrity loop runs (programme 047, T4702).

The integrity loop reconciles at process start and then once a day (`INTEGRITY_CHECKPOINT_INTERVAL_SECONDS`). After
a repair in the database, a restore, or a hold, the operator runs this instead of waiting for the next run
(`docs/ru/runbook-integrity-hold.md` §3). It is a caller, not a second verifier: each equivalent goes through
`run_scheduled_reconciliation` - one read snapshot, the stored result row, and on a FAILED the step 5c reaction
(re-verify, then hold the equivalent). It never clears a hold; an admin does, after a later PASSED.

    python scripts/reconcile_debts.py --all
    python scripts/reconcile_debts.py --equivalent UAH --equivalent HOUR

The database is the one `DATABASE_URL` names, as for the application. Exit codes:

    0  every equivalent checked PASSED
    1  a discrepancy: some equivalent is FAILED or UNVERIFIABLE (named on stdout, with its findings)
    2  the check did not happen, wholly or for some equivalent: usage, an unknown code, no equivalent at all,
       the configuration or the database unusable, or the verifier erred - such an equivalent is printed as
       NOT CHECKED. A discrepancy found elsewhere in the same run still exits 1.

Not seen by this command: a scheduled run of the hub publishing at the same moment may leave its own verdict as
the latest stored row (`record_outcome`); the printed line then says that the stored row differs. The Redis lock
of the integrity loop is not taken.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _latest_detail(session_factory, equivalent_id) -> tuple[str | None, dict]:
    from sqlalchemy import select

    from app.db.reconciliation_tables import debt_reconciliation_results

    results = debt_reconciliation_results.c
    async with session_factory() as session:
        row = (
            await session.execute(
                select(results.status, results.detail).where(
                    results.equivalent_id == equivalent_id, results.is_latest.is_(True)
                )
            )
        ).first()
    if row is None:
        return None, {}
    return row.status, row.detail if isinstance(row.detail, dict) else json.loads(row.detail or "{}")


async def _run(codes: list[str], check_all: bool, session_factory) -> int:
    from sqlalchemy import select

    from app.core.ledger import reconciliation as rec
    from app.db.models import Equivalent

    wanted = sorted({code.strip().upper() for code in codes if code.strip()})
    async with session_factory() as session:
        query = select(Equivalent.id, Equivalent.code).order_by(Equivalent.code)
        if not check_all:
            query = query.where(Equivalent.code.in_(wanted))
        rows = (await session.execute(query)).all()

    unknown = sorted(set(wanted) - {code for _, code in rows})
    if unknown:
        print(f"unknown equivalent code(s): {', '.join(unknown)}; nothing was checked", file=sys.stderr)
        return 2
    if not rows:
        print("no equivalent exists; nothing was checked", file=sys.stderr)
        return 2

    discrepancy = not_checked = False
    for equivalent_id, code in rows:
        counts = await rec.run_scheduled_reconciliation(session_factory, equivalent_ids=[equivalent_id])
        verdict = next((status for status in (rec.PASSED, rec.FAILED, rec.UNVERIFIABLE) if counts[status]), None)
        if verdict is None:
            not_checked = True
            print(f"{code}: NOT CHECKED - the verifier erred (see the log); no result was recorded")
            continue
        discrepancy |= verdict != rec.PASSED
        stored, detail = await _latest_detail(session_factory, equivalent_id)
        line = (
            f"{code}: {verdict} - entries_read={detail.get('entries_read')} "
            f"findings={detail.get('findings_total', 0)} missing_evidence={detail.get('missing_evidence') or []}"
        )
        if verdict == rec.FAILED:
            hold = next((name for key, name in (
                (rec.HOLD_SET, "set"), (rec.HOLD_ALREADY_HELD, "already held"),
                (rec.HOLD_NOT_CONFIRMED, "not confirmed by the re-run"), (rec.HOLD_EQUIVALENT_GONE, "equivalent gone"),
            ) if counts[f"hold_{key}"]), "ERROR (see the log)" if counts["hold_errors"] else "unknown")
            line += f" hold={hold}"
        if stored != verdict:
            line += f" (the latest stored row says {stored}: another run published after this one)"
        print(line)
        for finding in detail.get("findings") or []:
            print(f"  {json.dumps(finding, sort_keys=True, default=str)}")
    return 1 if discrepancy else 2 if not_checked else 0


async def _main(codes: list[str], check_all: bool) -> int:
    from app.db.session import AsyncSessionLocal, engine

    try:
        return await _run(codes, check_all, AsyncSessionLocal)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--equivalent", action="append", default=[], help="equivalent code; repeatable")
    target.add_argument("--all", action="store_true", help="every equivalent, active or not")
    args = parser.parse_args(argv)  # a usage error exits 2 here
    try:  # the application is imported here, so that a bad configuration is exit 2 as well, not a traceback's 1
        return asyncio.run(_main(args.equivalent, args.all))
    except Exception as error:  # noqa: BLE001 - whatever stopped the run, nothing was verified: exit 2, never 0
        message = re.sub(r"://[^@/\s]+@", "://***@", str(error))  # a URL in the message keeps no credentials
        print(f"the reconciliation did not run: {type(error).__name__}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
