"""RT-009-2: a best-effort writer must not discard the tick's transaction.

Program 009, finding `F-009-2` (`B-A2b-001`, P1).

`write_tick_bottlenecks` receives the tick's own session (`commit=False`, see
``app/core/simulator/real_tick_persistence.py:122-133``).  On any failure it calls
``await session.rollback()`` on that borrowed session and returns silently, so:

* everything the tick had staged and not yet committed disappears;
* the owner then commits and fires ``on_commit`` (``real_tick_persistence.py:138``),
  i.e. **reports success** for work that no longer exists.

The neighbouring writer already shows the accepted shape: `write_tick_metrics` was given a
SAVEPOINT by slice T715 of program 007 (``app/core/simulator/storage.py:520-529``) with a
comment naming this very finding.  `write_tick_bottlenecks` never received it, and program
007 recorded that omission as open debt `T717(а)`.

The gate is Postgres because the whole family of transaction-ownership findings is gated
there (`008/plan.md` §5); the mechanism itself is engine-independent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.simulator import storage as simulator_storage
from app.db.models.participant import Participant


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_failed_bottlenecks_write_does_not_discard_the_callers_transaction(db_session):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only gate for the transaction-ownership family")

    if not simulator_storage.db_enabled():
        pytest.skip("simulator storage is disabled in this environment")

    nonce = uuid.uuid4().hex[:10]

    # The tick stages its own work first.  This stands in for everything a real tick has
    # written and not yet committed when the best-effort writer runs.
    staged = Participant(
        id=uuid.uuid4(),
        pid="tick-tail-" + nonce,
        display_name="Tick tail",
        public_key="pk-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add(staged)
    await db_session.flush()

    before = await db_session.scalar(
        select(func.count()).select_from(Participant).where(Participant.pid == staged.pid)
    )
    assert before == 1, "precondition: the tick's own work is staged in its transaction"

    # Force the writer to fail after it has started working on the borrowed session.
    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced bottlenecks write failure")

    db_session.add_all = _boom  # type: ignore[method-assign]

    try:
        await simulator_storage.write_tick_bottlenecks(
            run_id="run-" + nonce,
            equivalent="UAH",
            computed_at=datetime.now(timezone.utc),
            # `attempts` must be > 0 and the failure ratio non-zero, otherwise the writer
            # drops the item and returns before it ever touches the session.
            edge_stats={
                ("a", "b"): {
                    "attempts": 10,
                    "errors": 3,
                    "timeouts": 0,
                    "committed": 7,
                    "rejected": 0,
                }
            },
            session=db_session,
            limit=50,
            commit=False,
        )
    finally:
        del db_session.add_all

    after = await db_session.scalar(
        select(func.count()).select_from(Participant).where(Participant.pid == staged.pid)
    )
    assert after == 1, (
        "the best-effort bottlenecks writer rolled back the tick's own session: the work the "
        "tick had staged is gone, and its owner will still commit and report success"
    )
