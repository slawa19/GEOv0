"""Mode B for a module that reaches the database through `tests.conftest`'s names (017 stage 2c, T1702).

NOT a test module. Import `tier_sessions_on_a_clone` into a test module and it becomes an AUTOUSE fixture
of that module, and of no other.

WHY IT EXISTS. `MODE_B` (in `tests/conftest.py`) moves `db_session` and `client` onto a disposable
clone. A family of modules written for the old PostgreSQL marker tier never used either: they commit
through `TestingSessionLocal()` and through a `factory` engine of their own, looking both up INSIDE
their functions (`from tests.conftest import TestingSessionLocal`), and clean up afterwards by deleting
the rows they believe they created. Their commits land in the TIER database. While that tier ran only
marker tests, nothing read what the cleanup missed.

Stage 2c put both tiers in one process, and the first run of it measured what the cleanup misses:
`audit_log`. A login writes `auth.login`, an admin action writes its audit row, and none of the modules
deletes them. `tests/unit/test_admin_audit_log_list.py::test_admin_audit_log_pagination_and_q_search`,
a mode-A test that counts every audit row, then read `35 == 3`. Each of the five polluters was
confirmed in a pair with that victim in one process (the victim alone passes).

WHAT THIS DOES. It asks for `committed_database` - a clone of the migrated template, dropped after the
test - and, for the duration of the test, rebinds `tests.conftest.TestingSessionLocal` to the clone's
sessionmaker. Every in-function lookup of that name then reaches the clone, so the module's commits,
its observers (`pg_locks ... current_database()`) and its cleanup all talk to ONE database, and that
database is gone when the test ends. Nothing the cleanup misses can outlive the test.

The rebinding is scoped by `monkeypatch`, so it is undone before the next test; and it is autouse
only where imported, so no other module sees it. `TEST_DATABASE_URL` is deliberately NOT rebound:
scratch-database helpers derive their names from it, and a clone's name already carries the reserved
`__` separator. A module that builds its own engine takes the clone's URL from `committed_database`
instead (see `factory` in `test_p015_p1_money_replay_postgres.py`).

WHAT IT DOES NOT DO: it does not reach a name bound at import time (`from tests.conftest import
TestingSessionLocal` at module level) - every module that imports this was checked to have none - and
it does not move `db_session`, which is still mode A on the tier. Nor does it rebind
`tests.conftest.engine`: a module that needs an engine over the clone asks for `committed_database`
and uses `committed_database.engine` or `.url` by name, so a reader sees which database each engine,
observer and child process talks to. (Rebinding the tier engine would also move the mode-A `db_session`
and, on the first test of a run, `_ensure_schema_initialized` onto the clone.)

TWO SPELLINGS, ONE REBINDING (018 B0b). Importing `tier_sessions_on_a_clone` makes the rebinding
autouse for the whole module. Importing `tier_on_a_clone` instead makes it an ordinary fixture that a
test opts into with `@pytest.mark.usefixtures("tier_on_a_clone")`: for a module whose other tests
write nothing that outlives them (mode A, or no committed rows) and should not pay for a clone each.
The two are independent fixtures over one helper rather than one depending on the other, because a
fixture's dependency must be visible by name in the requesting module and only the imported name is.

WHY THE ROW-BY-ROW CLEANUPS WENT (018 B0b). Until B0b the family deleted what it committed, journal
rows included (`purge_test_ledger`). Stage B1 puts the journal in the database and REFUSES deletes of
its rows, so a cleanup of that shape cannot survive it; on a clone it is also redundant, because the
drop takes every row with it. Never mix this with `MODE_B` in one test: both clone under the same name
(`_MODE_B_CLONE_SUFFIX`), and the second copy would drop the first.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest_asyncio


def _rebind_the_tier_sessions(committed_database, monkeypatch) -> None:
    import tests.conftest as tier

    monkeypatch.setattr(tier, "TestingSessionLocal", committed_database.sessionmaker)


@pytest_asyncio.fixture(autouse=True)
async def tier_sessions_on_a_clone(committed_database, monkeypatch) -> AsyncGenerator[object, None]:
    _rebind_the_tier_sessions(committed_database, monkeypatch)
    yield committed_database


@pytest_asyncio.fixture
async def tier_on_a_clone(committed_database, monkeypatch) -> AsyncGenerator[object, None]:
    """The same rebinding for one test, opted into with `@pytest.mark.usefixtures("tier_on_a_clone")`."""

    _rebind_the_tier_sessions(committed_database, monkeypatch)
    yield committed_database
