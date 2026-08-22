"""The uniqueness race on participant registration must stay a declared 409.

Program 009, `T905`.  Regression guard for a defect introduced by the T905 fix itself and
caught by re-reading it: moving the readback before the commit also moved the moment the
uniqueness violation surfaces.

`create_participant` checks for an existing participant first, so an ordinary duplicate is
rejected before any write.  The `except IntegrityError` exists for the case that check
cannot cover -- a competitor inserting the same pid or public key between the check and the
write.  That violation used to surface at the implicit flush inside `commit()`, which the
handler wrapped.  With an explicit `flush()` added ahead of the readback, it surfaces there
instead, and if the flush sits outside the handler the declared `ConflictException` (409)
silently becomes an unhandled `IntegrityError` (500) on a path whose whole point is to
answer conflicts properly.

The same trap was avoided deliberately in `action_trustline_create`, where the explicit
flush was placed inside the handler; it was missed here.  The lesson is small and worth
keeping: moving a readback across a commit moves the error surface with it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.participants.service import ParticipantService
from app.utils.exceptions import ConflictException


class _RacingSession:
    """A session whose flush raises the violation a competing transaction would cause.

    The pre-check has already passed at that point, which is exactly the window the
    handler exists for.
    """

    def __init__(self) -> None:
        self.rolled_back = False
        self.committed = False

    def add(self, obj) -> None:
        return None

    async def execute(self, *args, **kwargs):
        class _Result:
            @staticmethod
            def scalar_one_or_none():
                return None  # the pre-check finds nobody: the race is still open

        return _Result()

    async def flush(self) -> None:
        raise IntegrityError(
            "INSERT INTO participants ...",
            (),
            Exception('duplicate key value violates unique constraint "participants_pid_key"'),
        )

    async def refresh(self, obj) -> None:  # pragma: no cover - never reached in this test
        return None

    async def commit(self) -> None:  # pragma: no cover - never reached in this test
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_registration_race_is_reported_as_a_conflict_not_a_server_error(
    test_user_keys,
) -> None:
    from app.schemas.participant import ParticipantCreateRequest
    import base64
    from nacl.signing import SigningKey

    from app.core.auth.canonical import canonical_json

    payload = {
        "display_name": "Racer",
        "type": "person",
        "public_key": test_user_keys["public"],
    }
    signature = base64.b64encode(
        SigningKey(base64.b64decode(test_user_keys["private"]))
        .sign(canonical_json(payload))
        .signature
    ).decode("utf-8")

    request = ParticipantCreateRequest(
        display_name="Racer",
        type="person",
        public_key=test_user_keys["public"],
        signature=signature,
    )

    session = _RacingSession()

    with pytest.raises(ConflictException):
        await ParticipantService(session).create_participant(request)

    assert session.rolled_back, "the failed transaction must be rolled back before reporting"
