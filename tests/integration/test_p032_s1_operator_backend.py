"""032 S1 - the operator side of the admin backend: reproducers (spec `032-admin-refactoring`, Verification plan).

Each test below names the finding it reproduces. On `c9789774` (the spec's base) the reproducers are red:
`/admin/migrations` answered `is_up_to_date=false` on a database at head (A-1, `MissingGreenlet` swallowed); a
repeated `POST /admin/equivalents` was a 500 (A-2); `unfreeze` over a `deleted` participant answered 200 and
`active` (A-5); `PATCH /admin/equivalents/uah` was a 404 (A-11); `_` and `%` in a search were wildcards (A-11);
ban/unban, feature-flags and whoami answered (F-5, F-6). The 403 controls are green on both sides: removing
`dependencies=[]` must not weaken the router's admin guard.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.core.participants.service import ParticipantService
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import ConflictException
from tests.conftest import MODE_B
from tests.migrated_schema import repository_head

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


def _code() -> str:
    return "S1" + uuid.uuid4().hex[:8].upper()


async def _participant(db_session, status: str, *, display_name: str | None = None) -> Participant:
    pid = f"p032-s1-{uuid.uuid4().hex[:12]}"
    row = Participant(pid=pid, display_name=display_name or pid, public_key=uuid.uuid4().hex * 2, type="person",
                      status=status)
    db_session.add(row)
    await db_session.commit()
    return row


async def _audit_rows(db_session, *, action: str, object_id: str) -> int:
    rows = await db_session.execute(select(AuditLog).where(AuditLog.action == action, AuditLog.object_id == object_id))
    return len(rows.scalars().all())


# -- A-1 -------------------------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def own_app_engine(monkeypatch):
    """`/admin/migrations` reads through the application's engine (`app.db.session.engine`). Its pool outlives a
    test's event loop, so a connection another test left there belongs to a closed loop; the route gets an engine
    of this test's loop, over the same tier database, disposed at the end."""

    import app.db.session as app_db_session

    engine = create_async_engine(str(app_db_session.engine.url.render_as_string(hide_password=False)))
    monkeypatch.setattr(app_db_session, "engine", engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a1_migrations_status_reports_a_database_at_head_as_up_to_date(client, db_session, own_app_engine) -> None:
    stamps = (await db_session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    head = repository_head()
    assert stamps == [head], f"premise: the tier is migrated to head (GEO_TEST_USE_MIGRATED_SCHEMA=1), got {stamps}"

    response = await client.get("/api/v1/admin/migrations", headers=ADMIN)

    assert response.status_code == 200, response.text
    assert response.json() == {"current_revision": head, "head_revision": head, "is_up_to_date": True}


@pytest.mark.asyncio
async def test_a1_a_failed_migrations_read_is_logged_not_swallowed(client, monkeypatch, caplog, own_app_engine) -> None:
    from alembic.runtime.migration import MigrationContext

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("p032-s1 migration context unavailable")

    monkeypatch.setattr(MigrationContext, "configure", staticmethod(_refuse))
    with caplog.at_level(logging.ERROR, logger="app.api.v1.admin"):
        response = await client.get("/api/v1/admin/migrations", headers=ADMIN)

    assert response.status_code == 200, response.text
    assert response.json()["is_up_to_date"] is False
    failures = [r for r in caplog.records if r.getMessage().startswith("admin.migrations.status_failed")]
    assert len(failures) == 1 and failures[0].exc_info is not None, caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/admin/migrations", "/api/v1/admin/config"])
async def test_a11_reads_that_carried_an_empty_dependencies_list_still_refuse_without_the_token(client, path) -> None:
    assert (await client.get(path)).status_code == 403
    assert (await client.get(path, headers={"X-Admin-Token": "wrong"})).status_code == 403


# -- A-2 -------------------------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_a2_a_repeated_equivalent_code_is_409_code_exists(client, db_session) -> None:
    code = _code()
    body = {"code": code, "description": "first", "reason": "p032 A-2"}
    first = await client.post("/api/v1/admin/equivalents", headers=ADMIN, json=body)
    assert first.status_code == 200, first.text

    second = await client.post("/api/v1/admin/equivalents", headers=ADMIN, json={**body, "description": "second"})

    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"] == {"reason": "code_exists", "code": code}
    stored = (await db_session.execute(select(Equivalent).where(Equivalent.code == code))).scalar_one()
    assert stored.description == "first"
    assert await _audit_rows(db_session, action="admin.equivalents.create", object_id=code) == 1


# -- A-5 -------------------------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "command"),
    [
        ("deleted", "unfreeze"),  # was 200 and `active`: the operator silently lifted a ban
        ("deleted", "freeze"),  # was 200 and `suspended`: a ban turned into a freeze
        ("left", "unfreeze"),
        ("left", "freeze"),
        ("suspended", "freeze"),  # a repeated freeze
        ("active", "unfreeze"),  # a repeated unfreeze
    ],
)
async def test_a5_an_operator_transition_outside_the_matrix_is_409_and_changes_nothing(
    client, db_session, initial: str, command: str
) -> None:
    row = await _participant(db_session, initial)

    response = await client.post(f"/api/v1/admin/participants/{row.pid}/{command}", headers=ADMIN,
                                 json={"reason": "p032 A-5"})

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "status_transition_not_allowed", details
    assert details["status"] == initial
    await db_session.refresh(row)
    assert row.status == initial
    assert await _audit_rows(db_session, action=f"admin.participants.{command}", object_id=row.pid) == 0


@MODE_B
@pytest.mark.asyncio
async def test_a5_freeze_then_unfreeze_is_the_operator_matrix(client, db_session) -> None:
    row = await _participant(db_session, "active")

    frozen = await client.post(f"/api/v1/admin/participants/{row.pid}/freeze", headers=ADMIN, json={"reason": "a"})
    assert frozen.status_code == 200 and frozen.json() == {"pid": row.pid, "status": "suspended"}, frozen.text
    unfrozen = await client.post(f"/api/v1/admin/participants/{row.pid}/unfreeze", headers=ADMIN, json={"reason": "b"})
    assert unfrozen.status_code == 200 and unfrozen.json() == {"pid": row.pid, "status": "active"}, unfrozen.text
    assert await _audit_rows(db_session, action="admin.participants.freeze", object_id=row.pid) == 1
    assert await _audit_rows(db_session, action="admin.participants.unfreeze", object_id=row.pid) == 1


@pytest.mark.asyncio
async def test_a5_set_status_requires_the_allowed_sources_and_checks_them_under_the_lock(db_session) -> None:
    row = await _participant(db_session, "deleted")
    service = ParticipantService(db_session)

    with pytest.raises(TypeError):
        await service.set_status(row.pid, "suspended")  # `from_statuses` is a required keyword
    with pytest.raises(ConflictException) as refused:
        await service.set_status(row.pid, "suspended", from_statuses=("active",))
    assert refused.value.details == {"reason": "status_transition_not_allowed", "pid": row.pid, "status": "deleted",
                                     "requested": "suspended"}
    await db_session.refresh(row)
    assert row.status == "deleted"

    changed, before = await service.set_status(row.pid, "active", from_statuses=("deleted",))
    assert (changed.status, before) == ("active", "deleted")


# -- A-11 ------------------------------------------------------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_a11_a_lowercase_code_in_the_patch_path_is_the_equivalent(client, db_session) -> None:
    code = _code()
    created = await client.post("/api/v1/admin/equivalents", headers=ADMIN,
                                json={"code": code, "description": "before", "reason": "p032 A-11"})
    assert created.status_code == 200, created.text

    patched = await client.patch(f"/api/v1/admin/equivalents/{code.lower()}", headers=ADMIN,
                                 json={"description": "after", "reason": "p032 A-11"})

    assert patched.status_code == 200, patched.text
    assert patched.json()["code"] == code and patched.json()["description"] == "after"
    usage = await client.get(f"/api/v1/admin/equivalents/{code.lower()}/usage", headers=ADMIN)
    assert usage.status_code == 200 and usage.json()["code"] == code, usage.text


@pytest.mark.asyncio
@pytest.mark.parametrize("needle", ["_", "%"])
async def test_a11_participant_search_treats_like_metacharacters_literally(client, db_session, needle: str) -> None:
    tag = uuid.uuid4().hex[:8]
    literal = await _participant(db_session, "active", display_name=f"lit{tag}a{needle}b")
    await _participant(db_session, "active", display_name=f"lit{tag}axb")

    response = await client.get("/api/v1/admin/participants", headers=ADMIN,
                                params={"q": f"lit{tag}a{needle}b", "per_page": 200})

    assert response.status_code == 200, response.text
    assert [item["pid"] for item in response.json()["items"]] == [literal.pid]


@pytest.mark.asyncio
async def test_a11_audit_log_search_treats_like_metacharacters_literally(client, db_session) -> None:
    tag = uuid.uuid4().hex[:8]
    db_session.add_all([AuditLog(actor_role="admin", action="p032.s1", reason=f"r{tag}a_b"),
                        AuditLog(actor_role="admin", action="p032.s1", reason=f"r{tag}axb")])
    await db_session.commit()

    response = await client.get("/api/v1/admin/audit-log", headers=ADMIN, params={"q": f"r{tag}a_b"})

    assert response.status_code == 200, response.text
    assert [item["reason"] for item in response.json()["items"]] == [f"r{tag}a_b"]


@pytest.mark.asyncio
async def test_a11_audit_log_rows_with_one_timestamp_page_in_id_order(client, db_session) -> None:
    action = f"p032.s1.tie.{uuid.uuid4().hex[:8]}"
    stamp = datetime.now(timezone.utc)
    rows = [AuditLog(id=uuid.uuid4(), timestamp=stamp, actor_role="admin", action=action, reason=str(i))
            for i in range(6)]
    db_session.add_all(rows)
    await db_session.commit()

    seen = []
    for page in range(1, 7):
        response = await client.get("/api/v1/admin/audit-log", headers=ADMIN,
                                    params={"action": action, "per_page": 1, "page": page})
        assert response.status_code == 200, response.text
        seen.extend(item["id"] for item in response.json()["items"])

    assert seen == sorted((str(r.id) for r in rows), reverse=True)


# -- F-5, F-6 --------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/admin/participants/{pid}/ban"),
        ("POST", "/api/v1/admin/participants/{pid}/unban"),
        ("GET", "/api/v1/admin/feature-flags"),
        ("PATCH", "/api/v1/admin/feature-flags"),
        ("GET", "/api/v1/admin/whoami"),
    ],
)
async def test_f5_f6_removed_operator_routes_are_gone(client, db_session, method: str, path: str) -> None:
    row = await _participant(db_session, "active")

    response = await client.request(method, path.format(pid=row.pid), headers=ADMIN, json={"reason": "p032 F-5"})

    assert response.status_code in {404, 405}, response.text
    await db_session.refresh(row)
    assert row.status == "active"
