"""T1702: the tier creates its missing database only with `CREATEDB`, and refuses rather than skips.

`tests/integration/test_p017_t1702_mode_b_fixture_postgres.py` measures creation against a live
server whose role HAS the right; a server without it is not available to this repository's gates, so
the refusal is measured here through a stand-in for the maintenance connection. What this file sees
is the decision and the statements sent, not whether the server would have accepted them.

ANTI-VACUUM (§9): the refusal is paired with the accepted case and with the existing-database case,
so a function that refused everything, or created every time, is red here.

`_outcome` and not `pytest.raises`: a mutation that turned the refusal into `pytest.skip` raises a
`BaseException` that `pytest.raises` lets through as a skip (the lesson of
`test_p017_t1701_provisioning_refuses_rather_than_skips.py`).
"""

from __future__ import annotations

import pytest

from tests import migrated_schema
from tests.migrated_schema import MigratedSchemaError, ensure_tier_database

_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_t1702unit"


class _Maintenance:
    def __init__(self, *, exists: bool, may_create: bool) -> None:
        self._exists = exists
        self._may_create = may_create
        self.executed: list[str] = []
        self.role_probed = False
        self.closed = False

    async def fetchval(self, query: str, *args, **kwargs):
        assert "pg_database" in query
        return 1 if self._exists else None

    async def fetchrow(self, query: str, *args, **kwargs):
        self.role_probed = True
        return {"role_name": "geo", "may_create": self._may_create}

    async def execute(self, statement: str, *args, **kwargs):
        self.executed.append(statement)

    async def close(self):
        self.closed = True


async def _outcome(call):
    try:
        return await call(), None
    except MigratedSchemaError as exc:
        return None, exc
    except BaseException as exc:  # noqa: BLE001 - anything else, a skip included, is the defect
        pytest.fail(f"expected a MigratedSchemaError or a result, got {type(exc).__name__}: {exc}")


@pytest.fixture
def maintenance(monkeypatch):
    monkeypatch.setenv("GEO_TEST_ALLOW_DB_RESET", "1")
    holder: dict[str, _Maintenance] = {}

    def install(**kwargs) -> _Maintenance:
        fake = _Maintenance(**kwargs)
        holder["fake"] = fake

        async def _connect(base_url):
            return fake

        monkeypatch.setattr(migrated_schema, "maintenance_connection", _connect)
        return fake

    return install


async def test_a_missing_database_without_createdb_is_refused_and_nothing_is_created(maintenance):
    fake = maintenance(exists=False, may_create=False)
    result, refusal = await _outcome(lambda: ensure_tier_database(_URL))
    assert refusal is not None, f"no refusal; returned {result!r}"
    assert "CREATEDB" in str(refusal)
    assert fake.executed == []
    assert fake.closed


async def test_a_missing_database_with_createdb_is_created(maintenance):
    fake = maintenance(exists=False, may_create=True)
    result, refusal = await _outcome(lambda: ensure_tier_database(_URL))
    assert refusal is None, refusal
    assert result is True
    assert fake.executed == ['CREATE DATABASE "geov0_test_t1702unit"']


async def test_an_existing_database_is_left_alone_and_needs_no_createdb(maintenance):
    fake = maintenance(exists=True, may_create=False)
    result, refusal = await _outcome(lambda: ensure_tier_database(_URL))
    assert refusal is None, refusal
    assert result is False
    assert fake.executed == []
    assert not fake.role_probed
