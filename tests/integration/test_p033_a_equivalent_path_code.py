"""033 A, item 5: the four `/admin/equivalents/{code}...` routes read the path code one way.

On `0c24f030` (the spec's base) `POST /admin/equivalents/uah/integrity-hold/clear` answered 422 - the route alone
declared a strict path pattern - while PATCH, DELETE and usage already normalised `uah` to `UAH` (032 A-11).
The reproducer is the clear: `uah` must behave as `UAH` (the same 409 `no_integrity_hold` on a row that is not
held; 404 on a row that does not exist), and a code that cannot exist is a 400 on every one of the four.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from tests.conftest import MODE_B

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
REASON = {"reason": "p033 A item 5"}


def _code() -> str:
    return "P33" + uuid.uuid4().hex[:8].upper()


@MODE_B
@pytest.mark.asyncio
async def test_the_clear_of_a_lowercase_code_is_the_clear_of_the_equivalent(client, db_session) -> None:
    code = _code()
    created = await client.post("/api/v1/admin/equivalents", headers=ADMIN, json={"code": code, **REASON})
    assert created.status_code == 200, created.text

    upper = await client.post(f"/api/v1/admin/equivalents/{code}/integrity-hold/clear", headers=ADMIN, json=REASON)
    lower = await client.post(f"/api/v1/admin/equivalents/{code.lower()}/integrity-hold/clear", headers=ADMIN, json=REASON)

    assert upper.status_code == 409, upper.text
    assert upper.json()["error"]["details"]["reason"] == "no_integrity_hold"
    assert lower.status_code == 409, lower.text
    assert lower.json()["error"]["details"]["reason"] == "no_integrity_hold"


@MODE_B
@pytest.mark.asyncio
async def test_the_clear_of_an_unknown_lowercase_code_is_a_404_not_a_422(client, db_session) -> None:
    missing = _code().lower()

    response = await client.post(f"/api/v1/admin/equivalents/{missing}/integrity-hold/clear", headers=ADMIN, json=REASON)

    assert response.status_code == 404, response.text


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["bad-code", "x" * 17, "a.b"])
async def test_a_code_that_cannot_exist_is_a_400_on_all_four_routes(client, db_session, bad: str) -> None:
    base = f"/api/v1/admin/equivalents/{bad}"

    responses = {
        "patch": await client.patch(base, headers=ADMIN, json=REASON),
        "delete": await client.request("DELETE", base, headers=ADMIN, json=REASON),
        "usage": await client.get(f"{base}/usage", headers=ADMIN),
        "clear": await client.post(f"{base}/integrity-hold/clear", headers=ADMIN, json=REASON),
    }

    assert {name: r.status_code for name, r in responses.items()} == {n: 400 for n in responses}, {
        name: r.text for name, r in responses.items()
    }
