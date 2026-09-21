"""T1701: the provisioning preconditions REFUSE, and the refusal is not vacuous.

WHY THIS FILE EXISTS. Until 2026-09-21 four PostgreSQL modules answered a missing `CREATEDB` right
with `pytest.skip`, and each of them said in its own message that this was "an ABSENT measurement, not
a passing one" - while pytest reported it as a pass. `AGENTS.md` §9 calls that the standard way to
manufacture a false green, and §18 forbids counting a zero selection as success. With T1701 the right
became a precondition of the whole PostgreSQL tier (the schema template is provisioned by
`CREATE DATABASE`), so its absence is an environment fault and is raised as one.

WHAT THIS FILE CAN AND CANNOT SEE. It drives `assert_may_create_databases` through a stand-in for an
asyncpg connection, so it measures the decision and its message, NOT the SQL: whether
`rolsuper OR rolcreatedb` is the right question of a live server is measured by
`tests/integration/test_p017_t1701_schema_provisioning_postgres.py`, which runs the same function
against a real role that has the right and then provisions with it. Neither test alone is enough:
this one would pass against a query that always returns false, and that one would pass against a
function that never refuses.

ANTI-VACUUM (§9): every refusal below is paired with the case that must still be ACCEPTED, so a
function that refused everything - the easiest way to make a gate look strict - is red here.
"""

from __future__ import annotations

import pytest

from tests.migrated_schema import (
    MAX_IDENTIFIER_LENGTH,
    MigratedSchemaError,
    assert_may_create_databases,
    scratch_database_name,
    scratch_database_url,
)


class _RoleProbe:
    """The one asyncpg call `assert_may_create_databases` makes, with a scripted answer."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row
        self.queries: list[str] = []

    async def fetchrow(self, query: str, *args, **kwargs):
        self.queries.append(query)
        return self._row


@pytest.mark.asyncio
async def test_a_role_without_createdb_is_refused_and_the_message_says_what_to_do() -> None:
    """The refusal names the role, names the missing right, and names the command that grants it.

    MUTATION that must redden this: turn the raise in `assert_may_create_databases` back into
    `pytest.skip(...)`. The call then does not raise and the test fails.
    """

    probe = _RoleProbe({"role_name": "geo_reader", "may_create": False})

    with pytest.raises(MigratedSchemaError) as refusal:
        await assert_may_create_databases(probe)

    message = str(refusal.value)
    assert "geo_reader" in message
    assert "CREATEDB" in message
    assert "ALTER ROLE geo_reader CREATEDB" in message
    assert "not a passing run" in message
    # It asked the server rather than deciding on its own.
    assert probe.queries and "rolcreatedb" in probe.queries[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {"role_name": "geo", "may_create": True},  # CREATEDB or SUPERUSER: the tier's own case
    ],
)
async def test_a_role_with_the_right_is_accepted(row: dict[str, object]) -> None:
    """ANTI-VACUUM for the check above: a gate that refused every role would also be red here."""

    await assert_may_create_databases(_RoleProbe(row))


@pytest.mark.asyncio
async def test_a_role_that_is_not_in_pg_roles_is_refused_rather_than_assumed_capable() -> None:
    """No row is not the same as `may_create = true`; §9 forbids treating an absent answer as a pass."""

    with pytest.raises(MigratedSchemaError, match="no row in `pg_roles`"):
        await assert_may_create_databases(_RoleProbe(None))


def test_a_derived_name_that_would_be_truncated_is_refused_not_cut() -> None:
    """Two task slugs must not be able to land on one database.

    The helper this replaced ended in `[:63]`, so `geov0_test_<62 chars>_mig` and
    `geov0_test_<62 chars>_meta` were THE SAME DATABASE. Refusing is the only answer that keeps
    `AGENTS.md` §7 true.

    MUTATION that must redden this: restore the `[:63]` truncation in `scratch_database_name`.
    """

    base = "geov0_test_" + "a" * (MAX_IDENTIFIER_LENGTH - len("geov0_test_"))
    assert len(base) == MAX_IDENTIFIER_LENGTH

    with pytest.raises(MigratedSchemaError, match="truncat"):
        scratch_database_name(base, "mig")

    # ANTI-VACUUM: a name that fits is still produced, so this is not a rule that refuses everything.
    assert scratch_database_name("geov0_test_p017s1a", "mig") == "geov0_test_p017s1a__mig"


def test_derived_names_carry_the_task_slug_and_a_doubled_separator() -> None:
    """Isolation by slug (§7) is what the name is FOR, and the separator is what makes the sweep safe.

    `drop_stale_scratch_databases` deletes everything that starts with `<tier database>` plus the
    separator. With a single underscore, the tier `geov0_test_p017` would sweep the tier
    `geov0_test_p017_s1a` - another agent's database. The doubled underscore is the reason it cannot.
    """

    name = scratch_database_name("geov0_test_p017s1a", "tpl")
    assert name == "geov0_test_p017s1a__tpl"
    assert not scratch_database_name("geov0_test_p017", "tpl").startswith("geov0_test_p017s1a")


def test_a_suffix_that_is_not_a_plain_identifier_is_refused() -> None:
    """The name is interpolated into DDL, so its shape is checked where it is built."""

    for bad in ('mig"; DROP DATABASE geov0', "MiG", "_mig", "mig-1", ""):
        with pytest.raises(MigratedSchemaError):
            scratch_database_name("geov0_test_p017s1a", bad)


def test_provisioning_refuses_a_url_the_test_database_guard_would_reject() -> None:
    """The `geov0_test_*` guard is enforced on constructed names, never relaxed for provisioning.

    A developer database is the case that matters: `geov0` derives `geov0__mig`, which the guard
    rejects, and provisioning must reject it too rather than create it.
    """

    with pytest.raises(MigratedSchemaError):
        scratch_database_url("postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0", "mig")

    with pytest.raises(MigratedSchemaError, match="PostgreSQL"):
        scratch_database_url("sqlite+aiosqlite:///./.local-run/test-runs/x/test.db", "mig")


def test_a_geov0_test_url_is_accepted_and_keeps_its_password(monkeypatch) -> None:
    """ANTI-VACUUM for the refusals above, and the T1530 lesson about rendering URLs.

    `URL.__str__` writes the password as `***`; a URL rendered that way fails as "password
    authentication failed", a refusal that names the wrong problem.
    """

    monkeypatch.setenv("GEO_TEST_ALLOW_DB_RESET", "1")
    url, name = scratch_database_url(
        "postgresql+asyncpg://geo:secret@127.0.0.1:5432/geov0_test_p017s1a", "tpl"
    )
    assert name == "geov0_test_p017s1a__tpl"
    assert url.endswith("/geov0_test_p017s1a__tpl")
    assert "secret" in url and "***" not in url
