import argparse
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _one(cur: sqlite3.Cursor, q: str, *p):
    cur.execute(q, p)
    row = cur.fetchone()
    return row[0] if row else None


def _query_map(cur: sqlite3.Cursor, q: str, *p) -> dict[tuple[str, str], Decimal]:
    cur.execute(q, p)
    out: dict[tuple[str, str], Decimal] = {}
    for creditor_pid, debtor_pid, amount in cur.fetchall():
        out[(str(creditor_pid), str(debtor_pid))] = _d(amount)
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Compare real-mode graph snapshot link.used with debts in SQLite DB"
    )
    p.add_argument("--api", default="http://127.0.0.1:18000", help="Backend base URL")
    p.add_argument("--token", default="dev-admin-token-change-me", help="X-Admin-Token")
    p.add_argument(
        "--scenario",
        default="greenfield-village-100-realistic-v2",
        help="Scenario id",
    )
    p.add_argument("--equivalent", default="UAH", help="Equivalent code")
    p.add_argument("--run-id", default=None, help="Existing run id (optional)")
    p.add_argument(
        "--db",
        default="geov0.db",
        help="Path to SQLite DB file (default: geov0.db)",
    )
    p.add_argument(
        "--eps",
        default="0.01",
        help="Allowed absolute difference for used amount (default: 0.01)",
    )
    args = p.parse_args()

    api = str(args.api).rstrip("/")
    eq = str(args.equivalent).strip().upper()
    eps = _d(args.eps)

    headers = {"X-Admin-Token": str(args.token)}

    with httpx.Client(timeout=10.0, headers=headers) as client:
        run_id = args.run_id
        if not run_id:
            r = client.post(
                f"{api}/api/v1/simulator/runs",
                json={"scenario_id": args.scenario, "mode": "real", "intensity_percent": 0},
            )
            r.raise_for_status()
            run_id = r.json()["run_id"]

        snap = client.get(
            f"{api}/api/v1/simulator/runs/{run_id}/graph/snapshot",
            params={"equivalent": eq},
        )
        snap.raise_for_status()
        snapshot = snap.json()

    links = snapshot.get("links") or []
    snap_used: dict[tuple[str, str], Decimal] = {}
    for l in links:
        src = str(l.get("source") or "").strip()
        dst = str(l.get("target") or "").strip()
        if not src or not dst:
            continue
        used = _d(l.get("used") or "0")
        snap_used[(src, dst)] = used

    con = sqlite3.connect(args.db)
    try:
        cur = con.cursor()
        eq_id = _one(cur, "select id from equivalents where code = ?", eq)
        if not eq_id:
            raise SystemExit(f"Equivalent not found in DB: {eq}")

        # TrustLine direction: from (creditor) -> to (debtor)
        # Debt direction: debtor -> creditor
        db_used = _query_map(
            cur,
            """
select pc.pid as creditor_pid, pd.pid as debtor_pid, d.amount
from debts d
join participants pd on pd.id = d.debtor_id
join participants pc on pc.id = d.creditor_id
where d.equivalent_id = ?
""",
            eq_id,
        )

        mismatches: list[tuple[str, str, Decimal, Decimal]] = []
        for (creditor_pid, debtor_pid), expected in db_used.items():
            actual = snap_used.get((creditor_pid, debtor_pid))
            if actual is None:
                # Snapshot only includes scenario edges; missing edge is worth surfacing.
                mismatches.append((creditor_pid, debtor_pid, expected, Decimal("NaN")))
                continue
            if (actual - expected).copy_abs() > eps:
                mismatches.append((creditor_pid, debtor_pid, expected, actual))

        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "equivalent": eq,
                    "snapshot_links": len(snap_used),
                    "db_debts": len(db_used),
                    "mismatches": len(mismatches),
                },
                ensure_ascii=False,
            )
        )

        if mismatches:
            print("Top mismatches (creditor->debtor):")
            for c, d, exp, act in mismatches[:10]:
                print(f"  {c} -> {d}: db={exp} snapshot={act}")
            raise SystemExit(2)

    finally:
        con.close()


if __name__ == "__main__":
    main()
