"""`F-011-7`: the polling fallback answers an empty array, and both documents now say so.

Program 011 removed the canon's promise that `GET /simulator/events/poll` returns an array of
`SimulatorEvent` - six variants a client would write parsing code for and never receive. The
handler is a comment saying there is no replay buffer and `return []`, so the contract was changed
to state that: `type: array, maxItems: 0`, declared on both sides.

That is the strongest positive claim this programme makes about any body - not "here is the shape"
but "there is never anything here" - and until this file nothing executed it. T1108 found that:
editing `return []` would have made both documents fiction with the whole gate green.

The parameters are asserted too. They are declared as ignored, and a handler that started reading
`after` would be a behaviour change the canon does not describe.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings

_URL = "/api/v1/simulator/events/poll"


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"equivalent": "UAH"},
        {"equivalent": "USD"},
        {"equivalent": "UAH", "after": "evt_00000000"},
        {"equivalent": "UAH", "after": ""},
    ],
    ids=["bare", "other-equivalent", "with-cursor", "empty-cursor"],
)
async def test_the_poll_answers_an_empty_array_whatever_it_is_asked(
    client: AsyncClient, params: dict[str, str]
) -> None:
    """Four inputs, one answer. If any of them ever returns an element, the canon is wrong."""

    response = await client.get(_URL, params=params, headers=_admin_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == [], (
        f"{_URL} returned {body!r} for {params!r}. The canon declares this array with "
        "`maxItems: 0` (`F-011-7`); a real element makes authority number one wrong, and the "
        "declaration must move in the same change as the handler."
    )


@pytest.mark.asyncio
async def test_the_cursor_makes_no_difference_because_nothing_reads_it(
    client: AsyncClient,
) -> None:
    """The two parameters are documented as ignored; this is what "ignored" has to mean.

    Not a restatement of the test above: that one pins the body, this one pins that the body does
    not depend on the input. A replay buffer that honoured `after` would pass the first test on an
    empty database and fail this one as soon as there were events to skip.
    """

    without = await client.get(_URL, params={"equivalent": "UAH"}, headers=_admin_headers())
    with_cursor = await client.get(
        _URL, params={"equivalent": "UAH", "after": "evt_zzzzzzzz"}, headers=_admin_headers()
    )

    assert without.status_code == with_cursor.status_code == 200
    assert without.json() == with_cursor.json() == []
