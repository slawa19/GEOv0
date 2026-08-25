"""Why RT-012-1 is gated on PostgreSQL: SQLite does not have the behaviour under test.

Section 4 of the 012 verification plan forbids counting SQLite towards the `F-012-1` gate, and
makes the reason a mandatory check in its own right: *if SQLite behaves differently, the whole
class is invisible to the default tier, and that has to be written down.* This module is that
check, written as a measurement instead of a sentence, and it lives in the default tier on
purpose - it is the tier the claim is about.

WHAT WAS MEASURED (2026-08-24, HEAD e45e721). The same SQLAlchemy `Numeric(20, 8)` column,
declared from the same metadata, on `sqlite+aiosqlite` and on PostgreSQL 16.9:

| submitted              | PostgreSQL `amount::text` | SQLite raw column | SQLite via SQLAlchemy |
|---|---|---|---|
| `0.12345678`           | `0.12345678`  | `0.12345678`          | `Decimal('0.12345678')` |
| `0.123456789`          | `0.12345679`  | `0.123456789`         | `Decimal('0.12345679')` |
| `0.123456785`          | `0.12345679`  | `0.123456785`         | `Decimal('0.12345678')` |
| `0.000000001`          | `0.00000000`  | `1e-09`               | `Decimal('0E-8')`       |
| `0.123456789012345678` | `0.12345679`  | `0.12345678901234568` | `Decimal('0.12345679')` |

Three consequences, and each of them alone is enough to disqualify a SQLite run as evidence:

1. **SQLite does not round on write.** `NUMERIC(20,8)` there is type affinity, not a constraint;
   the column holds an IEEE-754 double carrying every digit that was submitted (and, at
   `0.123456789012345678`, digits that were not). The rounding a test would observe happens
   afterwards, client-side, in SQLAlchemy's decimal result processor. So a SQLite test would be
   measuring the ORM, and would keep passing if PostgreSQL's storage behaviour changed.
2. **The two backends disagree on the tie.** `0.123456785` becomes `0.12345679` on PostgreSQL and
   `0.12345678` on SQLite - opposite directions, because one is decimal half-up on an exact value
   and the other is a float that landed just below the midpoint.
3. **The constraint fires on one backend and not the other.** `0.000000001` rounds to exactly `0`
   in PostgreSQL, so `chk_debt_amount_positive` (`app/db/models/debt.py:27`) raises and the API
   answers HTTP 500. On SQLite the row holds `1e-09`, which is `> 0`, so the constraint is
   satisfied and no failure of any kind is produced.

The reproducer this supports is
`tests/integration/test_p012_rt1_signed_amount_versus_stored_amount_postgres.py`.

This module asserts SQLite's half of the table only - it builds its own in-memory engine rather
than using `TEST_DATABASE_URL`, so it runs and means the same thing in either tier. The
PostgreSQL half is asserted in the reproducer itself, against the live server.
"""

from __future__ import annotations

import uuid
import warnings
from decimal import Decimal

from sqlalchemy import Column, MetaData, Numeric, Table, Uuid, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

_METADATA = MetaData()

# Declared exactly as Debt.amount and TrustLine.limit are (app/db/models/debt.py:14,
# app/db/models/trustline.py:14), so the comparison is about the backend and nothing else.
_PROBE = Table(
    "p012_numeric_probe",
    _METADATA,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("amount", Numeric(20, 8), nullable=False),
)

# submitted -> (what PostgreSQL 16.9 stores, what the raw SQLite column keeps)
MEASURED = [
    ("0.12345678", "0.12345678", 0.12345678),
    ("0.123456789", "0.12345679", 0.123456789),
    ("0.123456785", "0.12345679", 0.123456785),
    ("0.000000001", "0.00000000", 1e-09),
    ("0.123456789012345678", "0.12345679", 0.12345678901234568),
]


async def test_sqlite_does_not_apply_the_declared_scale_so_the_default_tier_cannot_see_f_012_1() -> None:
    """The raw SQLite column keeps digits `NUMERIC(20,8)` on PostgreSQL discards."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_METADATA.create_all)

            ids: dict[str, uuid.UUID] = {}
            for submitted, _pg, _raw in MEASURED:
                row_id = uuid.uuid4()
                ids[submitted] = row_id
                # SQLAlchemy warns that sqlite handles Decimal via float; that warning is the
                # finding, not an accident, so it is silenced rather than allowed to fail a run.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    await conn.execute(
                        insert(_PROBE).values(id=row_id, amount=Decimal(submitted))
                    )

            for submitted, pg_stored, raw_expected in MEASURED:
                raw = (
                    await conn.execute(
                        text("SELECT amount FROM p012_numeric_probe WHERE id = :i"),
                        {"i": ids[submitted].hex},
                    )
                ).scalar_one()
                assert raw == raw_expected, (
                    f"SQLite's raw storage for {submitted!r} is {raw!r}, not the measured "
                    f"{raw_expected!r}. This module's premise - that SQLite stores a float and "
                    f"never applies the declared scale - has changed, and the claim that the "
                    f"default tier cannot see F-012-1 must be re-established before RT-012-1's "
                    f"postgres gate is justified by it."
                )
                if Decimal(submitted) != Decimal(pg_stored):
                    # PostgreSQL lost these digits at INSERT. SQLite still has them.
                    assert Decimal(repr(raw)) != Decimal(pg_stored), (
                        f"for {submitted!r} the raw SQLite column now holds exactly what "
                        f"PostgreSQL stores ({pg_stored!r}). If the backends have converged, the "
                        f"postgres marker on RT-012-1 is no longer load-bearing and must be "
                        f"re-argued rather than kept out of habit."
                    )

            # (3) the constraint that turns the vanishing case into an error on PostgreSQL.
            vanishing = (
                await conn.execute(
                    text("SELECT amount > 0 FROM p012_numeric_probe WHERE id = :i"),
                    {"i": ids["0.000000001"].hex},
                )
            ).scalar_one()
            assert bool(vanishing) is True, (
                "on SQLite 0.000000001 must still satisfy `amount > 0`, i.e. "
                "chk_debt_amount_positive (app/db/models/debt.py:27) does not fire. It does fire "
                "on PostgreSQL, where the value rounds to exactly 0, and the payment escapes as "
                "HTTP 500 E010. If this ever fails, the two backends have stopped disagreeing "
                "about the vanishing case and RT-012-1's third parameter would become "
                "reproducible on the default tier."
            )

            # (1) the rounding a SQLite test would 'see' is the ORM's, applied on read.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                via_orm = (
                    await conn.execute(
                        select(_PROBE.c.amount).where(_PROBE.c.id == ids["0.123456785"])
                    )
                ).scalar_one()
            assert via_orm == Decimal("0.12345678"), (
                f"SQLAlchemy's sqlite result processor returned {via_orm!r} for the tie case "
                f"0.123456785; it was measured as Decimal('0.12345678'), while PostgreSQL stores "
                f"0.12345679. A test that asserted a rounded value on SQLite would therefore be "
                f"asserting the opposite rounding direction from production."
            )
    finally:
        await engine.dispose()
