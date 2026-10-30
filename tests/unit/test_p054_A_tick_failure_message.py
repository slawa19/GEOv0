"""054-C-5 (tick part): the `last_error.message` of a failed tick carries no raw exception text and no class name.

NARROW unit test of `tick_failure_message`, the one function that forms the message. A real tick reaches the generic
failure branch with a non-application exception only through an injected failure (a database or OS error inside the
tick), which this programme does not accept as evidence; the wiring of the function into the branch is covered by
the tick stands that reach it (`test_p015_t1525_control_postgres`, `test_p024_run_refuses_a_real_participant_postgres`).
"""

from __future__ import annotations

from app.core.simulator.real_scenario_seeder import ScenarioTrustLineRefused
from app.core.simulator.tick import tick_failure_message


def test_a_foreign_exception_leaves_only_the_error_id() -> None:
    raw = 'relation "debts" does not exist at C:\\Users\\someone\\geo\\app.db [SQL: SELECT 1]'
    message = tick_failure_message(OSError(raw), error_id="abc123")
    assert "abc123" in message
    assert raw not in message and "SELECT" not in message and "C:\\" not in message
    assert "OSError" not in message


def test_an_application_refusal_keeps_its_own_message() -> None:
    refusal = ScenarioTrustLineRefused("a->b UAH", "participant_suspended", "the debtor is suspended")
    message = tick_failure_message(refusal, error_id="abc123")
    assert refusal.message in message and "abc123" in message
