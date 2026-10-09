"""028 E6: conflicts answered in the shape the canon declares.

* F-028-6: an Interact update/close whose line another writer closes while the action waits for its row lock answers
  the flat 409 `TRUSTLINE_CLOSED` (`SimulatorActionError`). REAL SCHEDULE (no injected SQLSTATE): a second connection
  closes the line and holds the row; the action blocks on `FOR UPDATE` and reads `closed` after the commit. Mode B.
* F-028-20: `POST /clearing/auto` with clearing off is 409/E008 `clearing_disabled`. `T2864`: resume/restart declare 409.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, text

from app.config import settings
from app.core.trustlines.service import TrustLineService
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import register_and_login
from tests.unit.test_p021_interact_trust_line_actions_wire import TRIPLE, _post, stand  # noqa: F401 - fixture

CANON = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"


#: An upper bound that only guards against a hang, never a synchronisation: the wait below ends the moment the
#: condition holds, however slow the machine is.
_GUARD_SECONDS = 60.0

#: LIVE sources only (`pg_locks`, `pg_blocking_pids`). The previous observer read `pg_stat_activity`, which a backend
#: snapshots ONCE PER TRANSACTION: its probe session stays in one transaction, so a waiter that appeared after the
#: first poll stayed invisible for the whole window ("the action never waited", about 4 runs in 40 on an idle
#: machine, 035 p028-e6). The waiter must be blocked BY the holder and hold the tuple lock of the line's row version.
_BLOCKED_ON_THIS_ROW_BY_THE_HOLDER = text(
    """
    select count(*)
    from pg_locks l
    where l.locktype = 'tuple' and l.relation = 'trust_lines'::regclass
      and l.page = :page and l.tuple = :tuple
      and :holder = any(pg_blocking_pids(l.pid))
    """
)

_WHO_HOLDS_WHAT = text(
    """
    select l.pid, l.locktype, l.relation::regclass::text as relation, l.page, l.tuple, l.granted,
           pg_blocking_pids(l.pid) as blocked_by
    from pg_locks l
    where l.locktype in ('tuple', 'transactionid') and l.pid <> pg_backend_pid()
    order by l.pid
    """
)


async def _wait_until_blocked_on_the_row(sessions, *, holder_pid: int, ctid: str, request: "asyncio.Task") -> None:
    """Return when a backend is blocked by `holder_pid` on the tuple `ctid` of `trust_lines`; fail loudly otherwise.

    The wait is the condition, polled as fast as the round trip allows (no sleep as synchronisation). `request` is
    the action's task: while the holder has not committed it cannot legitimately finish, so a finished request
    means it never waited for the row, and that is reported at once rather than after the guard.
    """

    page, tuple_ = (int(part) for part in ctid.strip("()").split(","))
    deadline = asyncio.get_running_loop().time() + _GUARD_SECONDS
    async with sessions() as probe:
        while not await probe.scalar(_BLOCKED_ON_THIS_ROW_BY_THE_HOLDER,
                                     {"holder": holder_pid, "page": page, "tuple": tuple_}):
            if request.done():
                outcome = f"raised {request.exception()!r}" if request.exception() else request.result().text
                raise AssertionError(
                    f"the action finished before the holder committed, so it never waited for the line's row lock "
                    f"(tuple {ctid} of trust_lines): {outcome}")
            if asyncio.get_running_loop().time() >= deadline:
                held = [tuple(row) for row in (await probe.execute(_WHO_HOLDS_WHAT)).all()]
                raise AssertionError(
                    f"no backend was blocked by backend {holder_pid} on tuple {ctid} of trust_lines within "
                    f"{_GUARD_SECONDS:.0f} s; row/transaction locks now (pid, type, relation, page, tuple, granted, "
                    f"blocked by): {held}")
            await asyncio.sleep(0)


async def _settle(pending: "asyncio.Task", other) -> None:
    """Whatever happened, end the holder's transaction (the lock), then wait for the request - never leave it running."""

    await other.rollback()  # a no-op after the commit; on a failure path it releases the row so the request can end
    if not pending.done():
        try:
            await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=_GUARD_SECONDS)
        except asyncio.TimeoutError:  # wait_for has cancelled it; the primary failure is what the test reports
            pass


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("action,body", [("trustline-update", {"new_limit": "5"}), ("trustline-close", {})])
async def test_a_line_closed_while_the_action_waits_answers_the_flat_conflict(client, stand, action, body) -> None:
    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200
    sessions = sessionmaker_of(stand["db"])
    async with sessions() as other:
        line = (await other.execute(select(TrustLine))).scalar_one()
        # The version of the row the action will queue behind: read before the holder updates it.
        ctid = await other.scalar(select(text("ctid::text")).select_from(TrustLine).where(TrustLine.id == line.id))
        holder_pid = await other.scalar(text("select pg_backend_pid()"))
        service = TrustLineService(other)
        batch = service.begin_internal_batch()
        await service.execute_close(batch, line.id, line.from_participant_id,
                                    TrustLineCloseRequest(signature="__internal__"), require_signature=False)
        await batch.finish()
        pending = asyncio.create_task(_post(client, action, {**TRIPLE, **body}))
        try:
            await _wait_until_blocked_on_the_row(sessions, holder_pid=holder_pid, ctid=ctid, request=pending)
            await other.commit()
            try:
                resp = await pending
            except Exception as exc:  # a stale decision surfaces here as the database's own refusal
                raise AssertionError(
                    f"the action raised {exc!r} instead of answering the flat 409 after its row lock") from exc
        finally:
            await _settle(pending, other)
    # A 200 here is the action deciding on the row it read BEFORE the wait: the lock did not come first (F-028-6).
    assert resp.status_code == 409, f"the action did not re-read the line after its row lock: {resp.text}"
    assert resp.json()["code"] == "TRUSTLINE_CLOSED" and "error" not in resp.json(), resp.text


@pytest.mark.asyncio
async def test_clearing_switched_off_is_a_declared_conflict(client, db_session, monkeypatch) -> None:
    user = await register_and_login(client, "E6Clearing")
    monkeypatch.setattr(settings, "CLEARING_ENABLED", False)
    resp = await client.post("/api/v1/clearing/auto", params={"equivalent": "UAH"}, headers=user["headers"])
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("E008", "clearing_disabled"), error


def test_resume_and_restart_declare_their_conflict() -> None:
    paths = yaml.safe_load(CANON.read_text(encoding="utf-8"))["paths"]
    for op in ("resume", "restart"):
        assert "409" in paths[f"/simulator/runs/{{run_id}}/{op}"]["post"]["responses"], op
