"""Server tests for the admin semantics the deleted Admin UI mock used to assert (032 S4, 2026-10-07).

The Admin UI's mock client (`admin-ui/src/api/mockApi.ts`) is deleted. Several of its Vitest cases
asserted SERVER semantics - "the mock must answer what production answers" - against the mock
itself, quoting production as a contract without executing it. The cases below are the ones no
backend test held yet (inventory in the 032 Changelog); each now runs against the real routes:

- RT-013-6 / F-013-6 (`mockApi.trustlineFilterExactness.test.ts`): `/admin/trustlines` filters are
  exact - a partial or padded pid, a lower-cased or partial code finds nothing; the filters compose
  to the exact intersection, and `total` counts the filtered set, not the page
  (`mockApi.listEndpoints.test.ts:60`);
- `mockApi.listEndpoints.test.ts:21`: `/admin/participants` composes `q` (case-insensitive
  substring of pid or name) with `status` and `type`, and `total` is the filtered count;
- `mockApi.adminMutations.test.ts:214`: an `is_active` PATCH on a legacy-precision equivalent is
  refused like any other non-repairing PATCH;
- `adminMutationIntegrity.contract.test.ts:305`: a negative precision on PATCH is refused;
- `adminMutationIntegrity.contract.test.ts:243`: `/admin/equivalents/{code}/usage` counts what uses
  the equivalent;
- `mockApi.adminMutations.test.ts:75`: `/admin/audit-log` lists the newest entry first.

Each refusal has a positive control in the same test, so an always-empty filter or an always-
refusing route cannot pass it (AGENTS.md section 9).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


def _headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _rows(client, **params) -> tuple[int, list[tuple[str, str, str]]]:
    response = await client.get("/api/v1/admin/trustlines", headers=_headers(), params={"per_page": 50, **params})
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = sorted((i["equivalent"], i["from"], i["to"]) for i in payload["items"])
    return payload["total"], rows


async def _trustline_population(db_session) -> None:
    people = {
        pid: Participant(pid=pid, display_name=pid.upper(), public_key=pid[0].upper() * 63 + str(n), type="person", status="active")
        for n, pid in enumerate(("p1", "p2", "p3", "p4"))
    }
    usd = Equivalent(code="USD", symbol="$", description="Dollar", precision=2, metadata_={}, is_active=True)
    eur = Equivalent(code="EUR", symbol="E", description="Euro", precision=2, metadata_={}, is_active=True)
    db_session.add_all([*people.values(), usd, eur])
    await db_session.flush()

    def line(creditor: str, debtor: str, eq: Equivalent, limit: str, status: str) -> TrustLine:
        return TrustLine(
            from_participant_id=people[creditor].id,
            to_participant_id=people[debtor].id,
            equivalent_id=eq.id,
            limit=Decimal(limit),
            policy={"auto_clearing": True, "can_be_intermediate": True},
            status=status,
        )

    db_session.add_all(
        [
            line("p1", "p2", usd, "10", "active"),
            line("p3", "p2", usd, "20", "active"),
            line("p1", "p4", eur, "30", "closed"),
        ]
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_trustline_filters_are_exact_and_compose(client, db_session) -> None:
    await _trustline_population(db_session)

    # Positive controls first: the whole value resolves, and only its rows come back.
    assert await _rows(client, creditor="p1") == (2, [("EUR", "p1", "p4"), ("USD", "p1", "p2")])
    assert await _rows(client, debtor="p2") == (2, [("USD", "p1", "p2"), ("USD", "p3", "p2")])
    assert await _rows(client, equivalent="USD") == (2, [("USD", "p1", "p2"), ("USD", "p3", "p2")])

    # Exactness: partial pid, padded pid, lower-cased code, partial code - nothing.
    assert await _rows(client, creditor="p") == (0, [])
    assert await _rows(client, debtor="p") == (0, [])
    assert await _rows(client, creditor=" p1 ") == (0, [])
    assert await _rows(client, equivalent="usd") == (0, [])
    assert await _rows(client, equivalent="US") == (0, [])

    # Composition: the exact intersection.
    assert await _rows(client, equivalent="USD", creditor="p1", debtor="p2", status="active") == (
        1,
        [("USD", "p1", "p2")],
    )

    # A differently-cased status is not a status at all.
    response = await client.get("/api/v1/admin/trustlines", headers=_headers(), params={"status": "ACTIVE"})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_trustline_total_counts_the_filtered_set_not_the_page(client, db_session) -> None:
    await _trustline_population(db_session)

    seen: list[tuple[str, str, str]] = []
    for page in (1, 2):
        response = await client.get(
            "/api/v1/admin/trustlines",
            headers=_headers(),
            params={"debtor": "p2", "status": "active", "per_page": 1, "page": page},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 2
        assert len(payload["items"]) == 1
        seen.extend((i["equivalent"], i["from"], i["to"]) for i in payload["items"])
    assert sorted(seen) == [("USD", "p1", "p2"), ("USD", "p3", "p2")]


@pytest.mark.asyncio
async def test_participant_filters_compose_with_a_case_insensitive_search(client, db_session) -> None:
    db_session.add_all(
        [
            Participant(pid="alpha", display_name="Anna Shop", public_key="A" * 64, type="business", status="active"),
            Participant(pid="beta", display_name="Bakery", public_key="B" * 64, type="business", status="active"),
            Participant(pid="gamma", display_name="Gala", public_key="C" * 64, type="person", status="active"),
            Participant(pid="delta", display_name="Dana", public_key="D" * 64, type="business", status="suspended"),
        ]
    )
    await db_session.commit()

    async def listed(**params) -> tuple[int, list[str]]:
        response = await client.get("/api/v1/admin/participants", headers=_headers(), params={"per_page": 50, **params})
        assert response.status_code == 200, response.text
        payload = response.json()
        return payload["total"], sorted(i["pid"] for i in payload["items"])

    # Control: `q` alone is a case-insensitive substring of pid or name ('A' is in every row).
    assert await listed(q="A") == (4, ["alpha", "beta", "delta", "gamma"])
    # Composed: q AND type AND status.
    assert await listed(q="A", type="business", status="active") == (2, ["alpha", "beta"])
    assert await listed(q="shop", type="business") == (1, ["alpha"])
    assert await listed(q="A", type="person", status="suspended") == (0, [])

    response = await client.get(
        "/api/v1/admin/participants",
        headers=_headers(),
        params={"q": "A", "type": "business", "status": "active", "per_page": 1, "page": 2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1


@pytest.mark.asyncio
async def test_is_active_patch_on_a_legacy_precision_equivalent_is_refused(client, db_session) -> None:
    await db_session.execute(
        insert(Equivalent.__table__).values(
            code="LEGACY12", description="before", precision=12, metadata={}, is_active=True
        )
    )
    await db_session.commit()
    audit_before = len((await db_session.execute(select(AuditLog))).scalars().all())

    response = await client.patch(
        "/api/v1/admin/equivalents/LEGACY12",
        headers=_headers(),
        json={"is_active": False, "reason": "stop it"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "E008"
    assert response.json()["error"]["details"]["reason"] == "noncanonical_precision"
    db_session.expire_all()
    stored = (await db_session.execute(select(Equivalent).where(Equivalent.code == "LEGACY12"))).scalar_one()
    assert stored.is_active is True
    assert len((await db_session.execute(select(AuditLog))).scalars().all()) == audit_before

    # Control: the repairing PATCH is accepted, so the route does not refuse everything.
    repaired = await client.patch(
        "/api/v1/admin/equivalents/LEGACY12", headers=_headers(), json={"precision": 8}
    )
    assert repaired.status_code == 200, repaired.text


@pytest.mark.asyncio
async def test_negative_precision_on_patch_is_refused(client, db_session) -> None:
    db_session.add(Equivalent(code="TOK", symbol="T", description="Token", precision=2, metadata_={}, is_active=True))
    await db_session.commit()

    refused = await client.patch("/api/v1/admin/equivalents/TOK", headers=_headers(), json={"precision": -1})
    assert refused.status_code in (400, 422), refused.text
    db_session.expire_all()
    stored = (await db_session.execute(select(Equivalent).where(Equivalent.code == "TOK"))).scalar_one()
    assert stored.precision == 2

    accepted = await client.patch("/api/v1/admin/equivalents/TOK", headers=_headers(), json={"description": "Token 2"})
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
async def test_equivalent_usage_counts_what_uses_it(client, db_session) -> None:
    await _trustline_population(db_session)

    usd = await client.get("/api/v1/admin/equivalents/USD/usage", headers=_headers())
    assert usd.status_code == 200, usd.text
    assert usd.json()["code"] == "USD"
    assert usd.json()["trustlines"] == 2
    assert usd.json()["debts"] == 0

    eur = await client.get("/api/v1/admin/equivalents/EUR/usage", headers=_headers())
    assert eur.status_code == 200, eur.text
    assert eur.json()["trustlines"] == 1


@pytest.mark.asyncio
async def test_audit_log_lists_the_newest_entry_first(client, db_session) -> None:
    # Distinct timestamps on purpose: the order of entries with EQUAL timestamps has no tie-break
    # today (032 finding A-11, fixed by slice S1), and two PATCHes inside one test transaction share
    # `now()` - measured 2026-10-07, they came back oldest first. This holds what the mock claimed:
    # newer timestamp, earlier row.
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add_all(
        [
            AuditLog(timestamp=base, action="p032.s4.order", object_type="config", after_state={"n": 1}),
            AuditLog(timestamp=base + timedelta(minutes=5), action="p032.s4.order", object_type="config", after_state={"n": 2}),
            AuditLog(timestamp=base + timedelta(minutes=1), action="p032.s4.order", object_type="config", after_state={"n": 3}),
        ]
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/admin/audit-log", headers=_headers(), params={"action": "p032.s4.order", "per_page": 10}
    )
    assert response.status_code == 200, response.text
    assert [item["after_state"]["n"] for item in response.json()["items"]] == [2, 3, 1]
