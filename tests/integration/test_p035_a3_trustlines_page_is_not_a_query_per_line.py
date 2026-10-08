"""035 A3 (`F-035-2`): a page of trust lines costs a fixed number of statements, not four per line.

`TrustLineService.get_by_participant` reads the page and then hydrates each line on its own
(`_hydrate_trustline`: equivalent, both participants, the debt). Counted on the service alone - no auth, no HTTP -
with `after_cursor_execute` on the session's connection, on 50 active lines the session has not loaded.

What this does not see: the byte equality of `GET /trustlines` before and after (the comparison stand of the fix),
and the filtered branch (`equivalent=`), which adds one statement of its own.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLine as TrustLineSchema
from tests.debt_setup import debt_fixture_setup

_LINES = 50
_STATEMENTS_ALLOWED = 3
_INDEBTED = 5  # the first lines carry a debt, so `used` is not zero on every row


def _participant(pid: str, n: str) -> Participant:
    return Participant(pid=f"{pid}_{n}", display_name=pid, public_key=f"pk{pid}-{n}", type="person",
                       status="active", profile={})


@pytest.mark.asyncio
async def test_fifty_fresh_active_lines_are_read_in_a_fixed_number_of_statements(db_session):
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"TL{n}", symbol="TL", description=None, precision=2, metadata_={}, is_active=True)
    me = _participant("ME", n)
    others = [_participant(f"O{i}", n) for i in range(_LINES)]
    db_session.add_all([eq, me, *others])
    await db_session.flush()
    db_session.add_all([TrustLine(from_participant_id=me.id, to_participant_id=other.id, equivalent_id=eq.id,
                                  limit=Decimal("100"), status="active", policy={}) for other in others])
    debts = [Debt(debtor_id=other.id, creditor_id=me.id, equivalent_id=eq.id, amount=Decimal(i + 1))
             for i, other in enumerate(others[:_INDEBTED])]
    async with debt_fixture_setup(db_session, label="p035-a3"):
        db_session.add_all(debts)
    await db_session.commit()
    me_id, my_pid = me.id, me.pid
    expected_used = {other.pid: Decimal(i + 1) if i < _INDEBTED else Decimal("0") for i, other in enumerate(others)}
    db_session.expunge_all()  # fresh lines: nothing of them, their participants or the equivalent is loaded

    statements: list[str] = []
    connection = await db_session.connection()

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(connection.sync_connection, "after_cursor_execute", _record)
    try:
        lines = await TrustLineService(db_session).get_by_participant(me_id)
    finally:
        event.remove(connection.sync_connection, "after_cursor_execute", _record)

    assert len(lines) == _LINES
    assert {line.from_pid for line in lines} == {my_pid}
    assert {line.to_pid: line.used for line in lines} == expected_used
    assert all(line.equivalent_code == f"TL{n}" and line.available == line.limit - line.used for line in lines)
    assert len(statements) <= _STATEMENTS_ALLOWED, (
        f"get_by_participant on {_LINES} active lines: actual={len(statements)} statements, "
        f"threshold={_STATEMENTS_ALLOWED}"
    )


def _wire(line) -> dict:
    return TrustLineSchema.model_validate(line).model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_the_page_says_of_every_line_what_the_single_line_read_says(db_session):
    """The page reads its lines in one statement; `get_one` still reads a line the old way, four statements each.
    Both must write the same line. The cases a join could get wrong: a CLOSED line whose pair owes its successor
    (`used` stays zero), a debt in the OTHER direction and in ANOTHER equivalent (not this line's), a line with no
    debt row, an incoming line, the equivalent filter, and a page cut by limit and offset."""

    n = uuid.uuid4().hex[:8].upper()
    eq_a = Equivalent(code=f"TA{n}", symbol="TA", description=None, precision=2, metadata_={}, is_active=True)
    eq_b = Equivalent(code=f"TB{n}", symbol="TB", description=None, precision=0, metadata_={}, is_active=True)
    me, p1, p2, p3 = (_participant(name, n) for name in ("ME", "P1", "P2", "P3"))
    db_session.add_all([eq_a, eq_b, me, p1, p2, p3])
    await db_session.flush()

    def line(frm, to, eq, limit, status="active"):
        return TrustLine(from_participant_id=frm.id, to_participant_id=to.id, equivalent_id=eq.id,
                         limit=Decimal(limit), status=status, policy={})

    db_session.add_all([
        line(me, p1, eq_a, "100"), line(me, p1, eq_a, "40", status="closed"),  # the closed predecessor of the first
        line(me, p2, eq_a, "10"), line(me, p2, eq_b, "7"), line(p3, me, eq_a, "30"), line(p1, me, eq_a, "5"),
        line(me, p3, eq_b, "9", status="closed"),
    ])
    debts = [
        Debt(debtor_id=p1.id, creditor_id=me.id, equivalent_id=eq_a.id, amount=Decimal("12.34")),  # me -> p1, live
        Debt(debtor_id=me.id, creditor_id=p3.id, equivalent_id=eq_a.id, amount=Decimal("4")),       # p3 -> me
        Debt(debtor_id=p2.id, creditor_id=me.id, equivalent_id=eq_b.id, amount=Decimal("3")),       # me -> p2 in B only
        Debt(debtor_id=p3.id, creditor_id=me.id, equivalent_id=eq_b.id, amount=Decimal("2")),       # pair of a closed line
    ]
    async with debt_fixture_setup(db_session, label="p035-a3-same"):
        db_session.add_all(debts)
    await db_session.commit()
    me_id, code_a = me.id, eq_a.code
    service = TrustLineService(db_session)

    async def page(**filters) -> list[dict]:
        db_session.expunge_all()
        return [_wire(item) for item in await service.get_by_participant(me_id, **filters)]

    async def one_by_one(lines: list[dict]) -> list[dict]:
        db_session.expunge_all()
        return [_wire(await service.get_one(uuid.UUID(item["id"]))) for item in lines]

    seen = 0
    for filters in ({}, {"status": "closed"}, {"direction": "outgoing"}, {"direction": "incoming"},
                    {"equivalent": code_a}, {"status": "closed", "equivalent": code_a}, {"limit": 2, "offset": 1}):
        lines = await page(**filters)
        assert lines == await one_by_one(lines), filters
        seen += len(lines)
    assert seen == 5 + 2 + 3 + 2 + 4 + 1 + 2

    active = {(item["from"], item["to"], item["equivalent"]): item for item in await page()}
    closed = await page(status="closed")
    assert active[(me.pid, p1.pid, code_a)]["used"] == "12.34" and active[(me.pid, p1.pid, code_a)]["available"] == "87.66"
    assert active[(me.pid, p2.pid, code_a)]["used"] == "0.00", "a debt in another equivalent is not this line's"
    assert active[(p1.pid, me.pid, code_a)]["used"] == "0.00", "the opposite direction's debt is not this line's"
    assert sorted((item["limit"], item["used"]) for item in closed) == [("40.00", "0.00"), ("9", "0")], closed
