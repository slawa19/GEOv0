"""047 `T4704`: the reaction to a persisting unheld FAILED fits in ONE verification pass of the attempt.

THE FINDING (closing §19.5 review, Codex, on `1c0359e1`; class 1). One attempt of the integrity job is ended by ONE
deadline (`INTEGRITY_ATTEMPT_TIMEOUT_SECONDS`, `asyncio.timeout` in `app/core/maintenance_jobs.py`
`_run_integrity_checkpoints_once`) that covers the checkpoints, the verification, the publication and the reaction.
For a diverged equivalent that is not held, `run_scheduled_reconciliation` verified it (pass 1), published FAILED,
and `react_to_failed` re-ran the FULL verification as the confirmation (pass 2) before it set the hold. When one pass
takes more than half the deadline, the deadline cuts the confirmation in every attempt; each retry repeats both
passes; the durable FAILED stays with `integrity_hold_result_id` NULL and money keeps moving.

THE MODELLED CUT, AND WHY IT IS FAITHFUL. Wall-clock time is not used (AGENTS §9/§11: no sleeps, no thresholds on
timing). The real host runs - `_run_integrity_checkpoints_once` with its real `asyncio.timeout` - with the deadline
left at its configured value; the `asyncio` the host module sees is a proxy whose `timeout` records the real Timeout
object it creates. The real `verify_journal_equals_change` is wrapped to count the passes over the diverged
equivalent; a pass beyond the attempt's budget first `reschedule`s that real Timeout to now and then runs the real
verifier. So the cancellation is the real deadline's own: delivered by `Timeout._on_timeout` as `CancelledError` at
the first suspension inside the verifier's first database statement of that pass - inside the pass, exactly where a
deadline between one and two passes lands - and turned into `TimeoutError` by the Timeout's exit, which the host
records as `periodic_timed_out` and returns False. Every session open at the cut is inside `async with` and is closed
on the way out: the verification snapshot is read-only, the reaction's transaction (evidence + hold) has not reached
`record_outcome` or its commit, so nothing of the cut pass is committed. Passes over OTHER equivalents of the clone
are not counted: they are empty and their verification is negligible against a long history.

THE REPRODUCER. Attempt 1 with a budget of one pass: verified FAILED, published, the confirmation cut (both before
and after the fix: no stored FAILED yet). Attempt 2 with a budget of EXACTLY ONE pass: on `1c0359e1` it needs two
passes and is cut again - hold NULL, red; after `T4704` the stored unheld FAILED goes straight to the reaction, whose
one pass confirms it and holds.

TIER. PostgreSQL, a disposable clone per test (`tier_on_a_clone`); the divergence is a real debt change around the
application through the named corruption helper (`tests/ledger_corruption.py`, via `_set_debt`).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import (
    FAILED,
    HOLD_ALREADY_HELD,
    HOLD_NOT_CONFIRMED,
    HOLD_SET,
    PASSED,
    run_scheduled_reconciliation,
)
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture
from tests.unit.test_p015_step5c_reaction_and_hold import _faulty_triangle, _hold_of, _set_debt


class _DeadlineStand:
    """The real host, its real deadline, and a cut placed at the pass where the deadline would land."""

    def __init__(self, monkeypatch, factory, equivalent_id: uuid.UUID) -> None:
        import app.core.maintenance_jobs as host
        import app.db.session as app_db_session

        self.host = host
        self.equivalent_id = equivalent_id
        self.timeouts: list[asyncio.Timeout] = []
        self.budget = 0
        self.passes = 0
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
            if equivalent_id == self.equivalent_id:
                self.passes += 1
                if self.passes > self.budget:
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
        self.budget, self.passes, self.cut = budget, 0, False
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


async def _attempt_one_cut_in_the_confirmation(stand, factory, equivalent_id) -> None:
    """Attempt 1, budget one pass: verified FAILED and published; the deadline cuts the confirmation."""

    completed, app = await stand.attempt(budget=1)
    assert completed is False and stand.cut and stand.passes == 2, (
        f"premise: the deadline did not cut attempt 1 in its confirmation pass: completed={completed} "
        f"passes={stand.passes} cut={stand.cut} event={_event(app)}"
    )
    assert _event(app) == "periodic_timed_out", app.state.background_jobs
    latest = await _latest(factory, equivalent_id)
    assert [row.status for row in latest] == [FAILED], f"premise: attempt 1 did not publish its FAILED: {latest}"
    assert await _hold_of(factory, equivalent_id) is None, "premise: the cut confirmation held the equivalent"


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_persisting_unheld_failed_is_held_within_one_pass_of_the_next_attempt(
    db_session, monkeypatch
) -> None:
    """Attempt 2 has a budget of exactly ONE pass of the diverged equivalent and must end with the hold set.

    RED on `1c0359e1`: attempt 2 verifies again (pass 1), then re-runs the confirmation (pass 2) - cut, hold NULL.
    MUTATION: remove the `T4704` routing in `run_scheduled_reconciliation` (the old two-pass path) - red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    stand = _DeadlineStand(monkeypatch, factory, equivalent_id)
    await _attempt_one_cut_in_the_confirmation(stand, factory, equivalent_id)

    completed, app = await stand.attempt(budget=1)
    hold = await _hold_of(factory, equivalent_id)
    assert hold is not None, (
        f"a persisting FAILED stayed unheld: attempt 2 needed {stand.passes} passes for a budget of 1 "
        f"(cut={stand.cut}, completed={completed}, event={_event(app)})"
    )
    assert (completed, stand.passes, stand.cut) == (True, 1, False), (completed, stand.passes, stand.cut)
    latest = await _latest(factory, equivalent_id)
    assert [(row.id, row.status) for row in latest] == [(hold, FAILED)], "the hold does not point at the latest FAILED"
    counts = stand.counts[-1]
    assert (counts[FAILED], counts[PASSED], counts["rows_unchanged"], counts["rows_inserted"]) == (1, 0, 1, 0), counts
    assert (counts[f"hold_{HOLD_SET}"], counts["hold_errors"], counts["error"]) == (1, 0, 0), counts


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_stored_failed_repaired_before_the_next_attempt_is_replaced_by_the_confirmation_passed(
    db_session, monkeypatch
) -> None:
    """The counter-check: the divergence is repaired before attempt 2. The confirmation (the one pass) is PASSED: it
    is PUBLISHED as the latest result, nothing is held, and the counters say what happened.

    MUTATIONS: (b) drop the publication of the not-confirmed outcome - the stale FAILED stays latest, red;
    (c) hold in the routing without the confirmation re-run - held on a repaired equivalent, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    triangle, debt = await _faulty_triangle(factory)
    equivalent_id = triangle.equivalent.id
    stand = _DeadlineStand(monkeypatch, factory, equivalent_id)
    await _attempt_one_cut_in_the_confirmation(stand, factory, equivalent_id)
    (stale,) = await _latest(factory, equivalent_id)

    await _set_debt(factory, debt, "10")
    completed, app = await stand.attempt(budget=1)

    assert await _hold_of(factory, equivalent_id) is None, "a repaired equivalent was held"
    latest = await _latest(factory, equivalent_id)
    assert [row.status for row in latest] == [PASSED] and latest[0].id != stale.id, (
        f"the confirmation's PASSED was not published; the latest is still {latest} (stale FAILED {stale.id})"
    )
    assert (completed, stand.passes, stand.cut) == (True, 1, False), (completed, stand.passes, stand.cut, _event(app))
    counts = stand.counts[-1]
    assert (counts[PASSED], counts[FAILED], counts["rows_inserted"], counts["rows_unchanged"]) == (1, 0, 1, 0), counts
    assert (counts[f"hold_{HOLD_NOT_CONFIRMED}"], counts[f"hold_{HOLD_SET}"], counts["error"]) == (1, 0, 0), counts


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_reaction_that_re_ran_nothing_falls_back_to_the_ordinary_path_counted_once(
    db_session, monkeypatch
) -> None:
    """The routing read said "FAILED, not held", but the equivalent is held by the time the reaction reads it (another
    run held it in between): the reaction re-runs nothing, so the ordinary path verifies and publishes - one verdict,
    one reaction counted, the hold not re-pointed.

    MUTATION: count the empty reaction as well - `hold_already_held` is 2, red; or `continue` after it - no verdict, red.
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


async def _stored_failed_unheld_by_a_failing_reaction(monkeypatch, factory):
    """A faulty equivalent whose first run published FAILED and whose reaction raised: the latest stored result is
    FAILED and nothing holds it. Returns (triangle, debt); the reaction stays patched to raise."""

    triangle, debt = await _faulty_triangle(factory)

    async def _reaction_fails(session_factory, equivalent_id):
        raise RuntimeError("stand: the reaction failed")

    monkeypatch.setattr(reconciliation, "react_to_failed", _reaction_fails)
    first = await run_scheduled_reconciliation(factory, equivalent_ids=[triangle.equivalent.id])
    assert (first[FAILED], first["hold_errors"], first["rows_inserted"]) == (1, 1, 1), f"premise: {first}"
    assert await _hold_of(factory, triangle.equivalent.id) is None
    return triangle, debt


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_reaction_error_on_the_one_pass_path_is_a_hold_error_and_the_command_still_reports_failed(
    db_session, monkeypatch, capsys
) -> None:
    """On the one-pass path a reaction error leaves no verdict of this run: `hold_errors`, the equivalent named, no
    row counted, the stored FAILED untouched, nothing held. The operator's command still reports that stored FAILED
    (exit 1, marked as not re-confirmed), never NOT CHECKED / exit 2 - its contract "a FAILED is never turned into
    'did not run'" (adversarial review of `T4704`, P2).

    MUTATION: drop the `restated` branch of `scripts/reconcile_debts.py` - exit 2, red.
    """
    import importlib.util
    from pathlib import Path

    from tests.conftest import TestingSessionLocal as factory

    triangle, _debt = await _stored_failed_unheld_by_a_failing_reaction(monkeypatch, factory)
    equivalent_id = triangle.equivalent.id
    (stored,) = await _latest(factory, equivalent_id)

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])
    assert (counts[FAILED], counts[PASSED], counts["rows_inserted"], counts["rows_unchanged"]) == (0, 0, 0, 0), counts
    assert (counts["hold_errors"], counts["error"], counts["errored_equivalents"]) == (1, 0, [str(equivalent_id)]), counts
    assert await _latest(factory, equivalent_id) == [stored] and await _hold_of(factory, equivalent_id) is None

    script = Path(__file__).resolve().parents[2] / "scripts" / "reconcile_debts.py"
    spec = importlib.util.spec_from_file_location("p047_t4704_reconcile_debts", script)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    capsys.readouterr()
    assert await command._run([triangle.equivalent.code], False, factory) == 1, capsys.readouterr()
    out = capsys.readouterr().out
    assert f"{triangle.equivalent.code}: FAILED hold=ERROR" in out and "re-confirmation and hold did not complete" in out, out


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_p047_a_not_confirmed_verdict_that_fails_to_publish_is_one_error_only(db_session, monkeypatch) -> None:
    """The repaired equivalent's confirmation is PASSED, but its publication raises: an error of this equivalent, no
    verdict and no reaction counted (the equivalent is counted once), the stale FAILED stays latest, nothing held.

    MUTATION: count `hold_not_confirmed` before the publication - it reads 1 next to `error` 1, red.
    """
    from tests.conftest import TestingSessionLocal as factory

    real_reaction = reconciliation.react_to_failed
    triangle, debt = await _stored_failed_unheld_by_a_failing_reaction(monkeypatch, factory)
    equivalent_id = triangle.equivalent.id
    (stale,) = await _latest(factory, equivalent_id)
    monkeypatch.setattr(reconciliation, "react_to_failed", real_reaction)
    await _set_debt(factory, debt, "10")

    async def _publication_fails(session, outcome):
        raise RuntimeError("stand: the publication failed")

    monkeypatch.setattr(reconciliation, "record_outcome", _publication_fails)
    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[equivalent_id])

    assert (counts["error"], counts["errored_equivalents"], counts["hold_errors"]) == (1, [str(equivalent_id)], 0), counts
    assert (counts[PASSED], counts[f"hold_{HOLD_NOT_CONFIRMED}"], counts["rows_inserted"]) == (0, 0, 0), counts
    assert await _latest(factory, equivalent_id) == [stale] and await _hold_of(factory, equivalent_id) is None
