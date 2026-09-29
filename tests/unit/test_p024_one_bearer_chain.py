"""024 `T2414.2` (F-024-9): the chain "bearer token -> participant -> active" has one owner in `app/api/deps.py`.

It was written out three times. The three callers keep their own answer to a token that does not decode: the
participant and participant-or-admin dependencies refuse with 401, the simulator actor falls through to its
cookie. A participant who is not active is 403 and an unknown subject 401 on every path. This pins those
answers, so the single helper cannot quietly give one caller another's policy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.api import deps
from app.config import settings
from app.core.simulator.session import COOKIE_NAME, create_session
from app.db.models.participant import Participant
from app.utils.exceptions import ForbiddenException, UnauthorizedException
from app.utils.security import create_access_token


@pytest.fixture
async def people(db_session):
    db_session.add_all(
        [
            Participant(pid="p024-active", display_name="A", public_key="A" * 64, type="person", status="active"),
            Participant(pid="p024-frozen", display_name="F", public_key="F" * 64, type="person", status="suspended"),
        ]
    )
    await db_session.flush()
    return db_session


def _request(cookie: str | None = None) -> MagicMock:
    request = MagicMock()
    request.method = "GET"
    request.headers = {}
    request.cookies = {COOKIE_NAME: cookie} if cookie else {}
    return request


async def _actor(db, token: str | None, cookie: str | None = None):
    return await deps.require_simulator_actor(
        request=_request(cookie), db=db, x_admin_token=None, x_simulator_owner=None, token=token
    )


@pytest.mark.asyncio
async def test_each_caller_keeps_its_answer(people) -> None:
    db = people
    active, frozen, unknown = (create_access_token(pid) for pid in ("p024-active", "p024-frozen", "p024-nobody"))

    for check in (
        lambda token: deps.get_current_participant(db=db, token=token),
        lambda token: deps.require_participant_or_admin(db=db, token=token, x_admin_token=None),
    ):
        assert (await check(active)).pid == "p024-active"
        with pytest.raises(ForbiddenException):
            await check(frozen)
        with pytest.raises(UnauthorizedException, match="Participant not found"):
            await check(unknown)
        with pytest.raises(UnauthorizedException, match="Could not validate credentials"):
            await check("not-a-token")

    actor = await _actor(db, active)
    assert (actor.kind, actor.participant_pid, actor.owner_id) == ("participant", "p024-active", "pid:p024-active")
    with pytest.raises(ForbiddenException):
        await _actor(db, frozen)
    with pytest.raises(UnauthorizedException, match="Participant not found"):
        await _actor(db, unknown)
    # A token that does not decode is not a refusal here: the cookie decides.
    cookie, session = create_session(settings.SIMULATOR_SESSION_SECRET)
    actor = await _actor(db, "not-a-token", cookie)
    assert (actor.kind, actor.owner_id) == ("anon", session.owner_id)
    with pytest.raises(UnauthorizedException, match="No valid credentials"):
        await _actor(db, "not-a-token")
