"""Take the debt reconciliation baseline - an explicit, deliberate cutover (programme 015, T1501).

RUN IT ON A QUIET SYSTEM: after seeding and before ordinary money operations on a fresh database; on an
upgrade, after the writers of the old envelope format have been stopped. Each equivalent is one
transaction under its owner lock. There is exactly one baseline per equivalent; a second attempt is
refused and nothing re-baselines.

A baseline makes later change to `debts` checkable against the journal. It DOES NOT CERTIFY the debts it
adopted: whatever the journal does not explain at this moment is recorded as an offset and taken as given.

    python scripts/take_reconciliation_baseline.py --equivalent UAH --equivalent HOUR
    python scripts/take_reconciliation_baseline.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.core.ledger.reconciliation import BaselineAlreadyTaken, take_baseline  # noqa: E402
from app.db.models import Equivalent  # noqa: E402
from app.db.reconciliation_tables import BASELINE_COMMENT  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402


async def _run(codes: list[str], take_all: bool) -> int:
    wanted = sorted({code.strip().upper() for code in codes if code.strip()})
    async with AsyncSessionLocal() as session:
        query = select(Equivalent.id, Equivalent.code).order_by(Equivalent.code)
        if not take_all:
            query = query.where(Equivalent.code.in_(wanted))
        rows = (await session.execute(query)).all()

    unknown = sorted(set(wanted) - {code for _, code in rows})
    if unknown:
        print(f"unknown equivalent code(s): {', '.join(unknown)}; nothing was taken", file=sys.stderr)
        return 2

    status = 0
    for equivalent_id, code in rows:
        async with AsyncSessionLocal() as session:
            try:
                taken = await take_baseline(session, equivalent_id)
                await session.commit()
            except BaselineAlreadyTaken as refusal:
                await session.rollback()
                if take_all:
                    # `--all` means "every equivalent that has no baseline yet", so it is idempotent and
                    # safe to run at the end of a local seed flow. An equivalent NAMED explicitly is a
                    # request for a new baseline, and refusing it is an error.
                    print(f"{code}: already has a baseline - skipped, nothing re-baselines")
                    continue
                print(f"{code}: refused - {refusal}")
                status = 1
                continue
        print(
            f"{code}: baseline taken - {taken.offsets_recorded} non-zero offset(s) over "
            f"{taken.edges_seen} edge(s), {taken.entries_read} journal entries read, "
            f"{taken.entry_arithmetic_contradictions} entry arithmetic contradiction(s)"
        )
    print(BASELINE_COMMENT)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--equivalent", action="append", default=[], help="equivalent code; repeatable")
    target.add_argument("--all", action="store_true", help="every equivalent that has no baseline yet")
    args = parser.parse_args()
    return asyncio.run(_run(args.equivalent, args.all))


if __name__ == "__main__":
    raise SystemExit(main())
