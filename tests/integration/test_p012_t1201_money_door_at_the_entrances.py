"""T1201: what the money door does at the two HTTP entrances, over HTTP.

`tests/unit/test_p012_t1201_money_door_bounds.py` pins `parse_money_amount` as a function. This
module pins the two places a client meets it, because the defect it closes was not in the
function at all - it was in how the entrance called it.

**`POST /trustlines` refused `{"limit": "1e3"}` and accepted `{"limit": 1e3}`.** Same number, two
verdicts. The cause: `limit` was typed `Decimal` on the schema, so pydantic destroyed the
client's spelling before the service saw it (`"1e3"` -> `Decimal('1E+3')`, `1e3` ->
`Decimal('1000')`), and `trustlines/service.py` then called the door on `str(data.limit)` -
i.e. on a spelling `Decimal.__str__` had invented. Worse, the SIGNED payload was rebuilt from
the same `str(data.limit)`, so for `"0.00000001"` the client signed `"0.00000001"` while the
server verified against `"1E-8"` - the smallest storable limit was unsignable (found by external
review of the first edition of this fix, which validated the value but kept signing the repr).

The fix is the one `api/openapi.yaml` had declared all along: `limit` is `type: string` there,
and the pydantic schema now agrees (`TrustLineCreateRequest.limit: str`). The door sees the
client's own string, the signature covers it verbatim, and both entrances now hold the same
contract:

**exponent notation is refused where a client writes it.** `amount` and `limit` are `str` on
the wire and the signature is taken over them verbatim, so accepting `"1e3"` would put
exponent-form money into `transactions.payload` / the signed trust-line payload, i.e. into
stored history. `T1209` has also already told 011 that the canon must declare money fields with
`pattern: ^-?\\d+(\\.\\d+)?$`; accepting `"1e3"` on the server would contradict the contract 012
is asking the canon to publish. A JSON NUMBER, in turn, is a type error (422) - money is a
string on the wire, which is what makes "sign exactly what you sent" a contract a client can
actually meet.

HOW THE DOOR IS TOLD APART FROM EVERYTHING ELSE HERE. Both a refusal by the door and a bad
signature answer HTTP 400; they differ by error code - `E009` (validation) against `E005`
(signature). The door is deliberately checked BEFORE `verify_signature` at both entrances, so a
request with a deliberately invalid signature is an exact probe of the door alone: `E009` means
the door refused, `E005` means the door let it through. That is why several tests below sign
nothing real - they are about the door's verdict, and reaching the signature check IS the
verdict. The tests that need to prove acceptance is real, rather than merely not-a-400, sign
properly and assert 201/200.

Default tier on purpose: nothing here depends on how `NUMERIC(20,8)` behaves on write, only on
what the entrance decides. The write-side consequences are on the PostgreSQL tier
(`test_p012_rt1_*`, `test_p012_t1201_magnitude_bound_is_load_bearing_postgres.py`).
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    _sign_trustline_update_request,
    register_and_login,
)

EQUIVALENT = "DOOR"

# A signature that is syntactically a signature and cryptographically nothing. Used to probe the
# door: it runs first, so this request gets E009 if the door refuses and E005 if it does not.
UNSIGNABLE = base64.b64encode(b"\x00" * 64).decode("ascii")


async def _seed_equivalent(db_session) -> Equivalent:
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == EQUIVALENT))
    eq = result.scalar_one_or_none()
    if not eq:
        eq = Equivalent(code=EQUIVALENT, description="T1201 door probe", precision=2)
        db_session.add(eq)
        await db_session.commit()
        await db_session.refresh(eq)
    return eq


def _error_code(response) -> str | None:
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:  # pragma: no cover - defensive
        return None


async def _post_trustline_raw(client: AsyncClient, headers: dict, body: str):
    """POST a trust line as raw JSON text, so `1e3` stays a JSON NUMBER and `"1e3"` a string.

    `json=` would round-trip through Python and erase exactly the distinction under test.
    """

    return await client.post(
        "/api/v1/trustlines",
        headers={**headers, "Content-Type": "application/json"},
        content=body,
    )


@pytest.mark.asyncio
async def test_a_trustline_limit_is_a_string_on_the_wire_and_a_json_number_is_a_type_error(
    client: AsyncClient, db_session
) -> None:
    """`{"limit": 1e3}` - a JSON NUMBER - is refused by the schema (422), not parsed and signed.

    The canon has always said `limit: type: string`; while the pydantic schema said `Decimal`,
    a number was quietly accepted and the server then signed a spelling of its own invention.
    A client cannot sign "whatever `str(Decimal)` will say" - it can only sign the bytes it
    sent, so the schema now refuses what the client cannot sign.

    MUTATION THIS CATCHES: retyping `TrustLineCreateRequest.limit` back to `Decimal`.
    """

    await _seed_equivalent(db_session)
    lender = await register_and_login(client, "Door_JsonNumber_Lender")
    borrower = await register_and_login(client, "Door_JsonNumber_Borrower")

    body = json.dumps(
        {"to": borrower["pid"], "equivalent": EQUIVALENT, "signature": UNSIGNABLE}
    )
    body = body[:-1] + ', "limit": 1e3}'
    response = await _post_trustline_raw(client, lender["headers"], body)

    assert response.status_code == 422, (
        f"a JSON-number limit must be a schema-level type error (limit is `type: string` in "
        f"the canon), got {response.status_code} {response.text}. If this is 201/E005 the "
        f"schema is parsing numbers again, i.e. accepting money the client cannot sign."
    )


@pytest.mark.asyncio
async def test_the_trustline_door_refuses_exponent_notation_written_by_a_client(
    client: AsyncClient, db_session
) -> None:
    """`{"limit": "1e3"}` gets the same verdict `POST /payments` gives `"amount": "1e3"`: E009.

    Now that `limit` is a string on the wire, the exponent decision (see the payments test
    below) applies here unchanged: the signature covers the string verbatim, so an accepted
    `"1e3"` would be exponent-form money inside a signed, stored payload.
    """

    await _seed_equivalent(db_session)
    lender = await register_and_login(client, "Door_ExpString_Lender")
    borrower = await register_and_login(client, "Door_ExpString_Borrower")

    response = await client.post(
        "/api/v1/trustlines",
        headers=lender["headers"],
        json={
            "to": borrower["pid"],
            "equivalent": EQUIVALENT,
            "limit": "1e3",
            "signature": UNSIGNABLE,
        },
    )

    assert response.status_code == 400 and _error_code(response) == "E009", (
        f"limit '1e3' is exponent notation written by the client and must be refused by the "
        f"door (E009) before the signature check; got {response.status_code} {response.text}"
    )


@pytest.mark.asyncio
async def test_the_smallest_storable_limit_is_signable_by_a_client_that_signs_what_it_sends(
    client: AsyncClient, db_session
) -> None:
    """`"0.00000001"` signed over its own bytes is a 201, on create AND on update.

    The regression pinned here (found by external review): the first edition of this fix
    validated `data.limit` as a value but still rebuilt the signed payload from
    `str(data.limit)`, which for `"0.00000001"` is `"1E-8"` - so the client's Ed25519
    signature over the string it actually sent could never verify, and the smallest limit
    `Numeric(20, 8)` can hold was unreachable through the front door.

    MUTATION THIS CATCHES: `signed_payload["limit"] = str(data.limit)` (or any other
    re-spelling between the wire and `verify_signature`) in `app/core/trustlines/service.py`,
    in either `create` or `update`.
    """

    await _seed_equivalent(db_session)
    lender = await register_and_login(client, "Door_Smallest_Lender")
    borrower = await register_and_login(client, "Door_Smallest_Borrower")
    lender_key = SigningKey(base64.b64decode(lender["priv"]))

    limit = "0.00000001"
    created = await client.post(
        "/api/v1/trustlines",
        headers=lender["headers"],
        json={
            "to": borrower["pid"],
            "equivalent": EQUIVALENT,
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=lender_key,
                to_pid=borrower["pid"],
                equivalent=EQUIVALENT,
                limit=limit,
            ),
        },
    )
    assert created.status_code == 201, (
        f"a limit of {limit!r}, signed over exactly that string, was refused: {created.text}. "
        f"E005 here means the server signed a different spelling than the client sent."
    )

    trustline_id = created.json()["id"]
    updated_limit = "0.00000002"
    updated = await client.patch(
        f"/api/v1/trustlines/{trustline_id}",
        headers=lender["headers"],
        json={
            "limit": updated_limit,
            "signature": _sign_trustline_update_request(
                signing_key=lender_key,
                trustline_id=trustline_id,
                limit=updated_limit,
            ),
        },
    )
    assert updated.status_code == 200, (
        f"an updated limit of {updated_limit!r}, signed over exactly that string, was "
        f"refused: {updated.text}"
    )


@pytest.mark.asyncio
async def test_a_trustline_limit_the_column_holds_exactly_is_accepted_with_trailing_zeros(
    client: AsyncClient, db_session
) -> None:
    """`"100.0000000000"` is 100, and the ledger keeps 100. Signed for real, so it is a 201.

    Ten fraction digits is what `to_money_str` emits for an equivalent declaring
    `precision: 10`, and `Equivalent.precision` is declared `ge=0, le=18`. The first edition of
    the door refused this, i.e. refused its own renderer's output.

    MUTATION THIS CATCHES: a lexical `max_scale=MONEY_MAX_SCALE` in `parse_money_amount`.
    """

    await _seed_equivalent(db_session)
    lender = await register_and_login(client, "Door_TrailingZeros_Lender")
    borrower = await register_and_login(client, "Door_TrailingZeros_Borrower")

    limit = "100.0000000000"
    response = await client.post(
        "/api/v1/trustlines",
        headers=lender["headers"],
        json={
            "to": borrower["pid"],
            "equivalent": EQUIVALENT,
            "limit": limit,
            "signature": _sign_trustline_create_request(
                signing_key=SigningKey(base64.b64decode(lender["priv"])),
                to_pid=borrower["pid"],
                equivalent=EQUIVALENT,
                limit=limit,
            ),
        },
    )

    assert response.status_code == 201, (
        f"a limit of {limit!r} - the value 100, spelled to ten places - was refused: "
        f"{response.text}. `## Intended` licenses refusing the value that cannot be stored "
        f"exactly; 100 is not that value."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["0.123456789", "1000000000000"])
async def test_a_trustline_limit_the_column_cannot_hold_is_refused_before_the_signature(
    client: AsyncClient, db_session, limit: str
) -> None:
    """The half of the door that must NOT move: unstorable values still get E009, still first.

    `0.123456789` would be rounded by the column and `1000000000000` would overflow it. Both are
    refused with `E009` on a request whose signature is garbage, which is how we know the door
    ran before `verify_signature` - the order that keeps an unstorable amount from ever becoming
    a signed commitment.
    """

    await _seed_equivalent(db_session)
    lender = await register_and_login(client, f"Door_Refuse_{limit.replace('.', '_')}_L")
    borrower = await register_and_login(client, f"Door_Refuse_{limit.replace('.', '_')}_B")

    response = await client.post(
        "/api/v1/trustlines",
        headers=lender["headers"],
        json={
            "to": borrower["pid"],
            "equivalent": EQUIVALENT,
            "limit": limit,
            "signature": UNSIGNABLE,
        },
    )

    assert response.status_code == 400 and _error_code(response) == "E009", (
        f"limit {limit!r} must be refused by the money door (E009) before the signature check "
        f"(E005) is reached; got {response.status_code} {response.text}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["1e3", "1E+3", "1E-3", "0.1e1"])
async def test_the_payment_door_still_refuses_exponent_notation_written_by_a_client(
    client: AsyncClient, db_session, amount: str
) -> None:
    """The exponent decision, pinned where it is a decision.

    `amount` is a `str` on the wire and the signature covers it verbatim, so an accepted `"1e3"`
    is exponent-form money inside `transactions.payload` - stored history, which is the output
    side of the invariant after all. It is also what `T1209` has told 011 the canon must forbid
    with `pattern: ^-?\\d+(\\.\\d+)?$`.

    MUTATION THIS CATCHES: dropping the `"e" in lowered` guard in `parse_amount_decimal`, or
    extending the door's `Decimal` re-spelling to strings.
    """

    await _seed_equivalent(db_session)
    payer = await register_and_login(client, f"Door_Exp_{amount.replace('.', '_')}_P")
    payee = await register_and_login(client, f"Door_Exp_{amount.replace('.', '_')}_R")

    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=payer["headers"],
        json={
            "tx_id": tx_id,
            "to": payee["pid"],
            "equivalent": EQUIVALENT,
            "amount": amount,
            "signature": UNSIGNABLE,
        },
    )

    assert response.status_code == 400 and _error_code(response) == "E009", (
        f"amount {amount!r} is exponent notation written by the client and must be refused by "
        f"the door (E009) before anything is signed or stored; got {response.status_code} "
        f"{response.text}"
    )


@pytest.mark.asyncio
async def test_a_payment_amount_with_trailing_zeros_commits_and_is_not_renormalised(
    client: AsyncClient, db_session
) -> None:
    """`"0.100000000"` is 0.1, the column holds 0.1, so the payment must commit.

    The end-to-end form of the compatibility break: nine fraction digits, eight of which are the
    value and one of which is a zero, refused with 400/E009 by the first edition of the door.
    The response must echo the amount the sender signed - the door validates, it does not
    renormalise a number a signature already covers.

    MUTATION THIS CATCHES: a lexical `max_scale=MONEY_MAX_SCALE` in `parse_money_amount`.
    """

    await _seed_equivalent(db_session)
    payer = await register_and_login(client, "Door_PayTrailing_Payer")
    payee = await register_and_login(client, "Door_PayTrailing_Payee")

    payee_key = SigningKey(base64.b64decode(payee["priv"]))
    setup = await client.post(
        "/api/v1/trustlines",
        headers=payee["headers"],
        json={
            "to": payer["pid"],
            "equivalent": EQUIVALENT,
            "limit": "100.00",
            "signature": _sign_trustline_create_request(
                signing_key=payee_key,
                to_pid=payer["pid"],
                equivalent=EQUIVALENT,
                limit="100.00",
            ),
        },
    )
    assert setup.status_code == 201, f"setup failed: {setup.text}"

    amount = "0.100000000"
    tx_id = str(uuid.uuid4())
    response = await client.post(
        "/api/v1/payments",
        headers=payer["headers"],
        json={
            "tx_id": tx_id,
            "to": payee["pid"],
            "equivalent": EQUIVALENT,
            "amount": amount,
            "signature": _sign_payment_request(
                signing_key=SigningKey(base64.b64decode(payer["priv"])),
                tx_id=tx_id,
                from_pid=payer["pid"],
                to_pid=payee["pid"],
                equivalent=EQUIVALENT,
                amount=amount,
            ),
        },
    )

    assert response.status_code == 200, (
        f"a payment of {amount!r} - the value 0.1 - was not accepted: {response.text}"
    )
    body = response.json()
    assert body.get("status") == "COMMITTED", body
    assert body.get("amount") == amount, (
        f"the API answered with amount={body.get('amount')!r} for a signed {amount!r}. The door "
        f"must not restate a number the signature covers."
    )
