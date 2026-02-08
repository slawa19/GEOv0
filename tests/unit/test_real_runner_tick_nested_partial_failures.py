import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner, _RealPaymentAction
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.schemas.payment import PaymentResult
from app.utils.exceptions import BadRequestException


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _DummyNested:
    def __init__(self, session: "_DummySession") -> None:
        self._s = session

    async def __aenter__(self):
        self._s.nested_begins += 1
        return None

    async def __aexit__(self, exc_type, exc, tb):
        # On error, SQLAlchemy would rollback SAVEPOINT; we only track.
        self._s.nested_ends += 1
        return False


class _DummySession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.nested_begins = 0
        self.nested_ends = 0

    def begin_nested(self):
        return _DummyNested(self)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(self, *args, **kwargs):
        # tick_real_mode has best-effort snapshot queries wrapped in try/except.
        raise RuntimeError("dummy execute")


class _DummySessionCtx:
    def __init__(self, s: _DummySession) -> None:
        self._s = s

    async def __aenter__(self) -> _DummySession:
        return self._s

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummySse:
    def __init__(self) -> None:
        self.types: list[str] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run.run_id}_{run._event_seq:06d}"

    def broadcast(self, run_id: str, payload: dict) -> None:
        self.types.append(str(payload.get("type") or ""))


class _DummyArtifacts:
    def enqueue_event_artifact(self, run_id: str, payload: dict) -> None:
        return None

    def write_real_tick_artifact(self, run: RunRecord, payload: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_real_runner_tick_real_mode_uses_nested_tx_and_survives_one_action_error(
    monkeypatch,
):
    # Arrange: a seeded run with preloaded participants/equivalents so tick_real_mode
    # doesn't need real DB reads.
    run = RunRecord(
        run_id="r1",
        scenario_id="s1",
        mode="real",
        state="running",
        started_at=_utc_now(),
    )
    run.seed = 123
    run.tick_index = 1
    run.sim_time_ms = 1000
    run.intensity_percent = 90
    run._real_seeded = True
    run._real_participants = [
        ("00000000-0000-0000-0000-000000000001", "A"),
        ("00000000-0000-0000-0000-000000000002", "B"),
    ]
    run._real_equivalents = ["UAH"]

    sse = _DummySse()
    session = _DummySession()

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: run,
        get_scenario_raw=lambda _scenario_id: {
            "equivalents": ["UAH"],
            "participants": [],
        },
        sse=sse,
        artifacts=_DummyArtifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: False,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )

    async def _noop_apply_due(*args, **kwargs):
        return None

    monkeypatch.setattr(
        runner, "_apply_due_scenario_events", _noop_apply_due, raising=True
    )

    # Force exactly two actions.
    def _plan_two(*args, **kwargs):
        return [
            _RealPaymentAction(
                seq=0, equivalent="UAH", sender_pid="A", receiver_pid="B", amount="1.00"
            ),
            _RealPaymentAction(
                seq=1, equivalent="UAH", sender_pid="B", receiver_pid="A", amount="1.00"
            ),
        ]

    monkeypatch.setattr(runner, "_plan_real_payments", _plan_two, raising=True)

    # Patch DB session factory used inside tick_real_mode.
    import app.core.simulator.real_runner as real_runner_mod

    monkeypatch.setattr(
        real_runner_mod.db_session,
        "AsyncSessionLocal",
        lambda: _DummySessionCtx(session),
        raising=True,
    )

    # Patch payment service so first action fails, second commits.
    from app.core.payments.service import PaymentService

    calls = {"n": 0}

    async def _fake_create_payment_internal(
        self,
        sender_id,
        *,
        to_pid,
        equivalent,
        amount,
        idempotency_key,
        commit: bool = True,
    ):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BadRequestException("bad payment")
        return PaymentResult(
            tx_id="tx1",
            status="COMMITTED",
            **{"from": "A"},
            to=str(to_pid),
            equivalent=str(equivalent),
            amount=str(amount),
            routes=None,
            error=None,
            created_at=_utc_now().isoformat(),
            committed_at=None,
        )

    monkeypatch.setattr(
        PaymentService,
        "create_payment_internal",
        _fake_create_payment_internal,
        raising=True,
    )

    # Make viz patches fast-fail (they are best-effort and must not abort the tick).
    async def _viz_create_fail(*args, **kwargs):
        raise RuntimeError("skip")

    monkeypatch.setattr(VizPatchHelper, "create", _viz_create_fail, raising=True)

    # Act
    await runner.tick_real_mode("r1")

    # Assert: we used SAVEPOINT per action and committed once for the whole tick.
    assert session.nested_begins == 2
    assert session.nested_ends == 2
    assert session.commits == 1
    assert "tx.failed" in sse.types
    assert "tx.updated" in sse.types
