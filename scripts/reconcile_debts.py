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
    2  the check did not happen, wholly or for some equivalent: usage, an empty or unknown code, no equivalent at all,
       the configuration or the database unusable, or the verifier erred - such an equivalent is printed as
       NOT CHECKED. A discrepancy found elsewhere in the same run still exits 1. (047 `T4704`: an equivalent whose
       stored FAILED this run tried and failed to re-confirm and hold is printed FAILED, marked so - exit 1.)

The verdict and the exit code are this run's. The details printed (`entries_read`, findings, `missing_evidence`) are
those of the latest STORED result row, named with its id, `checked_at` and `last_checked_at`: an unchanged verdict only
advances `last_checked_at` of the earlier row (`record_outcome`), and a scheduled run of the hub publishing at the same
moment may replace it. Only a difference of STATUS is flagged; another run's row with the same status is not told
apart. The Redis lock of the integrity loop is not taken.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _report_error(error: BaseException) -> str:
    message = re.sub(r"://[^@/\s]+@", "://***@", str(error))  # a URL in the message keeps no credentials
    return f"{type(error).__name__}: {message}"


async def _stored_result(session_factory, equivalent_id):
    """The equivalent's latest stored result row: id, status, checked_at, last_checked_at and its detail, or None."""

    from sqlalchemy import select

    from app.db.reconciliation_tables import debt_reconciliation_results

    results = debt_reconciliation_results.c
    async with session_factory() as session:
        row = (
            await session.execute(
                select(results.id, results.status, results.checked_at, results.last_checked_at, results.detail).where(
                    results.equivalent_id == equivalent_id, results.is_latest.is_(True)
                )
            )
        ).first()
    if row is None:
        return None
    detail = row.detail if isinstance(row.detail, dict) else json.loads(row.detail or "{}")
    return row, detail


def _hold_text(rec, counts) -> str:
    named = (
        (rec.HOLD_SET, "set"), (rec.HOLD_ALREADY_HELD, "already held"),
        (rec.HOLD_NOT_CONFIRMED, "not confirmed by the re-run"), (rec.HOLD_EQUIVALENT_GONE, "equivalent gone"),
    )
    return next((name for key, name in named if counts[f"hold_{key}"]),
                "ERROR (see the log)" if counts["hold_errors"] else "unknown")


async def _run(codes: list[str], check_all: bool, session_factory) -> int:
    """Verify, then report. The exit code is decided by THIS run's verdicts (`run_scheduled_reconciliation`'s counts),
    and a verdict once established is kept whatever fails afterwards: a failure to verify one equivalent, or to read
    the stored evidence of another, never turns a FAILED into "did not run"."""

    from sqlalchemy import select

    from app.core.ledger import reconciliation as rec
    from app.db.models import Equivalent

    if any(not code.strip() for code in codes):  # 047 (Argon P3): an empty code is refused, never silently dropped
        print("an empty equivalent code was given (--equivalent \"\"); nothing was checked", file=sys.stderr)
        return 2
    wanted = sorted({code.strip().upper() for code in codes})
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
        try:
            counts = await rec.run_scheduled_reconciliation(session_factory, equivalent_ids=[equivalent_id])
        except Exception as error:  # noqa: BLE001 - this equivalent was not verified; verdicts of the others stand
            not_checked = True
            print(f"{code}: NOT CHECKED - the reconciliation did not run for it: {_report_error(error)}")
            continue
        verdict = next((status for status in (rec.PASSED, rec.FAILED, rec.UNVERIFIABLE) if counts[status]), None)
        restated = verdict is None and bool(counts["hold_errors"])
        if restated:
            # 047 `T4704`: a hold error with no verdict happens only when the latest STORED result was FAILED and unheld,
            # so the run went straight to the reaction, and the reaction (its re-run or its hold) failed. That FAILED
            # stands - it is reported as FAILED, never as "did not run" - and is marked as not re-confirmed by this run.
            verdict = rec.FAILED
        if verdict is None:
            not_checked = True
            print(f"{code}: NOT CHECKED - the verifier erred (see the log); no result was recorded")
            continue
        discrepancy |= verdict != rec.PASSED
        line = f"{code}: {verdict}"
        if verdict == rec.FAILED:
            line += f" hold={_hold_text(rec, counts)}"
        if restated:
            line += " (the stored FAILED; this run's re-confirmation and hold did not complete)"
        try:
            stored = await _stored_result(session_factory, equivalent_id)
        except Exception as error:  # noqa: BLE001 - reporting only: the verdict above is this run's and stands
            print(f"{line} - verdict {verdict}; details could not be read: {_report_error(error)}")
            continue
        if stored is None:
            print(f"{line} - no stored result row was found to show its evidence")
            continue
        row, detail = stored
        # The evidence is the STORED transition row, not this run's: an unchanged verdict only advances its
        # `last_checked_at` (`record_outcome`), and a run publishing at the same moment may have replaced it.
        line += (
            f" - stored result {row.id} (status={row.status}, checked_at={row.checked_at.isoformat()}, "
            f"last_checked_at={row.last_checked_at.isoformat()}): entries_read={detail.get('entries_read')} "
            f"findings={detail.get('findings_total', 0)} missing_evidence={detail.get('missing_evidence') or []}"
        )
        if row.status != verdict:
            line += f" (the stored status differs from this run's {verdict})"
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
        print(f"the reconciliation did not run: {_report_error(error)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
