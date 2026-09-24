"""THE named corruption helper of programme 018 (spec 018 §2, `FORK-4`). NOT a test module.

WHY IT EXISTS. After stage B1 the database refuses every write to `debts` outside an operation and
every rewrite of the journal. The detection chain of programme 015 - an atom moved around the
application, a missing or doubled entry, a contradictory row, all ending in `FAILED` and a hold - must
keep its independent evidence (spec: "Замена всех тестов «порча → FAILED → удержание» тестами отказа
записи" is forbidden). This helper models the one real way such states arise: an operator, a restore,
or anything else that writes with the triggers switched off.

WHAT IT DOES, and nothing else:

* only on a DISPOSABLE database - a scratch clone of this tier (`<tier>__<suffix>`), never the tier
  database itself nor anything else; refused before connecting otherwise;
* on its OWN connection, in its OWN transaction, with `SET LOCAL session_replication_role = replica`:
  the setting ends with that transaction and never reaches another connection or a pool;
* `corrupt(...)` COMMITS its statements (a corruption the independent readers must see);
  `probe(...)` runs ONE statement and ROLLS BACK, returning the SQLSTATE that refused it (CHECK probes).

THE PRIVILEGE, checked before use with a clear refusal - never a skip. `session_replication_role` is
superuser-context: a role needs SUPERUSER, or (PostgreSQL 15+) `GRANT SET ON PARAMETER
session_replication_role TO <role>`, in addition to the `CREATEDB` the tier already requires. The CI
service (`postgres:16`, `POSTGRES_USER`) and the local portable server give a superuser. The
production role's rights are not changed by anything here.

WHAT `replica` ALSO SWITCHES OFF, and a caller must know it: foreign-key (RI) triggers and the
deferred completion check. A corruption through this helper is not FK-checked; CHECK constraints
still apply (they are not triggers), which is why the contradictory-arithmetic form needs the
CHECK-less clone as well.

NAMED USES (spec §2, §4, and manifest `T1808` section 3 items 4-5): the `T1508` corruption forms of
reconciliation and hold tests; the recipe doctorings of `test_p017_t1711_seed_recipe_postgres.py`;
CHECK probes of the journal tables; the `T1533` "debt without history" preparation. No application
code imports this; no flag in `app/` bypasses the triggers.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

__all__ = ["CorruptionHelperError", "corrupt", "probe", "require_the_privilege"]

_REPLICA = "SET LOCAL session_replication_role = replica"


class CorruptionHelperError(RuntimeError):
    """The helper refused: not a disposable database, or the role lacks the privilege."""


def _require_disposable(url: str) -> None:
    from tests.conftest import TEST_DATABASE_URL
    from tests.migrated_schema import SCRATCH_SEPARATOR

    tier = make_url(TEST_DATABASE_URL).database or ""
    target = make_url(url).database or ""
    if not tier or not target.startswith(tier + SCRATCH_SEPARATOR):
        raise CorruptionHelperError(
            f"the corruption helper writes with the journal's triggers switched off, so it runs only "
            f"on a disposable clone of this tier ({tier}{SCRATCH_SEPARATOR}<suffix>); got {target!r}"
        )


async def require_the_privilege(connection: Any) -> None:
    """Refuse unless the connected role may set `session_replication_role`. A precondition, not a skip."""

    row = (
        await connection.exec_driver_sql(
            "SELECT current_user, "
            "has_parameter_privilege(current_user, 'session_replication_role', 'SET')"
        )
    ).one()
    if not row[1]:
        raise CorruptionHelperError(
            f"the role {row[0]!r} may not set session_replication_role, so the corruption helper "
            f"cannot model a write with the triggers off. The test role needs CREATEDB AND either "
            f"SUPERUSER or `GRANT SET ON PARAMETER session_replication_role TO {row[0]};` "
            f"(PostgreSQL 15+). This is a missing precondition of the tier, not a passing run."
        )


async def corrupt(url: str, statements: Sequence[str]) -> None:
    """Run `statements` with the triggers off, in one transaction, and COMMIT."""

    _require_disposable(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await require_the_privilege(connection)
            await connection.rollback()
            async with connection.begin():
                await connection.exec_driver_sql(_REPLICA)
                for statement in statements:
                    await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def probe(url: str, statement: str) -> str | None:
    """Run ONE statement with the triggers off and ROLL BACK: the SQLSTATE that refused it, or None."""

    _require_disposable(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await require_the_privilege(connection)
            await connection.rollback()
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql(_REPLICA)
                try:
                    await connection.exec_driver_sql(statement)
                except DBAPIError as exc:
                    orig = getattr(exc, "orig", None)
                    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
                return None
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
