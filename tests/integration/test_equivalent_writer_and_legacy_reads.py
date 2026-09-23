"""Equivalent writers refuse a non-canonical row; reads and repairs survive a legacy one.

WHERE A LEGACY CODE CAN EXIST, and therefore where two of these tests run (programme 017, `T1702`,
2026-09-23). A row whose CODE is outside `^[A-Z0-9_]{1,16}$` can only exist in a database without
`chk_equivalents_code_format`. Migration 007 installs that CHECK on PostgreSQL only, validated
against the rows already there, and every writer in the application refuses such a code
(`app/utils/validation.py`) - so on a migrated PostgreSQL database the row cannot be written, and the
tests that seeded one there were refused by the schema before they measured anything
(`CheckViolationError ... "chk_equivalents_code_format"`). The CHECK is right and stays; the tests'
premise is a fact about SQLite, where migration 007 installs no such CHECK and a legacy row is a real
possibility. So those two tests run on a SQLite file of their own, whatever the tier is
(`legacy_sqlite_session`, which the module's `db_session` hands to the conftest's `client`). Their
assertions are unchanged. A legacy PRECISION is reachable on either backend - no CHECK bounds it -
and its tests stay on the tier.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.db.base import Base
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.sqlite_transaction_control import install_sqlite_transaction_control
from app.utils.exceptions import BadRequestException
from tests.scratch_db import install_test_sqlite_pragmas


@pytest_asyncio.fixture
async def legacy_sqlite_session(tmp_path):
    """A session over a fresh SQLite file: the schema a legacy code can live in.

    Wired as the conftest wires its SQLite engine - connect pragmas, transaction control, the
    `create_all` schema and its sessionmaker options - so the request handlers run against it exactly
    as they would on the SQLite tier.
    """
    url = f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    legacy_engine = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 30})
    install_test_sqlite_pragmas(legacy_engine.sync_engine, url=url)
    install_sqlite_transaction_control(legacy_engine.sync_engine)
    async with legacy_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=legacy_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        async with factory() as session:
            assert session.bind.dialect.name == "sqlite", session.bind.dialect.name
            yield session
    finally:
        await legacy_engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_session, request):
    """The tier's `db_session`, unless the test asked for `legacy_sqlite_session`.

    Overridden here and not in each test because the conftest's `client` (and `auth_headers` through
    it) takes `db_session` by name: this is what makes the request handlers of a legacy-code test run
    on the SQLite file the test seeded, rather than on the tier database where the row cannot exist.
    """
    if "legacy_sqlite_session" in request.fixturenames:
        yield request.getfixturevalue("legacy_sqlite_session")
    else:
        yield db_session


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_equivalent_source",
    [
        {"equivalents": ["MY-TOKEN"]},
        {"baseEquivalent": "MY-TOKEN"},
        {"trustlines": [{"equivalent": "MY-TOKEN"}]},
    ],
)
async def test_real_scenario_seeder_rejects_noncanonical_equivalent_before_write(
    db_session,
    scenario_equivalent_source,
) -> None:
    scenario = {
        **scenario_equivalent_source,
        "participants": [{"id": "SHOULD_NOT_BE_STAGED"}],
    }

    with pytest.raises(BadRequestException, match="Invalid equivalent code"):
        await RealScenarioSeeder().seed_scenario_into_db(
            session=db_session,
            scenario=scenario,
        )

    assert (
        await db_session.execute(
            select(Equivalent).where(Equivalent.code == "MY-TOKEN")
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(Participant).where(Participant.pid == "SHOULD_NOT_BE_STAGED")
        )
    ).scalar_one_or_none() is None


#: The two ENDS of the domain, which is what this test is for - not a sample of usable values.
#: Was `[0, 18]`; narrowed to `[0, 8]` on 2026-08-25 (012 / S1) because the domain itself was
#: narrowed to the storage scale of `Numeric(20, 8)` and the protocol's 0-8. The meaning of the
#: test is unchanged; only the upper end moved with the bound it names.
@pytest.mark.parametrize("precision", [0, 8])
def test_orm_writer_accepts_canonical_equivalent_precision_boundaries(
    precision: int,
) -> None:
    assert (
        Equivalent(code=f"BOUND{precision}", precision=precision).precision == precision
    )


def test_orm_writer_rejects_noncanonical_equivalent_code() -> None:
    with pytest.raises(BadRequestException, match="Invalid equivalent code"):
        Equivalent(code="MY-TOKEN", precision=2)


def test_orm_writer_rejects_noncanonical_equivalent_precision() -> None:
    """The FIRST value past the upper end, which is the only one that probes the bound.

    Was `19` (one past the old bound of 18); now `9`, one past 8. 19 would still be refused
    after the narrowing, so leaving it would have kept this test green while it stopped
    testing the boundary - a bound moved from 8 back to 18 would not have been noticed.
    """

    with pytest.raises(BadRequestException, match="Invalid equivalent precision"):
        Equivalent(code="NEWVALID", precision=9)


@pytest.mark.asyncio
async def test_legacy_invalid_equivalent_rows_remain_visible_on_read_surfaces(
    legacy_sqlite_session,
    client,
    db_session,
    auth_headers,
) -> None:
    await db_session.execute(
        insert(Equivalent.__table__).values(
            code="MY-TOKEN",
            precision=19,
            metadata={},
            is_active=True,
        )
    )
    await db_session.commit()

    admin_list = await client.get(
        "/api/v1/admin/equivalents?include_inactive=true",
        headers=_admin_headers(),
    )
    assert admin_list.status_code == 200, admin_list.text
    assert any(
        item["code"] == "MY-TOKEN" and item["precision"] == 19
        for item in admin_list.json()["items"]
    )

    public_list = await client.get(
        "/api/v1/equivalents",
        headers=auth_headers,
    )
    assert public_list.status_code == 200, public_list.text
    assert any(
        item["code"] == "MY-TOKEN" and item["precision"] == 19
        for item in public_list.json()["items"]
    )

    graph = await client.get(
        "/api/v1/admin/graph/snapshot",
        headers=_admin_headers(),
    )
    assert graph.status_code == 200, graph.text
    assert any(
        item["code"] == "MY-TOKEN" and item["precision"] == 19
        for item in graph.json()["equivalents"]
    )


#: Two kinds of row that are outside the domain and must both be repairable.
#:
#: `19` was never valid at any point in this repository's history - it is the original case.
#: `12` is the class the 2026-08-25 narrowing CREATED: it was inside the domain until that day,
#: so a live database may hold one, written through a door that accepted it at the time. Adding
#: it is the executable half of "no data migration is needed": such a row stays readable, and
#: the PATCH that repairs it is the same PATCH.
LEGACY_PRECISIONS_OUTSIDE_THE_DOMAIN = [
    ("LEGACY19", 19, "never valid"),
    ("LEGACY12", 12, "valid until the 012/S1 narrowing"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,legacy_precision,provenance",
    LEGACY_PRECISIONS_OUTSIDE_THE_DOMAIN,
    ids=[c[0] for c in LEGACY_PRECISIONS_OUTSIDE_THE_DOMAIN],
)
async def test_admin_can_repair_legacy_precision_when_code_is_canonical(
    client,
    db_session,
    code: str,
    legacy_precision: int,
    provenance: str,
) -> None:
    """The repair TARGET moved 18 -> 8 with the domain (012 / S1, 2026-08-25).

    Not a cosmetic change: 18 is now outside the domain, so patching to it would be answered
    422 by the door and this test would have been repairing a row into another broken state.
    """

    await db_session.execute(
        insert(Equivalent.__table__).values(
            code=code,
            precision=legacy_precision,
            metadata={},
            is_active=True,
        )
    )
    await db_session.commit()

    repair = await client.patch(
        f"/api/v1/admin/equivalents/{code}",
        headers=_admin_headers(),
        json={"precision": 8},
    )

    assert repair.status_code == 200, f"{code} ({provenance}): {repair.text}"
    assert repair.json()["code"] == code
    assert repair.json()["precision"] == 8
    stored = (
        await db_session.execute(
            select(Equivalent).where(Equivalent.code == code)
        )
    ).scalar_one()
    assert stored.precision == 8


@pytest.mark.asyncio
async def test_admin_patch_rejects_invalid_legacy_code_before_mutation(
    legacy_sqlite_session,
    client,
    db_session,
) -> None:
    await db_session.execute(
        insert(Equivalent.__table__).values(
            code="MY-TOKEN",
            description="before",
            precision=2,
            metadata={},
            is_active=True,
        )
    )
    await db_session.commit()

    response = await client.patch(
        "/api/v1/admin/equivalents/MY-TOKEN",
        headers=_admin_headers(),
        json={"description": "after"},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "E008"
    assert error["details"] == {
        "code": "MY-TOKEN",
        "reason": "noncanonical_code",
        "repair": "manual_cleanup",
    }
    stored = (
        await db_session.execute(
            select(Equivalent).where(Equivalent.code == "MY-TOKEN")
        )
    ).scalar_one()
    assert stored.description == "before"
    assert (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.equivalents.patch",
                AuditLog.object_id == "MY-TOKEN",
            )
        )
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,legacy_precision,provenance",
    LEGACY_PRECISIONS_OUTSIDE_THE_DOMAIN,
    ids=[c[0] for c in LEGACY_PRECISIONS_OUTSIDE_THE_DOMAIN],
)
async def test_admin_patch_rejects_nonrepairing_legacy_precision_before_mutation(
    client,
    db_session,
    code: str,
    legacy_precision: int,
    provenance: str,
) -> None:
    """A PATCH that does not repair the precision must not touch the row at all.

    PARAMETRIZED 2026-08-25 (012 / S1) over both kinds of out-of-domain row. `19` was never
    valid; `12` is the class the narrowing CREATED, and it is the one that matters here - it is
    the row a live database can actually be holding today, written through a door that accepted
    it yesterday. Without this case the "no data migration is needed" claim rested on the repair
    path alone, and the path an operator hits FIRST - an ordinary edit of the description - was
    covered only for a precision no door ever admitted.
    """

    await db_session.execute(
        insert(Equivalent.__table__).values(
            code=code,
            description="before",
            precision=legacy_precision,
            metadata={},
            is_active=True,
        )
    )
    await db_session.commit()

    response = await client.patch(
        f"/api/v1/admin/equivalents/{code}",
        headers=_admin_headers(),
        json={"description": "after"},
    )

    assert response.status_code == 409, f"{code} ({provenance}): {response.text}"
    error = response.json()["error"]
    assert error["code"] == "E008"
    assert error["details"] == {
        "code": code,
        "reason": "noncanonical_precision",
        "repair": "patch_precision",
    }
    stored = (
        await db_session.execute(
            select(Equivalent).where(Equivalent.code == code)
        )
    ).scalar_one()
    assert stored.description == "before"
    assert stored.precision == legacy_precision
    assert (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.equivalents.patch",
                AuditLog.object_id == code,
            )
        )
    ).scalar_one_or_none() is None
