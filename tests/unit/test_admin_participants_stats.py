from __future__ import annotations

import pytest

from app.config import settings
from app.db.models.participant import Participant


@pytest.mark.asyncio
async def test_admin_participants_stats_requires_admin_token(client):
    r = await client.get("/api/v1/admin/participants/stats")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_participants_stats_counts(client, db_session):
    db_session.add_all(
        [
            Participant(pid="alice", display_name="Alice", public_key="A" * 64, type="person", status="active"),
            Participant(pid="bob", display_name="Bob", public_key="B" * 64, type="person", status="suspended"),
            Participant(pid="carol", display_name="Carol", public_key="C" * 64, type="business", status="deleted"),
        ]
    )
    await db_session.commit()

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}
    r = await client.get("/api/v1/admin/participants/stats", headers=headers)
    assert r.status_code == 200

    payload = r.json()
    assert payload["total_participants"] == 3
    assert payload["participants_by_status"].get("active") == 1
    assert payload["participants_by_status"].get("suspended") == 1
    assert payload["participants_by_status"].get("deleted") == 1

    assert payload["participants_by_type"].get("person") == 2
    assert payload["participants_by_type"].get("business") == 1
