"""Programme 019 stage 5, `T1908` (d): CLEARING STARVATION under a continuous stream of successful payments.

A PROBE, NOT A GATE: marked `slow`, never in the default tier. Re-run with

    .\\scripts\\verify_local.ps1 -TaskSlug <slug> -BackendOnly -IncludeExpensive `
      -BackendSelector tests/integration/test_p019_t1908_clearing_starvation_probe_postgres.py

and read `p019_t1908_starvation.jsonl` under `GEO_TEST_ARTIFACT_ROOT` (the canonical runner sets it to
`.local-run/test-runs/<slug>/artifacts`; one line per configuration with every run, its summary, and the
verdict line), also printed as `T1908-STARVATION` lines with `-s`. The numbers of a run are recorded in the
spec with the date; they describe THIS load on THIS machine, not the absence of starvation in general (spec:
"Конечный успешный эксперимент подтверждает проверенную нагрузку, а не отсутствие голодания вообще").

THE QUESTION. Without the equivalent owner lock, the clearing's only protection against a stream of
payments over the edges of its cycle is its own retry budget: each attempt takes a SERIALIZABLE snapshot,
re-reads the cycle `FOR UPDATE`, and meets 40001 if any of those rows was committed by a payment since the
snapshot. If payments commit often enough, every attempt of the budget can lose - the clearing starves.
With the owner lock, the clearing queues on it once and then no payment of the equivalent commits until
the clearing does.

WRITTEN DOWN BEFORE THE COMPARISON (spec, item 5 and Verification plan: the criterion first):

* LOAD. One equivalent, the cycle A->B, B->C, C->A (100 each) and lines of 100 000 with auto-clearing;
  `WORKERS` concurrent payers (one per edge, round-robin), each paying 1.00 along the cycle in a loop
  through `PaymentService.pay` - every payment writes one of the three cycle rows. The clearing of the
  cycle starts after `WARMUP_S` of stream, and the stream runs until the clearing has ended. Budget: the
  shipped one (`COMMIT_RETRY_ATTEMPTS`, backoff, `PAYMENT_TOTAL_TIMEOUT_SECONDS`).
* CANDIDATE. The cycle is executable: without the stream, the same clearing clears 100 (the no-load
  control, asserted).
* PROGRESS OF THE PRODUCER. Payments committed during the window and payments/s; every payment must
  succeed or end as the typed retryable conflict - nothing else (asserted).
* CRITERION. A run STARVES when the clearing ends with the typed retryable refusal (its budget exhausted by
  conflicts). A configuration shows no starvation under this load when at least 90 % of its runs clear.
* POSITIVE CONTROL, FIRST. The same load with the locks off and a deliberately widened window - `GAP_S`
  of sleep between each attempt's snapshot and its re-read of the cycle - must be SEEN to starve: at most
  10 % of its runs clear, with the exhaustion observed. Without that, a "no starvation" below would be a
  stand that cannot see starvation.
* COMPARISON. `locks_on` and `locks_off` without the gap, `RUNS` runs each, same load; medians and spread
  (min-max) of clearing attempts, clearing conflicts, time to the clearing's outcome, payment throughput
  and payment conflicts.

Order of the owner-lock queue by itself proves nothing (spec): a transaction that lost a conflict releases
its locks and retries - so the probe measures outcomes (cleared or exhausted), not queue order.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService, RetryableClearingConflictException
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import RetryablePaymentConflictException
from tests.debt_setup import debt_fixture_setup
from tests.p019_locks_off import switch_money_boundary_locks_off

pytestmark = [pytest.mark.slow]

# The load and the run count can be raised for a heavier measurement without editing the file.
WORKERS = int(os.environ.get("P019_STARVATION_WORKERS", "6"))
WARMUP_S = 0.4
RUNS = int(os.environ.get("P019_STARVATION_RUNS", "7"))
CONTROL_RUNS = 5
GAP_S = 0.15
NO_STARVATION_SHARE = 0.9
CONTROL_MAX_SHARE = 0.1


@pytest_asyncio.fixture
async def stand(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=WORKERS + 6, max_overflow=0, pool_timeout=30,
        isolation_level="SERIALIZABLE",
    )
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@dataclass
class Run:
    cleared: Decimal | None = None
    outcome: str = ""
    clearing_attempts: int = 0
    clearing_conflicts: int = 0
    clearing_s: float = 0.0
    payments_committed: int = 0
    payments_conflicted: int = 0
    payment_retries: int = 0
    payments_other: list[str] = field(default_factory=list)
    window_s: float = 0.0

    @property
    def throughput(self) -> float:
        return self.payments_committed / self.window_s if self.window_s else 0.0


async def _seed(stand):
    n = uuid.uuid4().hex[:8].upper()
    async with stand() as s:
        eq = Equivalent(code=f"ST{n}"[:16], precision=2, is_active=True)
        a, b, c = [
            Participant(pid=f"{k}_ST_{n}", display_name=k, public_key=f"pk_{k}_st_{n}", type="person", status="active")
            for k in "ABC"
        ]
        s.add_all([eq, a, b, c])
        await s.flush()
        s.add_all(
            [
                TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
                          limit=Decimal("100000.00"), policy={"auto_clearing": True}, status="active")
                for debtor, creditor in ((a, b), (b, c), (c, a))
            ]
        )
        debts = [
            Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal("100.00"))
            for debtor, creditor in ((a, b), (b, c), (c, a))
        ]
        async with debt_fixture_setup(s, label="starvation-setup"):
            s.add_all(debts)
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, (a, b, c), [{"debt_id": str(d.id)} for d in debts]


def _instrument(monkeypatch, *, gap_s: float):
    counts = {"attempts": 0, "clearing_conflicts": 0, "payment_retries": 0}
    original_attempt = ClearingService._execute_clearing_with_amount

    async def attempt(self, cycle, **kwargs):
        counts["attempts"] += 1
        return await original_attempt(self, cycle, **kwargs)

    monkeypatch.setattr(ClearingService, "_execute_clearing_with_amount", attempt)

    original_retryable = ClearingService._is_retryable_concurrency_error.__func__

    def retryable(cls, exc):
        result = original_retryable(cls, exc)
        if result:
            counts["clearing_conflicts"] += 1
        return result

    monkeypatch.setattr(ClearingService, "_is_retryable_concurrency_error", classmethod(retryable))

    if gap_s:
        original_read = ClearingService._committed_execution_amount

        async def widened(self, tx_id, *, allowed_participant_pids=None):
            amount = await original_read(self, tx_id, allowed_participant_pids=allowed_participant_pids)
            await asyncio.sleep(gap_s)  # THE DELIBERATELY STARVING GAP: snapshot taken, cycle not yet locked
            return amount

        monkeypatch.setattr(ClearingService, "_committed_execution_amount", widened)

    original_retry = PaymentService._retry_or_none

    def retry_or_none(self, exc, **kwargs):
        counts["payment_retries"] += 1
        return original_retry(self, exc, **kwargs)

    monkeypatch.setattr(PaymentService, "_retry_or_none", retry_or_none)
    return counts


async def _one_run(stand, counts, *, with_stream: bool) -> Run:
    for key in counts:
        counts[key] = 0
    eq, (a, b, c), cycle = await _seed(stand)
    run = Run()
    stop = asyncio.Event()
    edges = [(a, b), (b, c), (c, a)]  # payer -> payee: each payment grows the payer's cycle debt

    async def payer(index: int) -> None:
        sender, receiver = edges[index % 3]
        while not stop.is_set():
            request = PaymentCreateRequest(
                tx_id=str(uuid.uuid4()), to=receiver.pid, equivalent=eq.code, amount="1.00", signature="__internal__"
            )
            try:
                result = await PaymentService.pay(stand, sender.id, request, require_signature=False)
                if result.status == "COMMITTED":
                    run.payments_committed += 1
                else:
                    run.payments_other.append(result.status)
            except RetryablePaymentConflictException:
                run.payments_conflicted += 1
            except Exception as exc:  # noqa: BLE001 - recorded and asserted empty
                run.payments_other.append(repr(exc)[:120])

    workers = [asyncio.create_task(payer(i)) for i in range(WORKERS)] if with_stream else []
    started = time.monotonic()
    try:
        if with_stream:
            await asyncio.sleep(WARMUP_S)
        clearing_started = time.monotonic()
        async with stand() as session:
            try:
                run.cleared = await ClearingService(session).execute_clearing_with_amount(cycle)
                run.outcome = "cleared" if run.cleared else "skipped"
            except RetryableClearingConflictException:
                run.outcome = "exhausted"
            except Exception as exc:  # noqa: BLE001 - recorded
                run.outcome = f"error:{type(exc).__name__}"
        run.clearing_s = time.monotonic() - clearing_started
        if with_stream:
            await asyncio.sleep(0.1)
    finally:
        stop.set()
        if workers:
            await asyncio.wait(workers, timeout=30)
        run.window_s = time.monotonic() - started
        PaymentRouter.invalidate_cache(eq.code)
    run.clearing_attempts = counts["attempts"]
    run.clearing_conflicts = counts["clearing_conflicts"]
    run.payment_retries = counts["payment_retries"]
    return run


def _summary(name: str, runs: list[Run]) -> dict:
    def spread(values):
        return {
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }

    cleared = sum(1 for r in runs if r.outcome == "cleared")
    summary = {
        "configuration": name,
        "runs": len(runs),
        "cleared": cleared,
        "exhausted": sum(1 for r in runs if r.outcome == "exhausted"),
        "other_outcomes": sorted({r.outcome for r in runs} - {"cleared", "exhausted"}),
        "clearing_attempts": spread([r.clearing_attempts for r in runs]),
        "clearing_conflicts": spread([r.clearing_conflicts for r in runs]),
        "clearing_s": spread([round(r.clearing_s, 3) for r in runs]),
        "payments_committed": spread([r.payments_committed for r in runs]),
        "payments_per_s": spread([round(r.throughput, 1) for r in runs]),
        "payment_retries": spread([r.payment_retries for r in runs]),
        "payments_conflicted": spread([r.payments_conflicted for r in runs]),
    }
    _record({**summary, "each_run": [asdict(r) for r in runs]})
    return summary


def _record(values: dict) -> None:
    line = json.dumps(values, default=str, sort_keys=True)
    print("T1908-STARVATION " + line)
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/t1908/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "p019_t1908_starvation.jsonl").open("a", encoding="utf-8") as out:
        out.write(line + "\n")


def _assert_the_producer_progressed(runs: list[Run]) -> None:
    for r in runs:
        assert r.payments_other == [], f"payments ended otherwise than committed/conflict: {r.payments_other}"
        assert r.payments_committed > 0, "the stream made no progress: a clearing outcome here says nothing"


@pytest.mark.asyncio
async def test_d_clearing_starvation_probe(stand, monkeypatch) -> None:
    # The no-load control: the candidate IS executable.
    with monkeypatch.context() as patch:
        counts = _instrument(patch, gap_s=0.0)
        no_load = await _one_run(stand, counts, with_stream=False)
    assert no_load.outcome == "cleared" and no_load.cleared == Decimal("100.00000000"), no_load
    assert no_load.clearing_attempts == 1 and no_load.clearing_conflicts == 0, no_load

    results: dict[str, dict] = {}
    configurations = [
        ("positive_control_locks_off_gap", True, GAP_S, CONTROL_RUNS),
        ("locks_on", False, 0.0, RUNS),
        ("locks_off", True, 0.0, RUNS),
    ]
    for name, locks_off, gap_s, runs_n in configurations:
        with monkeypatch.context() as patch:
            if locks_off:
                switch = switch_money_boundary_locks_off(patch)
            counts = _instrument(patch, gap_s=gap_s)
            runs = [await _one_run(stand, counts, with_stream=True) for _ in range(runs_n)]
            if locks_off:
                assert switch.total > 0, "the lock switch was never on the measured path"
        _assert_the_producer_progressed(runs)
        results[name] = _summary(name, runs)

    control = results["positive_control_locks_off_gap"]
    assert control["cleared"] <= CONTROL_MAX_SHARE * control["runs"] and control["exhausted"] >= 1, (
        f"POSITIVE CONTROL FAILED: the deliberately starving configuration was not seen to starve - the "
        f"stand cannot detect starvation, and the comparison below means nothing: {control}"
    )
    for name in ("locks_on", "locks_off"):
        assert results[name]["other_outcomes"] == [], results[name]
    # The verdict per configuration is recorded, not asserted: this is an experiment (spec, T1908).
    _record(
        {
            "verdict": {
                name: (
                    "no starvation under this load"
                    if results[name]["cleared"] >= NO_STARVATION_SHARE * results[name]["runs"]
                    else "STARVES under this load"
                )
                for name in ("locks_on", "locks_off")
            },
            "load": {"workers": WORKERS, "warmup_s": WARMUP_S, "runs": RUNS, "control_runs": CONTROL_RUNS,
                     "gap_s": GAP_S},
        }
    )
