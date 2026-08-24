"""RT-012-2: money the system accepts and stores is reported as zero, because two stages disagree.

WHY THIS MODULE EXISTS. `F-012-2` says the money modules never read `Equivalent.precision` - zero
occurrences in `app/core/payments/`, `app/core/clearing/`, `app/core/trustlines/` and
`app/core/balance/`. On its own that is an absence, and an absence is not a defect. This
reproducer turns it into an observable one by naming the two concrete stages that then disagree
about what a unit of money is, on a configuration the repository ships:

  * **the door** - `app/core/payments/service.py:504` calls `parse_amount_decimal`
    (`app/utils/validation.py:106`), which takes no equivalent and consults no precision. It
    admits `0.05` for any equivalent, because scale 2 is far below `DEFAULT_MAX_AMOUNT_SCALE`
    (18). `Numeric(20, 8)` then stores it faithfully as `0.05000000`.
  * **the wire** - `app/core/simulator/edge_patch_builder.py:66-70` derives
    `money_quant = 1 / 10**precision` from `Equivalent.precision` and emits
    `format(v.quantize(money_quant, rounding=ROUND_DOWN), "f")`. At `precision: 1` that quantum
    is `0.1`, and `Decimal("0.05").quantize(Decimal("0.1"), ROUND_DOWN)` is `0`.

`precision: 1` is not hypothetical: `seeds/equivalents.json:8-13` ships `HOUR` with it. So a real
payment of `0.05 HOUR` is accepted, signed, committed and stored - and the graph the participant
looks at reports `used: "0.0"`. The debt exists and is invisible.

WHAT IT MEASURED (2026-08-24, HEAD e45e721, PostgreSQL 16.9, `geov0_test_ci`, migrated schema
`019_trust_lines_partial_unique_live`): `POST /api/v1/payments` with `amount: "0.05"` on a
precision-1 equivalent returns 200 `COMMITTED`; `SELECT amount::text FROM debts` returns
`0.05000000`; `EdgePatchBuilder.build_edge_patch_for_equivalent` returns `used == "0.0"` and
`available` equal to the full limit. The same amount on a precision-2 equivalent renders
`"0.05"`, which is the counter-check: the formatter is precision-driven and the test reacts to
`precision` being substituted, so it is not merely restating a constant.

`HOUR` IS CREATED BY THE TEST. `tests/conftest.py` builds the schema and nothing else - it never
reads `seeds/equivalents.json` (verified: no occurrence of `seed` anywhere in that file, and
`_ensure_schema_initialized` at `tests/conftest.py:120-138` either asserts the alembic head or
runs `drop_all`/`create_all`). The claim is re-asserted at runtime below rather than trusted,
because a reproducer that silently reused a seeded `HOUR` would be measuring someone else's
fixture.

WHAT THE FIX IS EXPECTED TO DO (T1201 + T1202, slice B). Either end closes it, and the assertion
is written as that disjunction so that either one turns it green without an edit here: the door
learns the equivalent's precision and refuses `0.05` for a precision-1 unit with a 4xx, **or** the
value that was accepted and stored is reported faithfully instead of being floored to zero. What
must not happen is the third option - accept it, store it, and show zero.

Not asserted here: which rounding mode is right. `ROUND_DOWN` on this path against PostgreSQL's
half-up on storage is a two-rounding-modes question the spec routes to 015, and this module only
requires that an accepted, stored, positive obligation is not rendered as nothing.
"""

from __future__ import annotations

import base64
import logging
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import delete, select, text

from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.validation import DEFAULT_MAX_AMOUNT_SCALE, parse_amount_decimal

from tests.integration.p012_pg_http import make_pg_client_fixture
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

pytestmark = pytest.mark.postgres


# The shipped configuration this reproducer is about: seeds/equivalents.json:8-13.
SHIPPED_PRECISION_1_CODE = "HOUR"

# Representable at scale 8, so storage keeps it exactly; NOT representable at precision 1, so the
# wire formatter floors it away. Chosen because `0.1` - the smallest valid amount at precision 1 -
# is what the external review used to refute RT-012-3, and `0.05` is the first value below it.
UNREPRESENTABLE_AT_PRECISION_1 = "0.05"

TRUSTLINE_LIMIT = "10.00"

_WHY = (
    "WHY THIS MATTERS: the door and the wire disagree about what one unit of this equivalent is, "
    "and the disagreement is silent in the direction that hides money. A participant is shown a "
    "graph in which an obligation the ledger holds does not exist. Every downstream reading of "
    "that patch - available capacity, whether an edge is saturated, whether clearing is worth "
    "running - is computed from the number that was floored away. Do not fix this by changing "
    "the assertion to the current output: that would encode the erasure as the contract."
)


# The same harness RT-012-1 uses: on PostgreSQL the shared `client` fixture cannot drive the
# payment path at all. See `tests/integration/p012_pg_http.py` for the measurement.
pg_client = make_pg_client_fixture()


class _Scenario(dict):
    pass


@pytest_asyncio.fixture
async def scenario_factory(pg_client: AsyncClient):
    """Builds committed sender/receiver/equivalent/trustline sets and cleans them up.

    Rows are really written (see the fixture note in the RT-012-1 module), so every id created
    here is tracked and removed at teardown.
    """

    from tests.conftest import TestingSessionLocal

    created: list[_Scenario] = []

    async def _build(*, code: str, precision: int, label: str) -> _Scenario:
        async with TestingSessionLocal() as session:
            existing = (
                await session.execute(select(Equivalent).where(Equivalent.code == code))
            ).scalar_one_or_none()
            assert existing is None, (
                f"the equivalent {code!r} already exists in the test database with precision "
                f"{getattr(existing, 'precision', None)!r}. This reproducer must create it "
                f"itself: tests/conftest.py never loads seeds/equivalents.json, so a pre-existing "
                f"row means some other test committed one and this module would be measuring "
                f"that fixture instead of the shipped configuration."
            )
            equivalent = Equivalent(code=code, description=f"RT-012-2 {label}", precision=precision)
            session.add(equivalent)
            await session.commit()
            equivalent_id = equivalent.id

        nonce = uuid.uuid4().hex[:8]
        sender = await register_and_login(pg_client, f"RT2_Sender_{label}_{nonce}")
        receiver = await register_and_login(pg_client, f"RT2_Receiver_{label}_{nonce}")

        receiver_key = SigningKey(base64.b64decode(receiver["priv"]))
        response = await pg_client.post(
            "/api/v1/trustlines",
            headers=receiver["headers"],
            json={
                "to": sender["pid"],
                "equivalent": code,
                "limit": TRUSTLINE_LIMIT,
                "signature": _sign_trustline_create_request(
                    signing_key=receiver_key,
                    to_pid=sender["pid"],
                    equivalent=code,
                    limit=TRUSTLINE_LIMIT,
                ),
            },
        )
        assert response.status_code == 201, (
            f"setup failed for {code}: without capacity the payment below would be rejected for "
            f"the wrong reason. {response.text}"
        )

        async with TestingSessionLocal() as session:
            rows = (
                await session.execute(
                    text("SELECT id, pid FROM participants WHERE pid = ANY(:pids)"),
                    {"pids": [sender["pid"], receiver["pid"]]},
                )
            ).all()

        built = _Scenario(
            code=code,
            precision=precision,
            equivalent_id=equivalent_id,
            sender=sender,
            receiver=receiver,
            participants=[(r[0], r[1]) for r in rows],
        )
        created.append(built)
        return built

    try:
        yield _build
    finally:
        async with TestingSessionLocal() as cleanup:
            for built in created:
                ids = [pid for pid, _ in built["participants"]]
                await cleanup.execute(
                    delete(Debt).where(Debt.equivalent_id == built["equivalent_id"])
                )
                await cleanup.execute(
                    delete(TrustLine).where(TrustLine.equivalent_id == built["equivalent_id"])
                )
                if ids:
                    await cleanup.execute(
                        text("DELETE FROM prepare_locks WHERE participant_id = ANY(:ids)"),
                        {"ids": ids},
                    )
                    await cleanup.execute(
                        text(
                            "DELETE FROM integrity_audit_log WHERE tx_id IN "
                            "(SELECT tx_id FROM transactions WHERE initiator_id = ANY(:ids))"
                        ),
                        {"ids": ids},
                    )
                    await cleanup.execute(
                        text("DELETE FROM transactions WHERE initiator_id = ANY(:ids)"),
                        {"ids": ids},
                    )
                    await cleanup.execute(delete(Participant).where(Participant.id.in_(ids)))
                await cleanup.execute(
                    delete(Equivalent).where(Equivalent.id == built["equivalent_id"])
                )
            await cleanup.commit()


async def _pay(pg_client: AsyncClient, built: _Scenario, amount: str) -> tuple[int, dict[str, Any]]:
    sender = built["sender"]
    receiver = built["receiver"]
    sender_key = SigningKey(base64.b64decode(sender["priv"]))
    tx_id = str(uuid.uuid4())
    response = await pg_client.post(
        "/api/v1/payments",
        headers=sender["headers"],
        json={
            "tx_id": tx_id,
            "to": receiver["pid"],
            "equivalent": built["code"],
            "amount": amount,
            "signature": _sign_payment_request(
                signing_key=sender_key,
                tx_id=tx_id,
                from_pid=sender["pid"],
                to_pid=receiver["pid"],
                equivalent=built["code"],
                amount=amount,
            ),
        },
    )
    try:
        body = response.json()
    except ValueError:  # pragma: no cover - defensive
        body = {"_raw": response.text}
    return response.status_code, body


async def _edge_patch(built: _Scenario) -> list[dict[str, Any]]:
    """The real backend-authoritative edge patch - the shape the simulator pushes over SSE."""

    from tests.conftest import TestingSessionLocal

    run = RunRecord(
        run_id=f"rt2-{uuid.uuid4().hex[:8]}",
        scenario_id="rt2",
        mode="real",
        state="running",
    )
    run._real_participants = list(built["participants"])

    builder = EdgePatchBuilder(logger=logging.getLogger("test.p012.edge_patch"))
    async with TestingSessionLocal() as session:
        return await builder.build_edge_patch_for_equivalent(
            session=session, run=run, equivalent_code=built["code"]
        )


async def _stored_debt(equivalent_id: uuid.UUID) -> list[str]:
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT amount::text FROM debts WHERE equivalent_id = :eq"),
                {"eq": equivalent_id},
            )
        ).all()
    return [r[0] for r in rows]


async def test_rt_012_2_an_amount_accepted_and_stored_is_reported_as_zero_on_the_wire(
    pg_client: AsyncClient, scenario_factory
) -> None:
    """`HOUR` has `precision: 1`. `0.05` is admitted, stored, and then rendered as `0.0`."""

    # Stage 0 - the door does not know the equivalent exists. Stated as a measurement, because
    # the whole finding rests on it: `parse_amount_decimal` has no equivalent parameter at all.
    assert parse_amount_decimal(UNREPRESENTABLE_AT_PRECISION_1, require_positive=True) == Decimal(
        UNREPRESENTABLE_AT_PRECISION_1
    ), (
        f"parse_amount_decimal no longer accepts {UNREPRESENTABLE_AT_PRECISION_1!r} at the "
        f"default max_scale ({DEFAULT_MAX_AMOUNT_SCALE}); the door has changed and stage 1 of "
        f"this reproducer must be re-measured before the rest is trusted."
    )

    built = await scenario_factory(
        code=SHIPPED_PRECISION_1_CODE, precision=1, label="hour"
    )

    status_code, body = await _pay(pg_client, built, UNREPRESENTABLE_AT_PRECISION_1)
    stored = await _stored_debt(built["equivalent_id"])

    if 400 <= status_code < 500:
        # The post-fix world: the door consulted Equivalent.precision and refused an amount this
        # unit cannot express. Nothing was stored, so nothing can be misreported.
        assert stored == [], (
            f"the API refused {UNREPRESENTABLE_AT_PRECISION_1!r} on a precision-1 equivalent "
            f"with {status_code}, but a debt row remains ({stored!r}).\n{_WHY}"
        )
        return

    assert status_code == 200 and body.get("status") == "COMMITTED", (
        f"stage 1 did not behave as measured: HTTP {status_code} {body!r}. The reproducer needs "
        f"the payment either committed (today) or refused with a 4xx (after the fix); anything "
        f"else means the chain broke somewhere unrelated to precision."
    )
    assert stored == ["0.05000000"], (
        f"stage 2 did not behave as measured: Numeric(20, 8) holds {stored!r} rather than "
        f"'0.05000000'. Storage is not where this value is lost, and if that changed the "
        f"diagnosis below is wrong."
    )

    patches = await _edge_patch(built)
    assert len(patches) == 1, (
        f"expected exactly one edge patch for {built['code']}, got {patches!r}; without it the "
        f"assertion below would be vacuous."
    )
    patch = patches[0]

    assert Decimal(patch["used"]) == Decimal(stored[0]), (
        f"a payment of {UNREPRESENTABLE_AT_PRECISION_1!r} was accepted by the API "
        f"(app/core/payments/service.py:504 -> parse_amount_decimal, which never reads "
        f"Equivalent.precision) and PostgreSQL holds {stored[0]!r}, but the edge patch reports "
        f"used={patch['used']!r} and available={patch['available']!r} out of a limit of "
        f"{TRUSTLINE_LIMIT!r}. `app/core/simulator/edge_patch_builder.py:66-70` quantises with "
        f"money_quant = 1/10**precision = 0.1 and rounding=ROUND_DOWN, so the whole obligation "
        f"is floored to zero. The two stages disagree about what one unit of "
        f"{built['code']} is, and the disagreement destroys money in the direction nobody "
        f"audits.\n{_WHY}"
    )


async def test_rt_012_2_counter_check_the_same_amount_at_precision_2_is_reported_faithfully(
    pg_client: AsyncClient, scenario_factory
) -> None:
    """Substituting `precision` must change the output, or the reproducer proves nothing.

    Section 4 of the verification plan forbids a formatter test that carries its own expected
    two-digit string. This is the parametrised half: the identical amount, the identical code
    path, only `Equivalent.precision` differs, and the reported value flips from `0.0` to
    `0.05`. It also pins the spec's «правка не меняет уже корректные величины» counter-check -
    a precision-2 equivalent must keep rendering byte-for-byte what it renders today.
    """

    built = await scenario_factory(
        code=f"HR{uuid.uuid4().hex[:6]}".upper()[:16], precision=2, label="p2"
    )

    status_code, body = await _pay(pg_client, built, UNREPRESENTABLE_AT_PRECISION_1)
    assert status_code == 200 and body.get("status") == "COMMITTED", (
        f"0.05 is exactly representable at precision 2 and must commit; got {status_code} "
        f"{body!r}. Until it does, the contrast this counter-check draws is meaningless."
    )

    patches = await _edge_patch(built)
    assert len(patches) == 1, f"expected one edge patch, got {patches!r}"
    assert patches[0]["used"] == "0.05", (
        f"at precision 2 the same 0.05 must reach the wire as '0.05'; it came out as "
        f"{patches[0]['used']!r}. The formatter is supposed to be driven by "
        f"Equivalent.precision, so if substituting precision does not change the output, the "
        f"red result in this module is not caused by precision and its diagnosis is wrong."
    )


async def test_rt_012_2_the_other_producer_in_the_same_file_puts_exponential_money_on_the_wire(
    pg_client: AsyncClient, scenario_factory
) -> None:
    """`edge_patch_builder.py` contains two money producers, and only one of them is safe.

    The spec holds `edge_patch_builder.py:69-70` up as the correct form already present in the
    repository - it quantises by `Equivalent.precision` and `format(..., "f")` forbids exponent
    notation. The second producer in the same file,
    `build_edge_patch_for_pairs` (`:173`, the per-transaction and `clearing.done` patch
    path), emits `"used": str(used_amt)` and `"available": str(available_amt)` at `:273-274`.
    It has `helper.precision` in hand (`VizPatchHelper.precision`) and uses neither it nor
    `format(..., "f")`.

    That is reachable, not theoretical. `available` is computed as `limit - used`; when a
    trustline is one storage quantum short of fully used, PostgreSQL returns
    `Decimal('100.00000000')` and `Decimal('99.99999999')`, whose difference is
    `Decimal('1E-8')` - and `str()` of that is the literal text `1E-8`. Measured 2026-08-24: the
    patch carries `available: '1E-8'`.

    A precision-2 equivalent is used deliberately, so the failure below is about exponent
    notation alone and is not confounded with the precision erasure of the first test.

    The invariant list also asks for a value whose `str(Decimal)` yields `E+`. On the backend
    that spelling is not reachable from storage - every value read out of `Numeric(20, 8)` has
    exponent -8, and subtraction cannot raise it above zero - so the `E+` case is exercised
    against the two formatting functions directly, below, where it shows exactly which of the
    two upholds the invariant.
    """

    # Guard the guard: `str()` really is the unsafe function, on both signs of the exponent.
    assert str(Decimal("1E+1")) == "1E+1", (
        "the E+ probe value no longer stringifies exponentially, so this check has stopped "
        "modelling the hazard and must be rewritten rather than deleted."
    )
    money_quant_p1 = Decimal(1) / (Decimal(10) ** 1)
    assert format(Decimal("1E+1").quantize(money_quant_p1, rounding=ROUND_DOWN), "f") == "10.0", (
        "the form the spec calls correct (edge_patch_builder.py:69-70) must render an "
        "exponentially-spelled Decimal as plain text; if it does not, the 'правильная форма уже "
        "есть' premise of F-012-9 is wrong and the whole remediation plan needs re-basing."
    )

    from tests.conftest import TestingSessionLocal

    built = await scenario_factory(
        code=f"XP{uuid.uuid4().hex[:6]}".upper()[:16], precision=2, label="exp"
    )
    # Orient to the live trustline rather than guessing which registered participant is which.
    async with TestingSessionLocal() as session:
        row = (
            await session.execute(
                select(TrustLine.from_participant_id, TrustLine.to_participant_id).where(
                    TrustLine.equivalent_id == built["equivalent_id"]
                )
            )
        ).one()
        creditor_id, debtor_id = row[0], row[1]
        pid_by_id = {pid_uuid: pid for pid_uuid, pid in built["participants"]}
        creditor_pid, debtor_pid = pid_by_id[creditor_id], pid_by_id[debtor_id]

        # One storage quantum short of a fully-used line.
        await session.execute(
            text('UPDATE trust_lines SET "limit" = :v WHERE equivalent_id = :eq'),
            {"v": Decimal("100.00000000"), "eq": built["equivalent_id"]},
        )
        session.add(
            Debt(
                debtor_id=debtor_id,
                creditor_id=creditor_id,
                equivalent_id=built["equivalent_id"],
                amount=Decimal("99.99999999"),
            )
        )
        await session.commit()

    async with TestingSessionLocal() as session:
        helper = await VizPatchHelper.create(session, equivalent_code=built["code"])
        participants = {
            creditor_pid: (
                await session.execute(
                    select(Participant).where(Participant.id == creditor_id)
                )
            ).scalar_one(),
            debtor_pid: (
                await session.execute(select(Participant).where(Participant.id == debtor_id))
            ).scalar_one(),
        }
        builder = EdgePatchBuilder(logger=logging.getLogger("test.p012.edge_patch"))
        patches = await builder.build_edge_patch_for_pairs(
            session=session,
            helper=helper,
            edges_pairs=[(creditor_pid, debtor_pid)],
            pid_to_participant=participants,
        )

    assert len(patches) == 1, (
        f"expected one pair patch, got {patches!r}; without it the assertion below is vacuous."
    )
    patch = patches[0]

    assert patch["used"] == "99.99999999", (
        f"setup drifted: used came out as {patch['used']!r}, so the subtraction below is not the "
        f"one that was measured."
    )
    for field in ("used", "available"):
        assert "e" not in patch[field].lower(), (
            f"the per-transaction edge patch put {field}={patch[field]!r} on the wire - money in "
            f"exponential notation. This is `str(Decimal)` at "
            f"app/core/simulator/edge_patch_builder.py:273-274, in the same file whose other "
            f"producer (`:69-70`) is the form the spec calls correct. Every client that parses "
            f"this as a plain decimal string sees a malformed value or, worse, silently reads "
            f"it as a different number. `helper.precision` ({helper.precision}) is available at "
            f"that call site and is not used, so this producer also ignores the equivalent's "
            f"precision entirely: at precision {helper.precision} the correct rendering is "
            f"{format(Decimal(patch[field]).quantize(Decimal(1) / (Decimal(10) ** helper.precision), rounding=ROUND_DOWN), 'f')!r}.\n"
            f"{_WHY}"
        )
