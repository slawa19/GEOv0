"""047 `T4704`: a FAILED holds in the pass that found it, and a stored unheld FAILED holds with NO pass (fail-closed).

THE FINDINGS. (1) Closing §19.5 review (Codex, on `1c0359e1`; class 1): one attempt of the integrity job is ended by
ONE deadline (`INTEGRITY_ATTEMPT_TIMEOUT_SECONDS`, `asyncio.timeout` in `app/core/maintenance_jobs.py`
`_run_integrity_checkpoints_once`) covering checkpoints, verification, publication and reaction; a diverged, unheld
equivalent cost two full passes (the verification, then the reaction's confirmation re-run), so a pass longer than
half the deadline had the confirmation cut in every attempt and the FAILED never held while money moved. (2) §15
review of the first fix (Codex, `1c0359e1..3a042cac`; class 1): even one pass per stored FAILED starves - equivalent A,
earlier by code, eats most of the deadline, and B's confirmation is cut in every attempt. By the advisor's stop
condition (2026-10-19) this is NOT repaired a fifth time but narrowed FAIL-CLOSED: a FAILED is published and held in
one transaction with no confirmation re-run, and a stored FAILED without a hold is held at once, with no verification
pass. A false hold (an equivalent repaired after its FAILED was published) is released the existing way.

THE MODELLED CUT, AND WHY IT IS FAITHFUL. No wall-clock time (AGENTS §9/§11). The real host runs -
`_run_integrity_checkpoints_once` with its real `asyncio.timeout`, the deadline left at its configured value; the
`asyncio` the host module sees is a proxy whose `timeout` records the real Timeout object it creates (asserted: exactly
one per attempt). The real `verify_journal_equals_change` is wrapped to count the passes over the equivalents under
test; a pass beyond the attempt's budget first `reschedule`s that real Timeout to now and then runs the real verifier.
So the cancellation is the real deadline's own: delivered by `Timeout._on_timeout` as `CancelledError` at the first
suspension inside that pass's first database statement, turned into `TimeoutError` by the Timeout's exit, recorded by
the host as `periodic_timed_out`. Every session open at the cut is inside `async with` and closed on the way out;
nothing of the cut pass is committed (the snapshot is read-only, the publication has not begun). Passes over OTHER
equivalents of the clone are not counted: they are empty and negligible against a long history.

TIER. PostgreSQL, a disposable clone per test (`tier_on_a_clone`); the divergence is a real debt change around the
application through the named corruption helper (`tests/ledger_corruption.py`, via `_set_debt`).
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import (
    FAILED,
    HOLD_ALREADY_HELD,
    HOLD_SET,
    PASSED,
    run_scheduled_reconciliation,
)
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import _seed_triangle
from tests.unit.test_p015_step5a_reconciliation import _baseline, _fixture_debts
from tests.unit.test_p015_step5c_reaction_and_hold import _faulty_triangle, _hold_of, _set_debt

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reconcile_debts.py"


class _DeadlineStand:
    """The real host, its real deadline, and a cut placed at the pass where the deadline would land."""

    def __init__(self, monkeypatch, factory, equivalent_ids) -> None:
        import app.core.maintenance_jobs as host
        import app.db.session as app_db_session

        self.host = host
        self.counted = set(equivalent_ids)
        self.timeouts: list[asyncio.Timeout] = []
        self.budget = 0
        self.passes: list[uuid.UUID] = []
        self.cut = False
        self.counts: list[dict] = []

        def _recording_timeout(delay):
            timeout = asyncio.timeout(delay)
            self.timeouts.append(timeout)
            return timeout

        proxy = SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")})
        proxy.timeout = _recording_timeout
        monkeypatch.setattr(host, "asyncio", proxy)
        monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)

        real_verify = reconciliation.verify_journal_equals_change

        async def _verify(session, equivalent_id):
            if equivalent_id in self.counted:
                self.passes.append(equivalent_id)
                if len(self.passes) > self.budget:
                    # The deadline is due now: the real Timeout cancels this task at the verifier's next suspension.
                    self.cut = True
                    self.timeouts[-1].reschedule(asyncio.get_running_loop().time())
            return await real_verify(session, equivalent_id)

        monkeypatch.setattr(reconciliation, "verify_journal_equals_change", _verify)

        real_run = reconciliation.run_scheduled_reconciliation

        async def _run(session_factory, **kwargs):
            counts = await real_run(session_factory, **kwargs)
            self.counts.append(counts)
            return counts

        monkeypatch.setattr(reconciliation, "run_scheduled_reconciliation", _run)

    async def attempt(self, *, budget: int) -> tuple[bool | None, SimpleNamespace]:
        self.budget, self.passes, self.cut = budget, [], False
        self.timeouts.clear()
        app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
        completed = await self.host._run_integrity_checkpoints_once(app, reason="periodic")
        assert len(self.timeouts) == 1, "stand: the host did not open exactly one attempt deadline"
        return completed, app


async def _latest(factory, equivalent_id):
    columns = debt_reconciliation_results.c
    async with factory() as session:
        return (
            await session.execute(
                select(columns.id, columns.status).where(
                    columns.equivalent_id == equivalent_id, columns.is_latest.is_(True)
                )
            )
        ).all()


def _event(app) -> str | None:
    return app.state.background_jobs.get("integrity", {}).get("event")


async def _clean_triangle(factory):
    """A baselined equivalent with one debt `a -> b` the journal explains (PASSED). Returns (triangle, debt)."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    return triangle, debt


async def _publish_a_failed_without_its_hold(factory, equivalent_id) -> uuid.UUID:
    """STAND: the latest result is a real FAILED and nothing holds it - what a run before 047 `T4704` left when its
    separate reaction was cut or failed. Verified in a snapshot and published by `record_outcome`, as it was then."""

    async with factory() as session:
        await reconciliation.open_verification_snapshot(session)
        outcome = await reconciliation.verify_journal_equals_change(session, equivalent_id)
        await session.rollback()
    assert outcome.status == FAILED, f"premise: {outcome.status}"
    async with factory() as session:
        await reconciliation.record_outcome(session, outcome)
        await session.commit()
    ((row_id, status),) = await _latest(factory, equivalent_id)
    assert status == FAILED and await _hold_of(factory, equivalent_id) is None
    return row_id


def _command():
    spec = importlib.util.spec_from_file_location("p047_t4704_reconcile_debts", _SCRIPT)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    return command


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_stored_unheld_failed_is_held_with_zero_passes_when_an_earlier_equivalent_spends_the_budget(
    db_session, monkeypatch
) -> None:
    """Codex's A/B case: A (earlier by code, healthy) and B (a stored FAILED, unheld, still diverged). The attempt's
    budget is ONE pass - A's. B must end HELD on its latest FAILED with ZERO passes of its own, the attempt complete.

    RED on `3a042cac`: B's confirmation re-run is a second pass - cut, B unheld.
    MUTATION: verify B before holding it (a pass in the stored-FAILED path) - the pass is cut, B unheld / passes, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    first, second = await _clean_triangle(factory), await _clean_triangle(factory)
    (a, _a_debt), (b, b_debt) = sorted([first, second], key=lambda pair: pair[0].equivalent.code)
    assert a.equivalent.code < b.equivalent.code, "premise: the host verifies A before B (by code)"
    await _set_debt(factory, b_debt, "10.00000001")
    failed_row = await _publish_a_failed_without_its_hold(factory, b.equivalent.id)

    stand = _DeadlineStand(monkeypatch, factory, [a.equivalent.id, b.equivalent.id])
    completed, app = await stand.attempt(budget=1)

    hold = await _hold_of(factory, b.equivalent.id)
    assert hold == failed_row, (
        f"B's stored FAILED is not held: hold={hold}, passes={[str(p) for p in stand.passes]}, cut={stand.cut}, "
        f"completed={completed}, event={_event(app)}"
    )
    assert stand.passes == [a.equivalent.id] and not stand.cut, (stand.passes, stand.cut)
    assert completed is True, _event(app)
    counts = stand.counts[-1]
    assert (counts[f"hold_{HOLD_SET}"], counts["hold_errors"], counts["error"]) == (1, 0, 0), counts
    assert await _hold_of(factory, a.equivalent.id) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_fresh_failed_is_published_and_held_in_its_one_pass(db_session, monkeypatch) -> None:
    """A newly diverged equivalent, a budget of ONE pass: the FAILED is published and held in that pass - the attempt
    completes, the hold points at the latest FAILED row, no second pass is asked for.

    RED on `3a042cac`: the publication is followed by the reaction's confirmation re-run (pass 2) - cut, unheld.
    MUTATION: bring the confirmation re-run back in the ordinary path - pass 2 is cut, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    stand = _DeadlineStand(monkeypatch, factory, [equivalent_id])
    completed, app = await stand.attempt(budget=1)

    hold = await _hold_of(factory, equivalent_id)
    latest = await _latest(factory, equivalent_id)
    assert hold is not None, (
        f"a fresh FAILED is not held in its pass: passes={len(stand.passes)} cut={stand.cut} "
        f"completed={completed} event={_event(app)} latest={latest}"
    )
    assert [(row.id, row.status) for row in latest] == [(hold, FAILED)]
    assert (completed, len(stand.passes), stand.cut) == (True, 1, False), (completed, stand.passes, stand.cut)
    counts = stand.counts[-1]
    assert (counts[FAILED], counts["rows_inserted"], counts[f"hold_{HOLD_SET}"], counts["hold_errors"]) == (1, 1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_the_stand_s_cut_lands_inside_the_pass_and_the_host_records_it_timed_out(db_session, monkeypatch) -> None:
    """POSITIVE CONTROL of the stand (adversarial pass, P3): with a budget of ZERO passes the cut must land inside the
    first pass - the real deadline cancels it, the host returns False with `periodic_timed_out`, and nothing of the cut
    pass is committed (no result row, no hold). Without this the other tests' `not stand.cut` could hide a stand whose
    cut never fires.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    stand = _DeadlineStand(monkeypatch, factory, [equivalent_id])
    completed, app = await stand.attempt(budget=0)

    assert (completed, stand.cut, len(stand.passes), _event(app)) == (False, True, 1, "periodic_timed_out"), (
        completed, stand.cut, stand.passes, app.state.background_jobs
    )
    assert stand.counts == [], "the cut run returned counts: it was not cancelled"
    assert await _latest(factory, equivalent_id) == [] and await _hold_of(factory, equivalent_id) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_stored_failed_of_a_repaired_equivalent_is_held_and_the_existing_release_clears_it(
    db_session, monkeypatch, capsys
) -> None:
    """Fail-closed has a price, and this pins how it is paid. A stored FAILED whose equivalent was repaired since: the
    operator's command holds it WITHOUT a verification pass (exit 1, marked); run again, it verifies the held
    equivalent and publishes PASSED (exit 0); the admin's clear re-verifies under the row lock and lifts the hold.

    REWRITTEN 2026-10-19 (fail-closed): the first fix published the confirmation's PASSED and held nothing here.
    RED on `3a042cac`: the command confirms (PASSED, exit 0) and nothing is held.
    MUTATION: verify before holding in the stored-FAILED path - PASSED, no hold, red.
    """
    from app.core.equivalents import clear_integrity_hold
    from tests.conftest import TestingSessionLocal as factory

    triangle, debt = await _faulty_triangle(factory)
    equivalent_id, code = triangle.equivalent.id, triangle.equivalent.code
    failed_row = await _publish_a_failed_without_its_hold(factory, equivalent_id)
    await _set_debt(factory, debt, "10")

    passes: list[uuid.UUID] = []
    real_verify = reconciliation.verify_journal_equals_change

    async def _counting_verify(session, eq_id):
        passes.append(eq_id)
        return await real_verify(session, eq_id)

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", _counting_verify)
    command = _command()
    capsys.readouterr()
    assert await command._run([code], False, factory) == 1, capsys.readouterr()
    out = capsys.readouterr().out
    assert f"{code}: FAILED hold=set" in out and "held without a verification pass" in out, out
    assert passes == [] and await _hold_of(factory, equivalent_id) == failed_row, (passes, out)

    assert await command._run([code], False, factory) == 0, capsys.readouterr()
    assert passes == [equivalent_id]
    assert [row.status for row in await _latest(factory, equivalent_id)] == [PASSED]
    assert await _hold_of(factory, equivalent_id) == failed_row, "a PASSED cleared the hold by itself"

    async with factory() as session:
        _eq, cleared_hold, _cleared_on = await clear_integrity_hold(session, code)
        await session.commit()
    assert cleared_hold == failed_row and await _hold_of(factory, equivalent_id) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_reaction_that_held_nothing_falls_back_to_the_ordinary_path_counted_once(
    db_session, monkeypatch
) -> None:
    """The routing read said "FAILED, not held", but the equivalent is held by the time the reaction locks it (another
    runner held it in between): the reaction holds nothing, so the ordinary path verifies and publishes - one verdict,
    one hold decision counted, the hold not re-pointed.

    MUTATION: `continue` after the empty reaction - no verdict, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    first = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    hold = await _hold_of(factory, equivalent_id)
    assert first[f"hold_{HOLD_SET}"] == 1 and hold is not None, f"premise: {first}"

    async def _stale_routing_read(session_factory, equivalent_id):
        return True

    monkeypatch.setattr(reconciliation, "_stored_failed_unheld", _stale_routing_read)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])

    assert (counts[FAILED], counts["rows_unchanged"], counts[f"hold_{HOLD_ALREADY_HELD}"]) == (1, 1, 1), counts
    assert (counts["error"], counts["hold_errors"], counts[f"hold_{HOLD_SET}"]) == (0, 0, 0), counts
    assert await _hold_of(factory, equivalent_id) == hold


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_failing_hold_of_a_stored_failed_is_a_hold_error_and_the_command_still_reports_failed(
    db_session, monkeypatch, capsys
) -> None:
    """The hold of a stored FAILED raises: `hold_errors`, the equivalent named, no verdict and no row counted, the stored
    FAILED untouched, nothing held. The operator's command still reports that stored FAILED (exit 1, marked), never
    NOT CHECKED / exit 2 - its contract "a FAILED is never turned into 'did not run'".

    MUTATION: drop the `restated` branch of `scripts/reconcile_debts.py` - exit 2, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    failed_row = await _publish_a_failed_without_its_hold(factory, equivalent_id)

    async def _hold_fails(session, eq_id, result_id):
        raise RuntimeError("stand: the hold failed")

    monkeypatch.setattr(reconciliation, "_set_integrity_hold", _hold_fails)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    assert (counts[FAILED], counts[PASSED], counts["rows_inserted"], counts["rows_unchanged"]) == (0, 0, 0, 0), counts
    assert (counts["hold_errors"], counts["error"], counts["errored_equivalents"]) == (1, 0, [str(equivalent_id)]), counts
    assert [row.id for row in await _latest(factory, equivalent_id)] == [failed_row]
    assert await _hold_of(factory, equivalent_id) is None

    command = _command()
    capsys.readouterr()
    assert await command._run([triangle.equivalent.code], False, factory) == 1, capsys.readouterr()
    out = capsys.readouterr().out
    assert f"{triangle.equivalent.code}: FAILED hold=ERROR" in out and "hold did not complete" in out, out
