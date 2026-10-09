"""036 B1: a scripted `payment` event is spent only when the money phase that carried it is DURABLE.

`TickPaymentsPhase` is what the money boundary resolves exactly once: committed (or landed after an unknown commit) ->
`apply_deferred_effects`; rolled back -> `apply_rollback_observations`; unknown -> `apply_unknown_transaction_observations`.
Only the first spends the events. A phase that is rolled back (a stop request, a conflict that is replayed, a failed
tick) leaves them pending, so the next attempt or tick runs them again - with the same key, so the debt moves once.

The tail calls `apply_deferred_effects` a second time (`RealTick._commit_and_resolve`): spending is idempotent.
"""

from __future__ import annotations

from app.core.simulator.tick import TickPaymentsPhase


def _phase(spent: list[frozenset[int]]) -> TickPaymentsPhase:
    return TickPaymentsPhase(
        planned=[], per_eq_metric_values={}, committed=0, rejected=0, errors=0, timeouts=0,
        per_eq={}, per_eq_route={}, per_eq_edge_stats={}, stall_ticks=0,
        scripted_event_indexes=frozenset({3, 5}), on_durable=spent.append,
    )


def test_a_durable_phase_spends_its_events_and_a_second_call_spends_them_again_harmlessly() -> None:
    spent: list[frozenset[int]] = []
    phase = _phase(spent)

    phase.apply_deferred_effects()
    phase.apply_deferred_effects()

    assert spent == [frozenset({3, 5}), frozenset({3, 5})]  # the callback adds to a set: repeating it changes nothing


def test_a_rolled_back_or_unknown_phase_spends_nothing() -> None:
    spent: list[frozenset[int]] = []
    phase = _phase(spent)

    phase.apply_rollback_observations()
    phase.apply_unknown_transaction_observations()
    phase.discard_observations()

    assert spent == []


def test_a_phase_with_no_scripted_event_never_calls_back() -> None:
    spent: list[frozenset[int]] = []
    phase = TickPaymentsPhase(
        planned=[], per_eq_metric_values={}, committed=0, rejected=0, errors=0, timeouts=0,
        per_eq={}, per_eq_route={}, per_eq_edge_stats={}, stall_ticks=0, on_durable=spent.append,
    )

    phase.apply_deferred_effects()

    assert spent == []
