"""T1201: `MONEY_MAX_INTEGER_DIGITS` is what stands between a 400 and a bare HTTP 500.

WHY THIS MODULE EXISTS. The door has two capacity bounds and its own docstring says "both
bounds below are load-bearing". Only one of them was ever tested. Measured by an external
reviewer on this tier: mutating `MONEY_MAX_SCALE` 8 -> 18 correctly reddens four tests, while
mutating `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 leaves **all nine** 012 tests green, and `grep` for
the constant or for `1000000000000` across `tests/` finds nothing.

WHAT THE BOUND IS FOR. `Numeric(20, 8)` is twenty significant digits of which eight are the
fraction, so twelve integer digits are left: `abs(value) < 10**12`. One digit more and
PostgreSQL raises `numeric field overflow` - not at the door, but inside the transaction, on the
INSERT, from where it escapes to the client as an unclassified HTTP 500. That failure mode is
the second half of `F-012-1`'s family: the caller is told the service broke rather than that the
amount was unrepresentable, and (unlike the scale half) nothing is silently rounded, so it is
invisible to any test that only checks stored values.

WHY POSTGRESQL. The overflow is a property of the real `NUMERIC(20,8)` type. SQLite's
`Numeric(20, 8)` is affinity only and stores `1000000000000` happily - the same reason section 4
of the verification plan forbids counting SQLite for `F-012-1`. The door's verdict is
backend-independent and is pinned on the default tier
(`tests/unit/test_p012_t1201_money_door_bounds.py`); what needs PostgreSQL is the demonstration
that the verdict is the only thing preventing a 500, which is the counter-check below.
"""

from __future__ import annotations

import base64
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import delete, text

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils import validation
from tests.integration.p012_pg_http import make_pg_client_fixture
from tests.integration.test_scenarios import (
    _sign_trustline_create_request,
    register_and_login,
)

pytestmark = pytest.mark.postgres

# `Numeric(20, 8)`: twenty digits, eight of them fraction, so twelve integer digits remain.
COLUMN_PRECISION = 20
COLUMN_SCALE = 8

# The smallest value the column cannot hold, and the largest one it can.
OVERFLOWING_LIMIT = "1" + "0" * (COLUMN_PRECISION - COLUMN_SCALE)  # 1000000000000
LARGEST_STORABLE_LIMIT = "9" * (COLUMN_PRECISION - COLUMN_SCALE) + "." + "9" * COLUMN_SCALE


pg_client = make_pg_client_fixture()


@pytest_asyncio.fixture
async def pair(pg_client: AsyncClient) -> AsyncGenerator[dict, None]:
    """Two participants and an equivalent of this module's own, cleaned up afterwards.

    Rows are really committed on this tier (see `p012_pg_http`), so every caller owns and
    removes its own.
    """

    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    code = f"MG{nonce}".upper()[:16]

    async with TestingSessionLocal() as session:
        equivalent = Equivalent(code=code, description="T1201 magnitude probe", precision=2)
        session.add(equivalent)
        await session.commit()
        equivalent_id = equivalent.id

    participant_ids: list[uuid.UUID] = []
    try:
        lender = await register_and_login(pg_client, f"MG_Lender_{nonce}")
        borrower = await register_and_login(pg_client, f"MG_Borrower_{nonce}")

        async with TestingSessionLocal() as session:
            rows = (
                await session.execute(
                    text("SELECT id FROM participants WHERE pid = ANY(:pids)"),
                    {"pids": [lender["pid"], borrower["pid"]]},
                )
            ).all()
            participant_ids = [r[0] for r in rows]

        yield {
            "lender": lender,
            "borrower": borrower,
            "code": code,
            "equivalent_id": equivalent_id,
        }
    finally:
        async with TestingSessionLocal() as cleanup:
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
            )
            if participant_ids:
                await cleanup.execute(
                    delete(Participant).where(Participant.id.in_(participant_ids))
                )
            await cleanup.execute(delete(Equivalent).where(Equivalent.id == equivalent_id))
            await cleanup.commit()


async def _create_trustline(pg_client: AsyncClient, pair: dict, limit: str):
    lender = pair["lender"]
    return await pg_client.post(
        "/api/v1/trustlines",
        headers=lender["headers"],
        json={
            "to": pair["borrower"]["pid"],
            "equivalent": pair["code"],
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=SigningKey(base64.b64decode(lender["priv"])),
                to_pid=pair["borrower"]["pid"],
                equivalent=pair["code"],
                limit=limit,
            ),
        },
    )


async def _trustline_limits(equivalent_id: uuid.UUID) -> list[str]:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    'SELECT "limit"::text FROM trust_lines WHERE equivalent_id = :eq ORDER BY 1'
                ),
                {"eq": equivalent_id},
            )
        ).all()
    return [r[0] for r in rows]


async def test_the_largest_limit_the_column_can_hold_is_stored_unchanged(
    pg_client: AsyncClient, pair: dict
) -> None:
    """Anti-vacuum control: the bound is the column's, so the column's maximum must go through.

    Without this, the refusal below would be consistent with a door that simply refuses large
    numbers, and a narrowing of `MONEY_MAX_INTEGER_DIGITS` would look like a pass.
    """

    response = await _create_trustline(pg_client, pair, LARGEST_STORABLE_LIMIT)

    assert response.status_code == 201, (
        f"{LARGEST_STORABLE_LIMIT!r} is the largest value Numeric({COLUMN_PRECISION}, "
        f"{COLUMN_SCALE}) can hold and the door refused it: {response.text}"
    )
    assert await _trustline_limits(pair["equivalent_id"]) == [LARGEST_STORABLE_LIMIT], (
        "PostgreSQL must hold the maximum limit exactly as submitted"
    )


async def test_a_limit_one_digit_too_large_is_a_400_and_never_reaches_the_column(
    pg_client: AsyncClient, pair: dict
) -> None:
    """`1000000000000` is refused by the door with `E009`, and no row is written.

    MUTATION THIS CATCHES: `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 (or any widening past twelve),
    performed in the counter-check below, which shows what this request does without the bound.
    """

    response = await _create_trustline(pg_client, pair, OVERFLOWING_LIMIT)

    assert response.status_code == 400, (
        f"a limit of {OVERFLOWING_LIMIT!r} has thirteen integer digits and "
        f"Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}) has room for twelve. It must be refused "
        f"at the door with a client error naming the field, not carried into the INSERT. Got "
        f"{response.status_code} {response.text}"
    )
    body = response.json()
    assert body.get("error", {}).get("code") == "E009", body
    assert await _trustline_limits(pair["equivalent_id"]) == [], (
        "a refused trust line must leave no row behind"
    )


async def test_counter_check_without_the_magnitude_bound_the_same_request_is_an_http_500(
    pg_client: AsyncClient, pair: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation the reviewer ran, performed here so the bound cannot go untested again.

    `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 was measured to leave every 012 test green. With the
    constant widened, the identical request above stops being a 400 and becomes an unclassified
    server error out of `numeric field overflow` - which is precisely what the bound exists to
    convert into a 400, and precisely what no test observed.

    If this test ever fails while the one above passes, the honest reading is that the bound has
    stopped being load-bearing (the column grew, or something else now refuses the value first),
    and the docstring in `app/utils/validation.py` claiming both bounds are load-bearing has to
    be re-measured rather than trusted.
    """

    monkeypatch.setattr(validation, "MONEY_MAX_INTEGER_DIGITS", 50)

    try:
        response = await _create_trustline(pg_client, pair, OVERFLOWING_LIMIT)
        status_code, text_body = response.status_code, response.text
    except Exception as exc:  # the overflow may escape the app entirely
        status_code, text_body = 500, f"{type(exc).__name__}: {exc}"

    assert status_code >= 500, (
        f"with the magnitude bound widened to 50 digits, {OVERFLOWING_LIMIT!r} still did not "
        f"reach PostgreSQL as an overflow: got {status_code} {text_body}. Then the bound is not "
        f"what prevents the 500, and "
        f"`test_a_limit_one_digit_too_large_is_a_400_and_never_reaches_the_column` proves "
        f"nothing about MONEY_MAX_INTEGER_DIGITS."
    )
    assert await _trustline_limits(pair["equivalent_id"]) == [], (
        "the overflow must not have left a partial row behind"
    )
