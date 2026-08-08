from __future__ import annotations

import pytest
from sqlalchemy import insert, select

from app.config import settings
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import BadRequestException


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


@pytest.mark.parametrize("precision", [0, 18])
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
    with pytest.raises(BadRequestException, match="Invalid equivalent precision"):
        Equivalent(code="NEWVALID", precision=19)


@pytest.mark.asyncio
async def test_legacy_invalid_equivalent_rows_remain_visible_on_read_surfaces(
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


@pytest.mark.asyncio
async def test_admin_can_repair_legacy_precision_when_code_is_canonical(
    client,
    db_session,
) -> None:
    await db_session.execute(
        insert(Equivalent.__table__).values(
            code="LEGACY19",
            precision=19,
            metadata={},
            is_active=True,
        )
    )
    await db_session.commit()

    repair = await client.patch(
        "/api/v1/admin/equivalents/LEGACY19",
        headers=_admin_headers(),
        json={"precision": 18},
    )

    assert repair.status_code == 200, repair.text
    assert repair.json()["code"] == "LEGACY19"
    assert repair.json()["precision"] == 18
    stored = (
        await db_session.execute(
            select(Equivalent).where(Equivalent.code == "LEGACY19")
        )
    ).scalar_one()
    assert stored.precision == 18
