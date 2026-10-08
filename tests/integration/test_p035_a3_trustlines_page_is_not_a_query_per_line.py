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
