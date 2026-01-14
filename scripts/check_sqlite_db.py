import argparse
import sqlite3


def _one(cur: sqlite3.Cursor, q: str, *p):
    cur.execute(q, p)
    row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    p = argparse.ArgumentParser(description="Sanity-check a GEOv0 SQLite database")
    p.add_argument("--db", default="geov0.db", help="Path to SQLite DB file (default: geov0.db)")
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    print("DB:", args.db)

    print("participants:", _one(cur, "select count(*) from participants"))
    print(
        "participants with (Test):",
        _one(cur, "select count(*) from participants where display_name like '%(Test)%'"),
    )
    print("equivalents:", _one(cur, "select count(*) from equivalents"))
    print("trustlines:", _one(cur, "select count(*) from trust_lines"))
    print("debts:", _one(cur, "select count(*) from debts"))
    print("transactions:", _one(cur, "select count(*) from transactions"))
    print("audit_log:", _one(cur, "select count(*) from audit_log"))

    cur.execute("select code from equivalents order by code")
    print("equivalent codes:", [r[0] for r in cur.fetchall()])

    print(
        "trustlines self-loop:",
        _one(cur, "select count(*) from trust_lines tl where tl.from_participant_id = tl.to_participant_id"),
    )
    # "limit" is a reserved keyword in SQL; must be quoted.
    print(
        "trustlines negative limit:",
        _one(cur, 'select count(*) from trust_lines tl where tl."limit" < 0'),
    )

    cur.execute("select type, count(*) from participants group by type order by type")
    print("participant types:", dict(cur.fetchall()))
    cur.execute("select status, count(*) from participants group by status order by status")
    print("participant statuses:", dict(cur.fetchall()))

    print("debts <= 0:", _one(cur, "select count(*) from debts d where d.amount <= 0"))
    print(
        "debts with debtor=creditor:",
        _one(cur, "select count(*) from debts d where d.debtor_id = d.creditor_id"),
    )

    # TrustLine direction: from (creditor) -> to (debtor)
    # Debt direction: debtor -> creditor
    print(
        "debts missing trustline edge:",
        _one(
            cur,
            """
select count(*)
from debts d
left join trust_lines tl
  on tl.from_participant_id = d.creditor_id
 and tl.to_participant_id = d.debtor_id
 and tl.equivalent_id = d.equivalent_id
where tl.id is null
""",
        ),
    )
    print(
        "debt > trustline.limit violations:",
        _one(
            cur,
            """
select count(*)
from debts d
join trust_lines tl
  on tl.from_participant_id = d.creditor_id
 and tl.to_participant_id = d.debtor_id
 and tl.equivalent_id = d.equivalent_id
where d.amount > tl."limit" + 1e-9
""",
        ),
    )

    con.close()


if __name__ == "__main__":
    main()
