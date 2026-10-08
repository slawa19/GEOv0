"""035 A5 (`F-035-4`): `POST /integrity/verify` whose audit row cannot be prepared answers the internal error and
commits NOTHING - not a 200 that left no trace, and not the rows of the equivalents it had already prepared.

The handler built the `IntegrityAuditLog` object and added it inside `try: ... except Exception: pass`
(`app/api/v1/integrity.py`), so a failure BEFORE `db.add` was dropped and the operator read a verified state that
left no trace. The failure here is the construction of the audit object itself - not a database refusal at the
commit, which is outside that `except` and already surfaced.

HOW "NOTHING IS COMMITTED" IS OBSERVED (review of `a181b6c1`: the first edition of this test stayed green under a
handler that committed and re-raised). Two things have to be as in production, and neither is in mode A:

* the request has ITS OWN session, opened and closed around it as `app/api/deps.py::get_db` does - the fixture's
  shared session is not closed by a failed request, and in mode B the fixture commits it after every request, so a
  row the handler only added would be committed BY THE STAND;
* the result is read on ANOTHER session, after the request - what a later reader of the database sees.

So the test runs on a disposable clone (`MODE_B`), overrides `get_db` with a session per request on that clone, and
counts the rows through a fresh session.

The injection is on the model's constructor, so it holds wherever the preparation lives. It fails the k-th
construction of the request, for k = 1, 2, 3: the equivalents prepared before it must leave no row either.

What this does not see: the byte equality of `status`/`verify` answers before and after the fix, a failure at the
commit itself, and a failure that is not an exception of the constructor.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_db
from app.config import settings
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.main import app
from tests.conftest import MODE_B, sessionmaker_of

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
_EQUIVALENTS = 3


async def _verify_rows(factory) -> int:
    """`INTEGRITY_VERIFY` rows in the database, read on a session of its own."""

    async with factory() as session:
        return (await session.execute(
            select(func.count()).select_from(IntegrityAuditLog)
            .where(IntegrityAuditLog.operation_type == "INTEGRITY_VERIFY")
        )).scalar_one()


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("failing", [1, 2, 3], ids=["the first row", "the second row", "the third row"])
async def test_a_verify_whose_audit_row_cannot_be_prepared_answers_500_and_commits_nothing(
    client, db_session, monkeypatch, failing
):
    factory = sessionmaker_of(db_session)
    n = uuid.uuid4().hex[:8].upper()
    db_session.add_all([Equivalent(code=f"AU{k}{n}", description=f"AU{k}", precision=2) for k in range(_EQUIVALENTS)])
    await db_session.commit()
    async with factory() as session:
        equivalents = (await session.execute(select(func.count()).select_from(Equivalent))).scalar_one()
    assert equivalents >= _EQUIVALENTS

    async def _a_session_per_request():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _a_session_per_request  # the `client` fixture clears the overrides
    # The fixture's client re-raises an exception the application answered with 500; this one returns the answer.
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        # Anti-vacuum: an ordinary verify of everything writes one audit row for each equivalent.
        healthy = await ac.post("/api/v1/integrity/verify", json={}, headers=ADMIN)
        assert healthy.status_code == 200, healthy.text
        before = await _verify_rows(factory)
        assert before == equivalents

        built = 0
        construct = IntegrityAuditLog.__init__

        def _the_failing_one_cannot_be_built(self, **kwargs):
            nonlocal built
            built += 1
            if built == failing:
                raise ValueError("p035-a5: the audit object cannot be built")
            construct(self, **kwargs)

        monkeypatch.setattr(IntegrityAuditLog, "__init__", _the_failing_one_cannot_be_built)
        response = await ac.post("/api/v1/integrity/verify", json={}, headers=ADMIN)
        monkeypatch.undo()

    # The failing construction was reached (a handler that swallowed it goes on to build the rest: `built` is then
    # larger, and the status below says what went wrong).
    assert built >= failing, f"stand: the request built {built} audit object(s), the failure was set on #{failing}"
    assert response.status_code == 500, (
        f"POST /integrity/verify with an audit row that cannot be prepared: actual={response.status_code}, "
        f"expected=500 ({response.text})"
    )
    error = response.json()["error"]
    assert error["code"] == "E010" and error["request_id"], response.text
    after = await _verify_rows(factory)
    assert after == before, (
        f"the failed verify committed audit rows: actual={after - before} new INTEGRITY_VERIFY row(s) with the "
        f"failure on row #{failing}, expected=0"
    )
