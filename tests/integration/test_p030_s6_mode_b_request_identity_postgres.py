"""030 S6: in mode B a request reads the database, not the identity map the test's session kept from before.

Production opens one session per request, so a request starts with an empty identity map and every load reads the
committed row. In mode B every request runs on the test's ONE session (`tests/conftest.py`, `override_get_db`), whose
identity map outlives the request: an ORM object an earlier request - or the test - loaded comes back from a later
`select(TrustLine)` or `session.get` with the attributes it had then, not the ones another session committed since.
Found in `test_p026_s3_close_request_postgres.py` (a refused PATCH kept the line alive; the next GET answered
`active` for a line the payment's own session had closed) and worked around there by forgetting the lines in the test
(`235706c5`). The mechanism is fixed once, in `override_get_db`; these cells are its guard.

Red on `9ed439de` (conftest unchanged): all four cells, gc off. `test_handles_stay_readable_...` is the control of the
fix's shape: the test's handles must stay readable without IO (the session is `expire_on_commit=False`) and must show
what a request loaded through them - the sharing the existing mode-B tests read their results by. Its red on the old
conftest is the stale read itself; what it protects afterwards is that `expunge_all` / `expire_all` are not the fix."""

from __future__ import annotations

import gc
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.api.deps import get_db
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.main import app
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _patch_limit, _pay, _world
from tests.integration.test_p026_s3_close_request_postgres import _close


@pytest.fixture(autouse=True)
def _the_collector_is_off_for_the_test():
    """An object kept alive only by a cycle (a refused request's traceback) stays in the identity map for the test."""

    gc.disable()
    try:
        yield
    finally:
        gc.enable()


async def _seed(db_session):
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"ID{n}", symbol="I", description=None, precision=2, metadata_={}, is_active=True)
    a, b = (Participant(pid=f"{k}_ID_{n}", display_name=k, public_key=f"pk{k}-{n}", type="person", status="active",
                        profile={}) for k in "AB")
    db_session.add_all([eq, a, b])
    await db_session.flush()
    line = TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100"),
                     status="active", policy={})
    db_session.add(line)
    await db_session.commit()
    return a, line


async def _committed_elsewhere(db_session, line_id, **values) -> None:
    async with sessionmaker_of(db_session)() as other:
        await other.execute(update(TrustLine).where(TrustLine.id == line_id).values(**values))
        await other.commit()


async def _one_request(read):
    """What `get_db` hands a request under this conftest: the override's span, driven by hand around `read(session)`."""

    span = app.dependency_overrides[get_db]()
    session = await span.__anext__()
    try:
        return await read(session)
    finally:
        await span.aclose()


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["select", "get"])
async def test_a_request_reads_the_committed_row(client, db_session, how) -> None:  # `client` installs the override
    _a, line = await _seed(db_session)  # the test's handle: loaded, limit 100
    await _committed_elsewhere(db_session, line.id, limit=Decimal("55"))

    async def read(session):
        if how == "get":
            return (await session.get(TrustLine, line.id)).limit
        return (await session.execute(select(TrustLine.id, TrustLine).where(TrustLine.id == line.id))).one()[1].limit

    assert await _one_request(read) == Decimal("55"), "the request answered from the identity map, not the committed row"


@MODE_B
@pytest.mark.asyncio
async def test_handles_stay_readable_and_show_what_a_request_loaded_through_them(client, db_session) -> None:
    a, line = await _seed(db_session)
    await _committed_elsewhere(db_session, line.id, limit=Decimal("55"))

    async def read(session):
        return (await session.execute(select(TrustLine).where(TrustLine.id == line.id))).scalar_one()

    row = await _one_request(read)
    assert row is line and line.limit == Decimal("55")  # the shared handle shows the request's read ...
    assert a.display_name == "A"  # ... and a handle the request never touched is readable without IO


@MODE_B
@pytest.mark.asyncio
async def test_a_refused_request_leaves_nothing_the_next_request_reads_stale(client, db_session) -> None:
    """The p026 shape, whole: a refused PATCH, a payment on another session that completes the requested close, a GET."""

    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    assert await _pay(factory, b, a, code, "50")
    assert (await _close(client, a, lines["AB"])).status_code == 200  # requested: pending zero
    assert (await _patch_limit(client, a, lines["AB"], "10")).status_code == 409  # the refused request
    assert await _pay(factory, a, b, code, "50")  # another session closes the line
    assert await _debts(factory, code) == {}
    r = await client.get(f"/api/v1/trustlines/{lines['AB']}", headers=a["headers"])
    assert r.status_code == 200 and r.json()["status"] == "closed", r.text


@MODE_B
@pytest.mark.asyncio
async def test_overlapping_requests_leave_the_session_as_it_was_when_the_last_one_ends(client, db_session) -> None:
    """A held stream is a request still open while others run on the same session: the first to end must not undo it."""

    first, second = app.dependency_overrides[get_db](), app.dependency_overrides[get_db]()
    session = await first.__anext__()
    await second.__anext__()
    await first.aclose()
    assert "get" in session.sync_session.__dict__, "the second request lost its identity handling when the first ended"
    await second.aclose()
    assert "get" not in session.sync_session.__dict__ and "geo_request_depth" in session.info
    assert session.info["geo_request_depth"] == 0
