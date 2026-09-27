"""Programme 021, stage 1 (`T2102`): `TrustLineWriteBatch` - checkpoints per transaction and its detector.

Two properties of the batch itself, apart from any simulator caller (mode A):

* CHECKPOINTS PER CALLER TRANSACTION (spec, "Решения" item 9; the frozen benchmark threshold): a batch of
  several operations over two equivalents computes exactly `2 x touched_equivalents` checkpoints, whatever the
  number of operations, and every row of one equivalent carries that equivalent's single before/after pair.
  Counted on the service's own binding (`tests/p021_support.py`), calling straight through.
* THE UNFINISHED-BATCH DETECTOR: a commit of a batch with applied operations and no successful `finish()`
  raises instead of making the mutations durable without their audit rows; a rollback disarms it. It is an
  in-process mistake detector, not a barrier (see the class docstring).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.trustlines.service import TrustLineService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest, TrustLineUpdateRequest
from tests.p021_support import TrustLineCheckpoints, is_transaction_scoped, trust_line_audit_rows

_UNSIGNED = "__internal__"


async def _world(session):
    n = uuid.uuid4().hex[:6].upper()
    eqs = [Equivalent(code=f"P21W{i}{n}", precision=2, is_active=True, metadata_={}) for i in (1, 2)]
    people = [
        Participant(pid=f"P21W_{r}_{n}", display_name=r, public_key=f"pk_{r}_{n}", type="person",
                    status="active", profile={})
        for r in ("A", "B", "C")
    ]
    session.add_all([*eqs, *people])
    await session.flush()
    a, b, c = people
    lines = [
        TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eqs[0].id, limit=Decimal("10"),
                  status="active", policy={}),
        TrustLine(from_participant_id=a.id, to_participant_id=c.id, equivalent_id=eqs[0].id, limit=Decimal("10"),
                  status="active", policy={}),
        TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eqs[1].id, limit=Decimal("10"),
                  status="active", policy={}),
    ]
    session.add_all(lines)
    await session.commit()
    return a.id, [ln.id for ln in lines], [e.code for e in eqs]


@pytest.mark.asyncio
async def test_one_checkpoint_pair_per_touched_equivalent_per_batch(db_session, monkeypatch) -> None:
    owner, (l1, l2, l3), (e1, e2) = await _world(db_session)
    checkpoints = TrustLineCheckpoints(monkeypatch)
    service = TrustLineService(db_session)
    batch = service.begin_internal_batch()

    await service.execute_update(batch, l1, owner, TrustLineUpdateRequest(limit="11", signature=_UNSIGNED),
                                 require_signature=False)
    await service.execute_update(batch, l2, owner, TrustLineUpdateRequest(limit="12", signature=_UNSIGNED),
                                 require_signature=False)
    await service.execute_close(batch, l3, owner, TrustLineCloseRequest(signature=_UNSIGNED),
                                require_signature=False)
    await batch.finish()
    await db_session.commit()

    assert (batch.applied_operations, len(batch.touched_equivalent_ids)) == (3, 2)
    assert checkpoints.count == 2 * 2, f"{checkpoints.count} checkpoint computations for 3 operations in 2 equivalents"
    rows = await trust_line_audit_rows(db_session, equivalent_codes=[e1, e2])
    assert sorted(r.operation_type for r in rows) == ["TRUST_LINE_CLOSE", "TRUST_LINE_UPDATE", "TRUST_LINE_UPDATE"]
    assert all(is_transaction_scoped(r) for r in rows)
    pairs = {code: {(r.state_checksum_before, r.state_checksum_after) for r in rows if r.equivalent_code == code}
             for code in (e1, e2)}
    assert all(len(p) == 1 for p in pairs.values()), pairs
    assert all(before != after for (before, after), in pairs.values()), pairs


@pytest.mark.asyncio
async def test_a_commit_without_finish_is_refused_and_a_rollback_disarms(db_session) -> None:
    owner, (l1, _l2, _l3), codes = await _world(db_session)
    service = TrustLineService(db_session)
    batch = service.begin_internal_batch()
    await service.execute_update(batch, l1, owner, TrustLineUpdateRequest(limit="99", signature=_UNSIGNED),
                                 require_signature=False)

    with pytest.raises(RuntimeError, match="call finish\\(\\) first"):
        await db_session.commit()
    await db_session.rollback()
    await db_session.commit()  # disarmed by the rollback: nothing is refused any more

    limit = await db_session.scalar(select(TrustLine.limit).where(TrustLine.id == l1))
    assert Decimal(str(limit)) == Decimal("10")
    assert await trust_line_audit_rows(db_session, equivalent_codes=codes) == []


@pytest.mark.asyncio
async def test_control_an_empty_batch_computes_nothing_and_commits(db_session, monkeypatch) -> None:
    await _world(db_session)
    checkpoints = TrustLineCheckpoints(monkeypatch)
    batch = TrustLineService(db_session).begin_internal_batch()
    await batch.finish()
    await db_session.commit()
    assert checkpoints.count == 0
