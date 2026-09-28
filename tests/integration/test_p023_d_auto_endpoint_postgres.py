"""Programme 023, slice (d): `POST /clearing/auto` through the common runner (spec decisions 7, 8, 10; R2, R3).

The production entry of the manual pass, end to end over HTTP on a disposable clone (`MODE_B`). The answer is the
exact `ClearingAutoResponse` of decision R3 (2026-09-28): every field present, money as exact decimal strings, the
committed occurrences with their identity and edges, `complete` only on an empty plan over a fresh snapshot,
`interrupted` with a reason otherwise. Outcomes walked here, each through a REAL path (Verification plan §6):

* success - two disjoint triangles, `V_edge` 15 and `V_cyc` 5 differ, `complete`;
* `max_depth` in ANY form - 422 before the runner starts, nothing cleared (R2); `/cycles` keeps it (control);
* the operator stop between occurrences - `200 interrupted`, `reason = error`, `error.code = "E008"`, the first
  occurrence reported, the second never started;
* an unexpected error after progress - `200 interrupted` with a SANITISED error (no raw text);
* an unexpected error before any commit - the existing contract: the error itself, nothing reported as cleared;
* cancellation after a commit - the progress is accounted (logged, durable) and the cancellation is preserved.

The operator stop BEFORE the first commit keeps `409`/E008: `test_p015_t1544_operator_stop_refuses_money.py`.

RED BEFORE THE SWITCH: `/auto` runs the v1 ladder and answers `{equivalent, cleared_cycles}`; every target
comparison below ends in `TargetMismatch` under the strict 023 marker.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.clearing.service import ClearingService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import debt_uuid, participant_uuid, ring, seed_graph
from tests.p023_support import (
    AUTO_RESPONSE_FIELDS,
    auto_clear_http,
    fresh_read,
    positive_debt_total,
    require_auto_progress,
    require_target,
)

pytestmark = MODE_B

CODE = "PQU"
T1 = ring(["p023ua", "p023ub", "p023uc"], ["2", "2", "2"], [debt_uuid(0x23D1, k) for k in range(3)])
T2 = ring(["p023ud", "p023ue", "p023uf"], ["3", "3", "3"], [debt_uuid(0x23D1, 10 + k) for k in range(3)])
TOTAL = Decimal("15")


async def _seed(db_session) -> None:
    await seed_graph(db_session, CODE, T1 + T2, precision=2)


async def _clearings(session) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.type == "CLEARING")
        )
    ).scalar_one()


def _spy_execute(monkeypatch, before_call):
    """Delegate to the real `execute_occurrence`, running `before_call(n)` first (n from 1)."""

    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append(occurrence)
        await before_call(len(calls))
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls


def _triangle_of(entry) -> str:
    ids = {edge["debt_id"] for edge in entry["edges"]}
    return "T1" if ids == {str(e.debt_id) for e in T1} else "T2" if ids == {str(e.debt_id) for e in T2} else "?"


# --------------------------------------------------------------------------------------------- success


@pytest.mark.asyncio
async def test_a_complete_pass_answers_the_exact_committed_progress_shape(db_session, client, auth_headers) -> None:
    await _seed(db_session)
    response = await auto_clear_http(client, auth_headers, CODE)
    assert response.status_code == 200, response.text
    body = response.json()
    committed = require_auto_progress(body)

    by_triangle = {_triangle_of(entry): entry for entry in committed}
    shape_ok = (
        set(body) == AUTO_RESPONSE_FIELDS
        and body["equivalent"] == CODE
        and body["status"] == "complete"
        and body["reason"] is None
        and body["error"] is None
        and body["cleared_cycles"] == len(committed) == 2
        and sorted(by_triangle) == ["T1", "T2"]
        # Money: exact decimal strings; V_edge = Σ|C|·c = 3·2 + 3·3, V_cyc = Σc = 2 + 3.
        and all(isinstance(body[key], str) for key in ("v_edge", "v_cyc", "remaining_v_edge"))
        and (Decimal(body["v_edge"]), Decimal(body["v_cyc"])) == (Decimal(15), Decimal(5))
        and body["remaining_cycles"] == 0
        and Decimal(body["remaining_v_edge"]) == 0
    )
    require_target(shape_ok, f"/auto answer {body!r}")

    for name, cycle in (("T1", T1), ("T2", T2)):
        entry = by_triangle[name]
        assert set(entry) == {"occurrence_id", "plan_id", "ordinal", "amount", "edges", "after_cancellation"}
        assert Decimal(entry["amount"]) == Decimal(cycle[0].amount) and "e" not in entry["amount"].lower()
        assert entry["after_cancellation"] is False
        edges = {e["debt_id"]: e for e in entry["edges"]}
        for debt in cycle:
            # The runner's progress edge is debtor -> creditor, by participant UUID.
            assert edges[str(debt.debt_id)]["debtor_id"] == str(participant_uuid(debt.debtor))
            assert edges[str(debt.debt_id)]["creditor_id"] == str(participant_uuid(debt.creditor))
    stored = await fresh_read(db_session, lambda s: s.execute(select(Transaction.tx_id).where(Transaction.type == "CLEARING")))
    assert sorted(stored.scalars().all()) == sorted(entry["occurrence_id"] for entry in committed)
    assert await fresh_read(db_session, positive_debt_total, CODE) == 0


# ---------------------------------------------------------------------------------------- max_depth (R2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        pytest.param("&max_depth", id="bare"),
        pytest.param("&max_depth=", id="empty"),
        pytest.param("&max_depth=6", id="valid"),
        pytest.param("&max_depth=6&max_depth=7", id="repeated"),
        pytest.param("&max_depth=abc", id="not_an_int"),
        pytest.param("&max_depth=99", id="out_of_range"),
    ],
)
async def test_max_depth_in_any_form_is_refused_before_the_runner(db_session, client, auth_headers, monkeypatch, query) -> None:
    await _seed(db_session)
    calls = _spy_execute(monkeypatch, lambda n: asyncio.sleep(0))

    response = await auto_clear_http(client, auth_headers, CODE, query)

    body = response.json()
    errors = (((body.get("error") or {}).get("details") or {}).get("errors") or []) if isinstance(body, dict) else []
    explained = any(
        "max_depth" in str(err.get("loc")) and "removed" in str(err.get("msg", "")).lower() and "/cycles" in str(err.get("msg", ""))
        for err in errors
    )
    untouched = calls == [] and await fresh_read(db_session, positive_debt_total, CODE) == TOTAL
    assert untouched or response.status_code == 200, (response.status_code, body)
    require_target(
        response.status_code == 422 and (body.get("error") or {}).get("code") == "E009" and explained and untouched,
        f"{query!r}: {response.status_code} {body!r}",
    )


@pytest.mark.asyncio
async def test_counter_check_the_diagnostic_cycles_keeps_max_depth(db_session, client, auth_headers) -> None:
    await _seed(db_session)
    response = await client.get(f"/api/v1/clearing/cycles?equivalent={CODE}&max_depth=3", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["cycles"]) == 2
    bad = await client.get(f"/api/v1/clearing/cycles?equivalent={CODE}&max_depth=99", headers=auth_headers)
    assert bad.status_code == 422, bad.text


# ------------------------------------------------------------------------------------ error after progress


async def _stop_the_equivalent(factory) -> None:
    async with factory() as session:
        await session.execute(update(Equivalent).where(Equivalent.code == CODE).values(is_active=False))
        await session.commit()


@pytest.mark.asyncio
async def test_the_operator_stop_after_progress_answers_interrupted_with_e008(db_session, client, auth_headers, monkeypatch) -> None:
    await _seed(db_session)
    factory = sessionmaker_of(db_session)

    async def before(n: int) -> None:
        if n == 2:
            await _stop_the_equivalent(factory)

    calls = _spy_execute(monkeypatch, before)
    response = await auto_clear_http(client, auth_headers, CODE)
    body = response.json()
    require_target(response.status_code == 200 and isinstance(body, dict) and "status" in body, f"{response.status_code} {body!r}")
    committed = require_auto_progress(body)

    assert len(calls) == 2, "control: the second occurrence reached the boundary, which refused it"
    assert body["status"] == "interrupted" and body["reason"] == "error", body
    assert body["error"]["code"] == "E008" and body["error"]["details"]["reason"] == "equivalent_inactive", body
    assert body["cleared_cycles"] == len(committed) == 1
    assert body["remaining_cycles"] == 1 and Decimal(body["remaining_v_edge"]) > 0
    assert await fresh_read(db_session, _clearings) == 1
    left = await fresh_read(db_session, positive_debt_total, CODE)
    assert left == TOTAL - Decimal(body["v_edge"]) and left > 0


def _internal_failure(kind: str) -> Exception:
    """An internal failure: a bare exception, or a GeoException with the internal code E010 and a private message
    (the shape the retired `auto_clear` sanitised - `test_clearing_additional_cases.py`, removed 2026-09-28)."""

    from app.utils.exceptions import GeoException

    return RuntimeError("raw clearing secret") if kind == "raw" else GeoException("raw clearing secret")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["raw", "geo_e010"])
async def test_an_unexpected_error_after_progress_is_reported_sanitised(db_session, client, auth_headers, monkeypatch, kind) -> None:
    await _seed(db_session)

    async def before(n: int) -> None:
        if n == 2:
            raise _internal_failure(kind)

    calls = _spy_execute(monkeypatch, before)
    response = await auto_clear_http(client, auth_headers, CODE)
    body = response.json()
    require_target(response.status_code == 200 and isinstance(body, dict) and "status" in body, f"{response.status_code} {body!r}")
    committed = require_auto_progress(body)

    assert len(calls) == 2
    assert body["status"] == "interrupted" and body["reason"] == "error" and len(committed) == 1, body
    assert body["error"] == {"code": "E010", "message": "Internal server error", "details": None}, body
    assert "raw clearing secret" not in response.text
    assert await fresh_read(db_session, _clearings) == 1


@pytest.mark.asyncio
async def test_an_unexpected_error_before_any_commit_is_the_error_itself(db_session, client, auth_headers, monkeypatch) -> None:
    await _seed(db_session)

    async def before(n: int) -> None:
        raise RuntimeError("raw clearing secret")

    calls = _spy_execute(monkeypatch, before)
    raised = None
    try:
        response = await auto_clear_http(client, auth_headers, CODE)
    except RuntimeError as error:  # the test client re-raises what the server answers as a bare 500
        raised = error
        response = None
    require_target(
        raised is not None and str(raised) == "raw clearing secret",
        f"expected the unhandled error (a sanitised 500 in production); got {getattr(response, 'status_code', None)}",
    )
    assert len(calls) == 1
    assert await fresh_read(db_session, _clearings) == 0
    assert await fresh_read(db_session, positive_debt_total, CODE) == TOTAL


# ------------------------------------------------------------------------------------ cancellation


@pytest.mark.asyncio
async def test_a_cancelled_request_accounts_its_progress_and_stays_cancelled(db_session, client, auth_headers, monkeypatch, caplog) -> None:
    await _seed(db_session)
    second_started = asyncio.Event()

    async def before(n: int) -> None:
        if n == 2:
            second_started.set()
            await asyncio.sleep(60)

    calls = _spy_execute(monkeypatch, before)
    caplog.set_level(logging.INFO)
    request = asyncio.create_task(auto_clear_http(client, auth_headers, CODE))
    waiter = asyncio.create_task(second_started.wait())
    await asyncio.wait({request, waiter}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    reached = second_started.is_set()
    request.cancel()
    cancelled = False
    try:
        await request
    except asyncio.CancelledError:
        cancelled = True
    require_target(reached and cancelled, f"the second occurrence was reached: {reached}; cancelled: {cancelled}")

    assert len(calls) == 2
    assert await fresh_read(db_session, _clearings) == 1, "the first occurrence is durable"
    records = [r.getMessage() for r in caplog.records if "event=clearing.auto.cancelled" in r.getMessage()]
    assert records and "committed=1" in records[-1], records


@pytest.mark.asyncio
async def test_an_internal_geo_error_before_any_commit_is_the_sanitised_envelope(db_session, client, auth_headers, monkeypatch) -> None:
    """The E010 GeoException before any commit: the bare internal envelope, its private message only in the log."""

    await _seed(db_session)

    async def before(n: int) -> None:
        raise _internal_failure("geo_e010")

    _spy_execute(monkeypatch, before)
    response = await auto_clear_http(client, auth_headers, CODE)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "E010"
    assert response.json()["error"]["message"] == "Internal server error"
    assert "raw clearing secret" not in response.text
    assert await fresh_read(db_session, _clearings) == 0
