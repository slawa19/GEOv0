"""T1701: the provisioning preconditions REFUSE, and the refusal is not vacuous.

WHY THIS FILE EXISTS. Until 2026-09-21 four PostgreSQL modules answered a missing `CREATEDB` right
with `pytest.skip`, and each of them said in its own message that this was "an ABSENT measurement, not
a passing one" - while pytest reported it as a pass. `AGENTS.md` §9 calls that the standard way to
manufacture a false green, and §18 forbids counting a zero selection as success. With T1701 the right
became a precondition of the whole PostgreSQL tier (the schema template is provisioned by
`CREATE DATABASE`), so its absence is an environment fault and is raised as one.

AND UNTIL 2026-09-22 THIS FILE DID NOT DETECT THE MUTATION IT ADVERTISED. Every refusal below was
written `with pytest.raises(MigratedSchemaError)`, and the documented mutation - turn the raise back
into `pytest.skip(...)` - raises `_pytest.outcomes.Skipped`, which derives from **BaseException**, not
from `Exception`. `pytest.raises` does not convert it into a failure: it travels straight through the
`with` block and the test reports SKIPPED. Skips do not fail this runner, so the guard against a
false green was itself a false green (Codex external review of `e2e1380..37fec08`). `_refusal_from`
below is the repair: it names the one exception type it will accept and fails on everything else,
including `Skipped`, and `test_the_refusal_helper_is_not_fooled_by_a_skip` is the counter-check that
the repair works - with the unmutated call as its control, so a helper that failed on everything
would be red there too.

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

import inspect
from pathlib import Path

import pytest

from scripts.validate_test_database_url import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)
from tests.migrated_schema import (
    MAX_IDENTIFIER_LENGTH,
    REPO_ROOT,
    SCRATCH_SEPARATOR,
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


async def _refusal_from(call) -> MigratedSchemaError:
    """Run `call` and insist that it refused with `MigratedSchemaError`; return the refusal.

    DELIBERATELY NOT `pytest.raises`. `pytest.skip()` and `pytest.fail()` raise subclasses of
    `BaseException`, which `pytest.raises(MigratedSchemaError)` lets through untouched - the test
    then reports skipped or dies with the wrong reason, and a mutation that replaces a refusal with a
    skip goes unnoticed. That mutation is the one every "MUTATION that must redden this" note in
    this file names, so the helper has to see it.

    Takes a zero-argument callable rather than a coroutine so the counter-check below can hand it a
    stand-in without building the real one first.
    """

    try:
        result = call()
        if inspect.isawaitable(result):
            await result
    except MigratedSchemaError as refusal:
        return refusal
    except BaseException as escaped:  # noqa: BLE001 - the point is to catch what raises() does not
        pytest.fail(
            f"the precondition raised {type(escaped).__name__} instead of MigratedSchemaError: "
            f"{escaped!r}. A skip, a pass-through or any other outcome is an ABSENT measurement "
            f"reported as a result (AGENTS.md §9)."
        )
    pytest.fail(
        "the precondition returned without refusing, so a missing precondition would be reported "
        "as a passing run."
    )


@pytest.mark.asyncio
async def test_the_refusal_helper_is_not_fooled_by_a_skip() -> None:
    """COUNTER-CHECK for the instrument itself, with the unmutated case as its control.

    Control first: a real refusal is still accepted and handed back, so what follows is evidence
    about `pytest.skip`, not about a helper that fails on everything.
    """

    async def _really_refuses() -> None:
        raise MigratedSchemaError("geo_reader has neither SUPERUSER nor CREATEDB")

    control = await _refusal_from(_really_refuses)
    assert "CREATEDB" in str(control)

    async def _skips_instead() -> None:
        pytest.skip("the role cannot create databases; this measurement was not taken")

    with pytest.raises(pytest.fail.Exception, match="Skipped"):
        await _refusal_from(_skips_instead)

    async def _returns_quietly() -> None:
        return None

    with pytest.raises(pytest.fail.Exception, match="without refusing"):
        await _refusal_from(_returns_quietly)


@pytest.mark.asyncio
async def test_a_role_without_createdb_is_refused_and_the_message_says_what_to_do() -> None:
    """The refusal names the role, names the missing right, and names the command that grants it.

    MUTATION that must redden this: turn the raise in `assert_may_create_databases` back into
    `pytest.skip(...)`. `_refusal_from` reports that as a failure; `pytest.raises` did not.
    """

    probe = _RoleProbe({"role_name": "geo_reader", "may_create": False})

    message = str(await _refusal_from(lambda: assert_may_create_databases(probe)))

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

    message = str(await _refusal_from(lambda: assert_may_create_databases(_RoleProbe(None))))
    assert "no row in `pg_roles`" in message


@pytest.mark.asyncio
async def test_a_derived_name_that_would_be_truncated_is_refused_not_cut() -> None:
    """Two task slugs must not be able to land on one database.

    The helper this replaced ended in `[:63]`, so `geov0_test_<62 chars>_mig` and
    `geov0_test_<62 chars>_meta` were THE SAME DATABASE. Refusing is the only answer that keeps
    `AGENTS.md` §7 true.

    MUTATION that must redden this: restore the `[:63]` truncation in `scratch_database_name`.
    """

    base = "geov0_test_" + "a" * (MAX_IDENTIFIER_LENGTH - len("geov0_test_"))
    assert len(base) == MAX_IDENTIFIER_LENGTH

    message = str(await _refusal_from(lambda: scratch_database_name(base, "mig")))
    assert "truncat" in message

    # ANTI-VACUUM: a name that fits is still produced, so this is not a rule that refuses everything.
    assert scratch_database_name("geov0_test_p017s1a", "mig") == "geov0_test_p017s1a__mig"


@pytest.mark.asyncio
async def test_derived_names_carry_the_task_slug_and_the_separator_is_reserved() -> None:
    """Isolation by slug (§7), and where ownership of a swept name actually comes from.

    THE PREVIOUS VERSION OF THIS TEST RECORDED A FALSE REASON, and the false reason is why the
    defect survived review (`AGENTS.md` §15: an unsound premise is worse than a missing one). It
    said the doubled underscore made `drop_stale_scratch_databases` safe because a neighbouring slug
    (`geov0_test_p017` beside `geov0_test_p017_s1a`) is matched only by a SINGLE-underscore prefix.
    That is true and insufficient: `p017s1a__probe` is an equally valid task slug, its tier database
    is `geov0_test_p017s1a__probe`, and the sweep run for the task `p017s1a` dropped it. Reproduced
    on PostgreSQL 16 with two disposable databases, 2026-09-22.

    What makes the sweep sound is that the separator is RESERVED: a tier database name may not
    contain it, so a `<tier>__<rest>` name can only be a scratch database of `<tier>`. Both ends of
    that rule are tested - the guard below, and `scratch_database_name` here.
    """

    name = scratch_database_name("geov0_test_p017s1a", "tpl")
    assert name == "geov0_test_p017s1a__tpl"
    assert not scratch_database_name("geov0_test_p017", "tpl").startswith("geov0_test_p017s1a")

    # A tier name carrying the separator is ambiguous with a neighbour's scratch database.
    ambiguous = f"geov0_test_p017s1a{SCRATCH_SEPARATOR}probe"
    message = str(await _refusal_from(lambda: scratch_database_name(ambiguous, "tpl")))
    assert SCRATCH_SEPARATOR in message and "reserved" in message

    # And a suffix may not smuggle one back in.
    smuggled = str(await _refusal_from(lambda: scratch_database_name("geov0_test_p017s1a", "a__b")))
    assert "doubled underscore" in smuggled


def test_the_url_guard_refuses_a_tier_database_that_could_be_a_neighbours_scratch() -> None:
    """The other end of the reserved separator, where a run's own `TEST_DATABASE_URL` arrives.

    This is the check that turns "the sweep only reaches this task" from an assumption into a fact:
    if no tier database may be named `geov0_test_<x>__<y>`, then every such database on the server
    was derived by provisioning from the tier `geov0_test_<x>`.
    """

    def _check(database: str, **kwargs):
        return assert_safe_test_database_url(
            f"postgresql+asyncpg://geo:secret@127.0.0.1:5432/{database}",
            allow_destructive_reset="1",
            repo_root=REPO_ROOT,
            required_backend="postgresql",
            **kwargs,
        )

    # ANTI-VACUUM, and the control for the refusal below: ordinary tier names are still accepted,
    # single underscores included, so this is not a rule that rejects everything.
    assert _check("geov0_test_p017fix").database == "geov0_test_p017fix"
    assert _check("geov0_test_agent_payments_review").database is not None
    assert _check("geov0_test_ci-postgres-phase2").database is not None

    with pytest.raises(UnsafeTestDatabaseError, match="doubled underscore"):
        _check("geov0_test_p017s1a__probe")

    # Provisioning is the one caller that derives such a name on purpose, and it says so.
    assert (
        _check("geov0_test_p017s1a__probe", allow_scratch_suffix=True).database
        == "geov0_test_p017s1a__probe"
    )
    # Even then the name may carry the separator exactly once: `__a__b` is ambiguous again.
    with pytest.raises(UnsafeTestDatabaseError):
        _check("geov0_test_p017s1a__a__b", allow_scratch_suffix=True)


@pytest.mark.asyncio
async def test_a_suffix_that_is_not_a_plain_identifier_is_refused() -> None:
    """The name is interpolated into DDL, so its shape is checked where it is built."""

    for bad in ('mig"; DROP DATABASE geov0', "MiG", "_mig", "mig-1", ""):
        await _refusal_from(lambda bad=bad: scratch_database_name("geov0_test_p017s1a", bad))


@pytest.mark.asyncio
async def test_provisioning_refuses_a_url_the_test_database_guard_would_reject() -> None:
    """The `geov0_test_*` guard is enforced on constructed names, never relaxed for provisioning.

    A developer database is the case that matters: `geov0` derives `geov0__mig`, which the guard
    rejects, and provisioning must reject it too rather than create it.
    """

    await _refusal_from(
        lambda: scratch_database_url("postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0", "mig")
    )
    backend = str(
        await _refusal_from(
            lambda: scratch_database_url("sqlite+aiosqlite:///./.local-run/test-runs/x/test.db", "mig")
        )
    )
    assert "PostgreSQL" in backend


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
    assert isinstance(REPO_ROOT, Path)
