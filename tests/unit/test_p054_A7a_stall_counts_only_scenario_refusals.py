"""054-A-7a review: which all-refused ticks extend the stall (`_real_consec_all_rejected_ticks`), at the executor.

Owner, 2026-10-30: when the automatic payments do not go through the scenario is exhausted; an operator's stop or an
integrity hold is a different state. So a tick with nothing committed and no error counts when AT LEAST ONE refusal is
the scenario's own - routing, capacity or the line's policy (`SCENARIO_REFUSAL_CODES`) - whatever the other refusals
are: in a run of two equivalents the stop of one must not silence the stall of the other. A tick with no refusal of
the scenario at all (only money stops, `MoneyBoundary.MONEY_STOP_REASONS`; a participant out of circulation; an invalid
input), or one cut short by a stop of the run, is neutral: the counter neither grows nor resets. A commit or an error
resets it, as before.

The executor runs for real (`execute_planned_payments`); only the payment core is replaced by a function that raises,
or returns as a stored refusal (019 `T1905`), the exception the core itself raises for that refusal.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService, StagedPaymentResult
from app.schemas.payment import PaymentResult
from app.utils.exceptions import (
    BadRequestException,
    ConflictException,
    RoutingException,
)
from tests.unit.test_real_payments_ordered_journal import (
    _Action,
    _executor,
    _payment_result,
    _run,
    _Session,
    _Sse,
)

START = 1


def _no_capacity() -> Exception:
    return RoutingException("no capacity", insufficient_capacity=True, details={"reason": "insufficient_capacity"})


def _policy() -> Exception:
    return RoutingException("policy", insufficient_capacity=False, details={"reason": "policy", "rule": "x"})


def _stopped() -> Exception:
    return MoneyBoundary.inactive_equivalent_conflict(["UAH"])


#: The run is paused while its payments run (the executor reads `run.state`), and the payment is refused for capacity.
PAUSED_THEN_NO_CAPACITY = "paused-then-no-capacity"


def _held() -> Exception:
    return MoneyBoundary.integrity_hold_conflict(["UAH"])


def _suspended() -> Exception:
    return MoneyBoundary.participant_suspended_conflict(["B"])


def _invalid() -> Exception:
    return BadRequestException("invalid")


def _aborted(to_pid: str, amount: str) -> PaymentResult:
    return PaymentResult(
        tx_id=f"tx-{to_pid}",
        status="ABORTED",
        **{"from": "A"},
        to=to_pid,
        equivalent="UAH",
        amount=amount,
        routes=None,
        error=None,
        created_at="2026-10-30T00:00:00+00:00",
        committed_at=None,
    )


async def _counter_after(monkeypatch, outcomes: list, *, equivalents: list[str] | None = None) -> int:
    """Run one tick whose payments end as `outcomes` (an exception factory, `("stored", factory)` for a refusal the core
    returns, "commit", or `PAUSED_THEN_NO_CAPACITY`) from a counter of `START`, payment `i` in `equivalents[i]` (UAH by
    default); the counter the executor leaves."""

    session = _Session()
    run = _run()
    run._real_consec_all_rejected_ticks = START
    by_receiver = {f"R{i}": outcome for i, outcome in enumerate(outcomes)}

    async def staged(self, _sender_id, *, to_pid, amount, **_kwargs):
        outcome = by_receiver[to_pid]
        if outcome == PAUSED_THEN_NO_CAPACITY:
            run.state = "paused"
            raise _no_capacity()
        if outcome == "commit":
            return StagedPaymentResult(result=_payment_result(to_pid=to_pid, amount=amount), post_commit_effects=None)
        if isinstance(outcome, tuple):
            return StagedPaymentResult(result=_aborted(to_pid, amount), post_commit_effects=None, refusal=outcome[1](),
                                       written_here=True)
        raise outcome()

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", staged, raising=True)
    eqs = equivalents or ["UAH"] * len(outcomes)
    result = await _executor(_Sse()).execute_planned_payments(
        session=session,
        run_id=run.run_id,
        run=run,
        planned=[_Action(i, eqs[i], "A", f"R{i}", "1.00") for i in range(len(outcomes))],
        equivalents=sorted(set(eqs)),
        sender_id_by_pid={"A": uuid.uuid4()},
        max_in_flight=1,
        max_timeouts_per_tick=0,
        fail_run=lambda *_args: None,
    )
    assert result.committed == outcomes.count("commit"), result
    assert result.errors == 0, result
    assert result.stall_ticks == run._real_consec_all_rejected_ticks
    return int(run._real_consec_all_rejected_ticks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes",
    [
        pytest.param([_no_capacity, _no_capacity], id="capacity"),
        pytest.param([_policy], id="line-policy"),
        pytest.param([("stored", _no_capacity)], id="stored-capacity-refusal"),
        pytest.param([_no_capacity, _stopped], id="mixed-capacity-and-stop"),
        pytest.param([_held, _no_capacity], id="mixed-hold-and-capacity"),
        pytest.param([_invalid, _no_capacity], id="mixed-invalid-input-and-capacity"),
    ],
)
async def test_a_tick_with_a_refusal_of_the_scenario_counts(monkeypatch, outcomes) -> None:
    assert await _counter_after(monkeypatch, outcomes) == START + 1


@pytest.mark.asyncio
async def test_the_stop_of_one_equivalent_does_not_silence_the_stall_of_another(monkeypatch) -> None:
    """Two equivalents: UAH stopped by the operator, EUR refused for capacity - EUR's scenario is stalled, the tick
    counts (ruling of the main session on review P3-1)."""

    assert await _counter_after(monkeypatch, [_stopped, _no_capacity], equivalents=["UAH", "EUR"]) == START + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes",
    [
        pytest.param([_stopped, _stopped], id="operator-stop"),
        pytest.param([_held], id="integrity-hold"),
        pytest.param([("stored", _stopped)], id="stored-operator-stop"),
        pytest.param([_stopped, _held], id="stop-and-hold"),
        pytest.param([_suspended], id="participant-suspended"),
        pytest.param([_invalid], id="invalid-input"),
        pytest.param([PAUSED_THEN_NO_CAPACITY, _no_capacity], id="capacity-refused-while-the-run-stops"),
    ],
)
async def test_a_tick_with_no_refusal_of_the_scenario_is_neutral(monkeypatch, outcomes) -> None:
    assert await _counter_after(monkeypatch, outcomes) == START


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes",
    [
        pytest.param(["commit", _no_capacity], id="commit-and-capacity"),
        pytest.param(["commit", _stopped], id="commit-and-stop"),
    ],
)
async def test_a_tick_that_commits_resets(monkeypatch, outcomes) -> None:
    assert await _counter_after(monkeypatch, outcomes) == 0


def test_premise_the_money_stop_refusals_carry_their_reason() -> None:
    """The neutral class rests on `details.reason`; the factories above build the core's own refusals."""

    assert {_stopped().details["reason"], _held().details["reason"]} == set(MoneyBoundary.MONEY_STOP_REASONS)
    assert isinstance(_stopped(), ConflictException)
