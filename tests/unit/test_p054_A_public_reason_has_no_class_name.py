r"""054-A review FD6 (brothers of 054-C-5): no exception class name in a public field of the run.

Defect (2026-10-30): the `last_error.message` of `REAL_MODE_MONEY_CONFLICT_UNRESOLVED` ended with
`({type(error).__name__})`, and the reason of a scripted clearing's progress (`RunStatus.episode_progress[].reason`)
was `pass_error:<class>` / `clearing_error:<class>`. The class of an internal failure belongs in the log (054-C-5);
runbook 6.8 promises `last_error.message` without raw exception data. Target: the public text names the conflict or
the ending (`pass_error`, `clearing_error`) and no class; the class is logged with the run.

NARROW unit tests of the three producers, each driven through its production function: the money conflict through
`RealTick._record_money_conflict_tick` (a real tick reaches it only after the money replay budget is spent under
real contention - `test_p015_p1_money_replay_postgres` covers that path and its code), the clearing endings through
`RealTick._run_clearing` with a double of the runner entry and of the precision read (a real pass fails this way only
on an internal error, which this programme does not inject into the database). No reader parses the reason tokens:
`git grep -n "clearing_error\|pass_error" -- simulator-ui/v2/src admin-ui/src tests app docs` finds none.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.simulator.models import RunRecord
from tests.simulator_tick_stand import clearing_unit_tick, unit_tick


class _SecretFailureError(RuntimeError):
    """A class name that must not leave the process through a public field."""


def _run() -> RunRecord:
    run = RunRecord(run_id=f"fd6-{uuid.uuid4().hex[:6]}", scenario_id="s", mode="real", state="running")
    run.tick_index = 3
    return run


@pytest.mark.asyncio
async def test_the_money_conflict_message_names_the_conflict_not_the_class(caplog) -> None:
    tick = unit_tick(_real_max_consec_money_no_progress_limit=0)
    run = _run()

    with caplog.at_level(logging.WARNING):
        await tick._record_money_conflict_tick(
            run.run_id, run=run, conflict="serialization_failure", error=_SecretFailureError("x")
        )

    message = run.last_error["message"]
    assert run.last_error["code"] == "REAL_MODE_MONEY_CONFLICT_UNRESOLVED"
    assert "serialization_failure" in message, message
    assert "_SecretFailureError" not in message, message
    logged = [r.getMessage() for r in caplog.records if "money_phase_no_progress" in r.getMessage()]
    assert logged and all(f"run_id={run.run_id}" in m and "error_type=_SecretFailureError" in m for m in logged), logged


class _Rows:
    def __init__(self, row) -> None:
        self._row = row

    def one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self) -> list:
        return []


class _Session:
    def __init__(self, row) -> None:
        self._row = row

    async def execute(self, _statement) -> _Rows:
        return _Rows(self._row)


class _SessionContext:
    def __init__(self, row) -> None:
        self._row = row

    async def __aenter__(self) -> _Session:
        return _Session(self._row)

    async def __aexit__(self, *_exc) -> None:
        return None


class _Sse:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _NoEdgePatches:
    async def build_edge_patch_for_pairs(self, **_kwargs) -> list:
        return []


_A, _B = uuid.uuid5(uuid.NAMESPACE_DNS, "a.fd6"), uuid.uuid5(uuid.NAMESPACE_DNS, "b.fd6")


def _failing_pass(*, after_progress: bool):
    from app.core.clearing.runner import (
        ClearingPassError,
        ClearingPassResult,
        CommittedEdge,
        CommittedOccurrence,
        InterruptReason,
    )

    async def _pass(_session_factory, _equivalent, *, allowed_participant_pids, on_committed, deadline):
        if not after_progress:
            raise _SecretFailureError("the pass failed before any cycle")
        occurrence = CommittedOccurrence(
            occurrence_id=str(uuid.uuid4()), plan_id=uuid.uuid4(), ordinal=0, amount_atoms=int(Decimal("1").scaleb(8)),
            edges=(CommittedEdge(uuid.uuid4(), _A, _B),), after_cancellation=False,
        )
        on_committed(occurrence)
        result = ClearingPassResult("USD", "interrupted", InterruptReason.ERROR, (occurrence,), 1, 100, 1, False)
        raise ClearingPassError(result, _SecretFailureError("the pass failed after one cycle"))

    return _pass


@pytest.mark.asyncio
@pytest.mark.parametrize("after_progress, token", [(True, "pass_error"), (False, "clearing_error")])
async def test_the_clearing_outcome_reason_names_the_ending_not_the_class(monkeypatch, after_progress, token) -> None:
    async def _no_growth(**_kwargs):
        return SimpleNamespace(updated_count=0)

    tick = clearing_unit_tick(
        monkeypatch, sse=_Sse(), session_factory=lambda: _SessionContext((2,)),
        runner_pass=_failing_pass(after_progress=after_progress), apply_trust_growth=_no_growth,
        edge_patch_builder=_NoEdgePatches(),
    )
    run = _run()
    run._edges_by_equivalent = {"USD": [("b", "a")]}
    run._real_participants = [(_A, "a"), (_B, "b")]
    outcomes: dict[str, str] = {}

    await tick._run_clearing(session=None, run_id=run.run_id, run=run, equivalents=["USD"], committed={},
                             outcomes=outcomes)

    assert outcomes == {"USD": token}, outcomes
    assert run.last_error and run.last_error["code"] == "CLEARING_ERROR", run.last_error
    assert "_SecretFailureError" not in str(run.last_error), run.last_error
