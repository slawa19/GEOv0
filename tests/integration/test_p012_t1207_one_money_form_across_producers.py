"""T1207: one money string, produced one way, by every producer in the simulator.

WHY THIS MODULE EXISTS.  `T1201` replaced the two money producers in
`app/core/simulator/edge_patch_builder.py` with a shared `to_money_str`, whose rule is that
`Equivalent.precision` sets the MINIMUM number of fraction digits and never the maximum.  It
left the other producers on `format(v.quantize(1/10**precision, ROUND_DOWN), "f")`.  The result
was worse than the defect it fixed: for a `HOUR` trustline (`precision: 1`) carrying a stored
`0.05`, the SSE edge patch said `used: "0.05"` while `GET /simulator/graph/snapshot` said
`used: "0.0"`.  Two views of one number, disagreeing.  A reader cannot tell which is the
ledger.

WHAT THIS MODULE PINS, in order:

  1. **Agreement.**  The same trustline, at the same moment, rendered by
     `EdgePatchBuilder.build_edge_patch_for_equivalent` (the SSE `topology.edge_patch` path),
     by `EdgePatchBuilder.build_edge_patch_for_pairs` (the per-transaction and
     `clearing.done` path) and by `SnapshotBuilder.build_graph_snapshot` (the REST snapshot)
     must be the same string, byte for byte, at every precision.  Same for a participant's
     `net_balance` across the snapshot and `VizPatchHelper.compute_node_patches`.
     All three real producers are driven; none of their logic is copied here, because a copy
     would agree with itself no matter what the shipped code does.

  2. **One scale for `clearing.done.cleared_amount`, cancelled or not.**  `T1203` recorded
     that `real_clearing_engine.py` produced this one field at two scales - by
     `Equivalent.precision` on the happy path and by a hard-coded `Decimal("0.01")` on the
     `CancelledError` path, which never reached the precision-aware line.  Confirmed and
     pinned below by driving the real engine down both paths at several precisions.

  3. **No exponential money, from any producer.**  `T1200` measured that `E+` is unreachable
     from `Numeric(20, 8)` storage (every value read out has exponent -8, and subtraction
     cannot raise it) and that `E-` is the reachable violation.  That holds for producers
     reading the ledger.  It does NOT hold for `topology.changed`, whose trustline `limit` is
     rendered from the SCENARIO, before storage: `Decimal(str("1e3"))` is `Decimal('1E+3')`
     and `str()` of it is the literal text `1E+3`.  Both signs are exercised.

  4. **Nothing already-correct moved.**  A differential over values exactly representable at
     each precision, against the exact expression each producer used before, at precisions
     0..18.  Asserted as a comparison against the old code, not as a table of expected
     strings - a test carrying its own expected two-digit constants is forbidden by section 4
     of the verification plan, and would only be checking itself.

WHAT WOULD CATCH WHAT.  Test 1 fails the moment any one producer is changed, or left behind,
independently of the others - which is exactly the state `T1201` created and this task exists
to end.  Test 2 fails if the cancel path drifts back to its own scale.  Test 3 fails if any
producer returns to `str(Decimal)`.  Test 4 fails if the minimum-digits rule is quietly turned
back into a maximum.

Default tier, deliberately.  Nothing here depends on how the database rounds on write - the
subject is what the code does with a `Decimal` it already holds - so this must be visible to
the tier that runs on every change.  The storage half is `RT-012-1`/`RT-012-2`, which are
Postgres-gated for the reason recorded in their docstrings.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clearing.service import ClearingCommittedAfterCancellation
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.snapshot_builder import SnapshotBuilder
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

_LOG = logging.getLogger("test.p012.t1207")

# `Equivalent.precision` is declared `ge=0, le=18` (app/schemas/equivalents.py).  The set below
# spans the shipped values (`HOUR` is 1, the default is 2) and both ends of that range.
PRECISIONS = [0, 1, 2, 4, 8, 18]

_WHY_AGREEMENT = (
    "WHY THIS MATTERS: these are two views of one number.  A participant who opens the graph "
    "and a client that follows the SSE stream must be looking at the same obligation.  When "
    "they disagree, nothing in the system says which one is the ledger, and the disagreement "
    "is silent in the direction that hides money.  Do not fix this by relaxing the assertion "
    "to whichever string the code currently produces on one side: the requirement is that the "
    "two sides are produced by the same function, and the assertion is written as an equality "
    "between real producers precisely so that it cannot be satisfied by a copy."
)


def _utc_now() -> datetime:
    return datetime(2026, 8, 24, tzinfo=timezone.utc)


async def _fixture(session: AsyncSession, *, precision: int, limit: str, used: str):
    """A committed creditor -> debtor trustline with a debt against it."""

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce).upper()[:16], precision=precision, is_active=True)
    creditor = Participant(
        pid=f"CR{nonce}",
        display_name="creditor",
        public_key=f"pk-cr-{nonce}",
        type="person",
        status="active",
        profile={},
    )
    debtor = Participant(
        pid=f"DB{nonce}",
        display_name="debtor",
        public_key=f"pk-db-{nonce}",
        type="person",
        status="active",
        profile={},
    )
    session.add_all([eq, creditor, debtor])
    await session.commit()

    session.add_all(
        [
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq.id,
                limit=Decimal(limit),
                status="active",
                policy={"auto_clearing": True},
            ),
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal(used),
            ),
        ]
    )
    await session.commit()
    return eq, creditor, debtor


def _run_for(eq, creditor, debtor) -> RunRecord:
    run = RunRecord(
        run_id=f"t1207-{uuid.uuid4().hex[:8]}",
        scenario_id=f"scn-{uuid.uuid4().hex[:8]}",
        mode="real",
        state="running",
    )
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]
    run._scenario_raw = {
        "participants": [
            {"id": creditor.pid, "name": "creditor", "type": "person"},
            {"id": debtor.pid, "name": "debtor", "type": "person"},
        ],
        # The snapshot's link set comes from the scenario topology; the money on it is then
        # re-derived from the database rows created above.
        "trustlines": [
            {"from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "limit": "0"}
        ],
    }
    return run


async def _snapshot_link(session: AsyncSession, run: RunRecord, eq_code: str):
    builder = SnapshotBuilder(
        lock=threading.RLock(),
        runs={run.run_id: run},
        scenarios={},
        utc_now=_utc_now,
        db_enabled=lambda: True,
    )
    snap = await builder.build_graph_snapshot(
        run_id=run.run_id, equivalent=eq_code, session=session
    )
    assert len(snap.links) == 1, f"expected one link in the snapshot, got {snap.links!r}"
    return snap


# ---------------------------------------------------------------------------
# 1. Agreement between the views
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", PRECISIONS)
@pytest.mark.parametrize(
    ("limit", "used"),
    [
        # Exactly representable at every precision in PRECISIONS: the byte-for-byte
        # counter-check that already-correct values did not move.
        ("100", "30"),
        # The RT-012-2 value: storable at scale 8, NOT representable at precision 0 or 1.
        ("10.00000000", "0.05"),
        # One storage quantum short of a fully used line: `available` is the Decimal whose
        # `str()` is the literal `1E-8` that T1200 measured on the wire.
        ("100.00000000", "99.99999999"),
    ],
)
async def test_the_edge_patch_and_the_snapshot_report_the_same_trustline(
    db_session: AsyncSession, precision: int, limit: str, used: str
) -> None:
    """The SSE edge patch and the REST snapshot must render one trustline identically."""

    eq, creditor, debtor = await _fixture(
        db_session, precision=precision, limit=limit, used=used
    )
    run = _run_for(eq, creditor, debtor)

    patches = await EdgePatchBuilder(logger=_LOG).build_edge_patch_for_equivalent(
        session=db_session, run=run, equivalent_code=eq.code
    )
    assert len(patches) == 1, f"expected one edge patch, got {patches!r}"
    patch = patches[0]

    snap = await _snapshot_link(db_session, run, eq.code)
    link = snap.links[0]

    for field, edge_key in (("trust_limit", "trust_limit"), ("used", "used"), ("available", "available")):
        assert getattr(link, field) == patch[edge_key], (
            f"the snapshot and the SSE edge patch disagree about {field} on the same "
            f"trustline at precision {precision}: snapshot says "
            f"{getattr(link, field)!r}, edge patch says {patch[edge_key]!r}.  The stored "
            f"values are limit={limit!r} used={used!r}.  These are the two producers "
            f"`app/core/simulator/snapshot_builder.py` and "
            f"`app/core/simulator/edge_patch_builder.py`, and they must call the same "
            f"renderer.\n{_WHY_AGREEMENT}"
        )

    # And the money reported is the money stored - the property that made the disagreement a
    # defect rather than a cosmetic difference.
    assert Decimal(patch["used"]) == Decimal(used), (
        f"at precision {precision} the edge patch reports used={patch['used']!r} for a stored "
        f"debt of {used!r}.  A rendering may add digits; it may not change the number.\n"
        f"{_WHY_AGREEMENT}"
    )
    assert Decimal(link.trust_limit) == Decimal(limit)
    assert Decimal(link.available) == max(Decimal("0"), Decimal(limit) - Decimal(used))


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", PRECISIONS)
async def test_the_per_transaction_patch_agrees_with_the_other_two(
    db_session: AsyncSession, precision: int
) -> None:
    """`build_edge_patch_for_pairs` is the third producer of `used`/`available`."""

    eq, creditor, debtor = await _fixture(
        db_session, precision=precision, limit="100.00000000", used="99.99999999"
    )
    run = _run_for(eq, creditor, debtor)

    builder = EdgePatchBuilder(logger=_LOG)
    by_equivalent = (
        await builder.build_edge_patch_for_equivalent(
            session=db_session, run=run, equivalent_code=eq.code
        )
    )[0]

    helper = await VizPatchHelper.create(db_session, equivalent_code=eq.code)
    by_pairs = (
        await builder.build_edge_patch_for_pairs(
            session=db_session,
            helper=helper,
            edges_pairs=[(creditor.pid, debtor.pid)],
            pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
        )
    )[0]

    snap = await _snapshot_link(db_session, run, eq.code)

    for field in ("used", "available"):
        assert by_equivalent[field] == by_pairs[field] == getattr(snap.links[0], field), (
            f"the three producers of {field} disagree at precision {precision}: "
            f"build_edge_patch_for_equivalent={by_equivalent[field]!r}, "
            f"build_edge_patch_for_pairs={by_pairs[field]!r}, "
            f"snapshot={getattr(snap.links[0], field)!r}.\n{_WHY_AGREEMENT}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", PRECISIONS)
async def test_net_balance_agrees_between_the_snapshot_and_the_node_patch(
    db_session: AsyncSession, precision: int
) -> None:
    """`net_balance` has two producers as well: the snapshot and `VizPatchHelper`."""

    eq, creditor, debtor = await _fixture(
        db_session, precision=precision, limit="10.00000000", used="0.05"
    )
    run = _run_for(eq, creditor, debtor)

    helper = await VizPatchHelper.create(db_session, equivalent_code=eq.code)
    node_patches = await helper.compute_node_patches(
        db_session,
        pids=[creditor.pid, debtor.pid],
        pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
    )
    by_pid = {p["id"]: p for p in node_patches}

    snap = await _snapshot_link(db_session, run, eq.code)
    snap_by_pid = {n.id: n for n in snap.nodes}

    for pid in (creditor.pid, debtor.pid):
        assert by_pid[pid]["net_balance"] == snap_by_pid[pid].net_balance, (
            f"the node patch and the snapshot disagree about {pid}'s net_balance at "
            f"precision {precision}: {by_pid[pid]['net_balance']!r} vs "
            f"{snap_by_pid[pid].net_balance!r}.\n{_WHY_AGREEMENT}"
        )
    assert Decimal(by_pid[creditor.pid]["net_balance"]) == Decimal("0.05"), (
        f"the creditor is owed 0.05 and the node patch reports "
        f"{by_pid[creditor.pid]['net_balance']!r} at precision {precision}"
    )


# ---------------------------------------------------------------------------
# 2. `clearing.done.cleared_amount`: one scale, cancelled or not
# ---------------------------------------------------------------------------


class _ClearingScalarResult:
    def scalars(self):
        return self

    def all(self) -> list:
        return []


class _ClearingSession:
    async def execute(self, _statement) -> _ClearingScalarResult:
        return _ClearingScalarResult()


class _ClearingSessionContext:
    async def __aenter__(self) -> _ClearingSession:
        return _ClearingSession()

    async def __aexit__(self, *_exc) -> None:
        return None


class _SseCapture:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"event-{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _NoopEdgePatchBuilder:
    async def build_edge_patch_for_pairs(self, **_kwargs) -> list:
        return []


def _viz_helper(precision: int):
    class _H:
        async def maybe_refresh_quantiles(self, *_a, **_k) -> None:
            return None

        async def compute_node_patches(self, *_a, **_k) -> list:
            return []

    h = _H()
    h.precision = precision
    return h


# The amount is chosen so that it is NOT representable at precision 0 or 1 and carries more
# digits than scale 2 can hold: the two hard-coded `Decimal("0.01")` sites and the
# precision-aware one therefore produced three visibly different strings for it.
CLEARED = Decimal("5.0625")


class _CycleService:
    """Clears `CLEARED` once, then fails or cancels depending on `failure_kind`."""

    failure_kind = "geo"

    def __init__(self, _session) -> None:
        self.calls = 0
        self.cycle = [{"debtor": "alice", "creditor": "bob", "amount": "11.00"}]

    async def find_cycles(self, _equivalent, *, max_depth, allowed_participant_pids=None):
        return [self.cycle]

    async def execute_clearing_with_amount(self, _cycle, *, allowed_participant_pids=None):
        self.calls += 1
        if self.calls == 1:
            if self.failure_kind == "committed_cancel":
                raise ClearingCommittedAfterCancellation(
                    tx_id="clearing-committed", cleared_amount=CLEARED
                )
            return CLEARED
        if self.failure_kind == "cancelled_execute":
            raise asyncio.CancelledError
        raise RuntimeError("stop after one cycle")


async def _drive_clearing(*, precision: int, failure_kind: str) -> str:
    sse = _SseCapture()
    run = RunRecord(
        run_id=f"t1207-clearing-{uuid.uuid4().hex[:6]}",
        scenario_id="scenario",
        mode="real",
        state="running",
    )
    run.tick_index = 3
    run._real_viz_by_eq["USD"] = _viz_helper(precision)
    run._edges_by_equivalent = {"USD": [("bob", "alice")]}

    engine = RealClearingEngine(
        lock=threading.RLock(),
        sse=sse,
        utc_now=_utc_now,
        logger=_LOG,
        edge_patch_builder=_NoopEdgePatchBuilder(),
        clearing_max_depth_limit=6,
        clearing_max_fx_edges_limit=8,
        real_clearing_time_budget_ms=10_000,
    )
    _CycleService.failure_kind = failure_kind

    async def _apply_trust_growth(**_kwargs):
        return SimpleNamespace(updated_count=0)

    call = engine.tick_real_mode_clearing(
        None,
        run_id=run.run_id,
        run=run,
        equivalents=["USD"],
        apply_trust_growth=_apply_trust_growth,
        build_edge_patch_for_equivalent=lambda **_k: None,
        broadcast_topology_edge_patch=lambda **_k: None,
        async_session_local=lambda: _ClearingSessionContext(),
        clearing_service_cls=_CycleService,
    )
    if failure_kind in {"cancelled_execute", "committed_cancel"}:
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        await call

    done = [e for e in sse.events if e["type"] == "clearing.done"]
    assert len(done) == 1, f"expected exactly one clearing.done, got {sse.events!r}"
    return done[0]["cleared_amount"]


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", PRECISIONS)
async def test_clearing_done_reports_one_scale_whether_or_not_it_was_cancelled(
    precision: int,
) -> None:
    """One field, one scale.  `T1203` found two, chosen by whether the clearing was cancelled.

    Confirmed before the fix: `real_clearing_engine.py` produced `cleared_amount` at
    `:438` (hard-coded `Decimal("0.01")`), re-produced it at `:482` from
    `Equivalent.precision`, and produced it a third time at `:640` on the `CancelledError`
    path with the hard-coded scale again - and the cancel path never passes through `:482`.
    So a `precision`-4 equivalent reported `5.0625` when the clearing completed and `5.06`
    when it was cancelled, and a `precision`-1 equivalent reported `5.0` completed against
    `5.06` cancelled: the cancelled figure was the MORE precise of the two, which is the
    tell that neither was deliberate.
    """

    happy = await _drive_clearing(precision=precision, failure_kind="geo")
    cancelled = await _drive_clearing(precision=precision, failure_kind="cancelled_execute")
    committed_cancel = await _drive_clearing(
        precision=precision, failure_kind="committed_cancel"
    )

    assert happy == cancelled == committed_cancel, (
        f"clearing.done reported cleared_amount at more than one scale for the same amount "
        f"{CLEARED} at precision {precision}: completed={happy!r}, "
        f"cancelled-during-execute={cancelled!r}, committed-after-cancellation="
        f"{committed_cancel!r}.  One field must not change shape according to how the tick "
        f"ended; a consumer diffing successive clearing.done events would read the change of "
        f"scale as a change of amount.\n{_WHY_AGREEMENT}"
    )
    assert Decimal(happy) == CLEARED, (
        f"cleared_amount={happy!r} is not the amount that was cleared ({CLEARED}) at "
        f"precision {precision}.  Rendering may pad; it may not truncate a committed total."
    )
    assert "e" not in happy.lower()


# ---------------------------------------------------------------------------
# 3. No exponential money, from any producer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_producer_reading_the_ledger_emits_exponential_money(
    db_session: AsyncSession,
) -> None:
    """`E-` is the reachable violation for anything read out of `Numeric(20, 8)` (`T1200`)."""

    eq, creditor, debtor = await _fixture(
        db_session, precision=2, limit="100.00000000", used="99.99999999"
    )
    run = _run_for(eq, creditor, debtor)

    # The hazard is real: this is the exact subtraction the producers perform.
    assert str(Decimal("100.00000000") - Decimal("99.99999999")) == "1E-8", (
        "the probe value no longer stringifies exponentially, so this test has stopped "
        "modelling the hazard and must be rewritten rather than deleted."
    )

    builder = EdgePatchBuilder(logger=_LOG)
    helper = await VizPatchHelper.create(db_session, equivalent_code=eq.code)
    produced: dict[str, str] = {}

    for key, value in (
        await builder.build_edge_patch_for_equivalent(
            session=db_session, run=run, equivalent_code=eq.code
        )
    )[0].items():
        if key in {"trust_limit", "used", "available"}:
            produced[f"build_edge_patch_for_equivalent.{key}"] = value

    for key, value in (
        await builder.build_edge_patch_for_pairs(
            session=db_session,
            helper=helper,
            edges_pairs=[(creditor.pid, debtor.pid)],
            pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
        )
    )[0].items():
        if key in {"used", "available"}:
            produced[f"build_edge_patch_for_pairs.{key}"] = value

    snap = await _snapshot_link(db_session, run, eq.code)
    link = snap.links[0]
    produced["snapshot.trust_limit"] = link.trust_limit
    produced["snapshot.used"] = link.used
    produced["snapshot.available"] = link.available
    for node in snap.nodes:
        produced[f"snapshot.net_balance[{node.id}]"] = node.net_balance

    for patch in await helper.compute_node_patches(
        db_session,
        pids=[creditor.pid, debtor.pid],
        pid_to_participant={creditor.pid: creditor, debtor.pid: debtor},
    ):
        produced[f"compute_node_patches.net_balance[{patch['id']}]"] = patch["net_balance"]

    offenders = {k: v for k, v in produced.items() if "e" in str(v).lower()}
    assert not offenders, (
        f"money left the backend in exponential notation: {offenders!r}.  Every client that "
        f"parses these as plain decimal strings sees a malformed value or, worse, silently "
        f"reads a different number.  All producers checked: {sorted(produced)!r}"
    )
    # And the exponentially-spelled value survived as a number, not just as text.
    assert Decimal(produced["build_edge_patch_for_equivalent.available"]) == Decimal("1E-8")
    assert produced["snapshot.available"] == produced[
        "build_edge_patch_for_equivalent.available"
    ]


class _RecordingSse:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"evt_{run._event_seq:06d}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


class _NoopArtifacts:
    def enqueue_event_artifact(self, _run_id: str, _payload: dict) -> None:
        return None

    def write_real_tick_artifact(self, _run: RunRecord, _payload: dict) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_limit", "exponential_spelling"),
    [
        # `E-`: the same class the ledger producers can reach.
        ("0.00000001", "1E-8"),
        # `E+`: NOT reachable from storage (T1200 measured that), but this producer renders a
        # scenario-supplied value before it is ever stored, and `Decimal(str("1e3"))` is
        # `Decimal('1E+3')`.  So the invariant needs both signs after all.
        ("1e3", "1E+3"),
    ],
)
async def test_the_topology_changed_trustline_limit_is_not_exponential(
    db_session: AsyncSession, scenario_limit: str, exponential_spelling: str
) -> None:
    """`inject` renders a trustline limit onto the wire before storage ever sees it."""

    assert str(Decimal(str(scenario_limit))) == exponential_spelling, (
        f"{scenario_limit!r} no longer stringifies as {exponential_spelling!r}; the hazard "
        f"this test models has changed and it must be rewritten rather than deleted."
    )

    nonce = uuid.uuid4().hex[:8]
    eq = Equivalent(code=f"IJ{nonce}".upper()[:16], precision=2, is_active=True)
    a = Participant(
        pid=f"IA_{nonce}",
        display_name="a",
        public_key=f"pk_a_{nonce}"[:64],
        type="person",
        status="active",
    )
    b = Participant(
        pid=f"IB_{nonce}",
        display_name="b",
        public_key=f"pk_b_{nonce}"[:64],
        type="person",
        status="active",
    )
    db_session.add_all([eq, a, b])
    await db_session.flush()

    run = RunRecord(
        run_id=f"t1207-inject-{nonce}",
        scenario_id=f"scn-{nonce}",
        mode="real",
        state="running",
        started_at=_utc_now(),
    )
    run.sim_time_ms = 1000
    run.tick_index = 1
    run._real_seeded = True
    run._real_participants = [(a.id, a.pid), (b.id, b.pid)]
    run._real_equivalents = [eq.code]
    run._edges_by_equivalent = {eq.code: []}
    run._real_viz_by_eq = {}

    sse = _RecordingSse()
    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: None,  # type: ignore[arg-type]
        get_scenario_raw=lambda _sid: {},
        sse=sse,
        artifacts=_NoopArtifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: False,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=_LOG,
    )
    runner._real_enable_inject = True

    scenario: dict[str, Any] = {
        "participants": [{"id": a.pid}, {"id": b.pid}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 500,
                "effects": [
                    {
                        "op": "create_trustline",
                        "from": a.pid,
                        "to": b.pid,
                        "equivalent": eq.code,
                        "limit": scenario_limit,
                    }
                ],
            }
        ],
    }

    await runner._apply_due_scenario_events(
        db_session, run_id=run.run_id, run=run, scenario=scenario
    )

    changed = [e for e in sse.events if e.get("type") == "topology.changed"]
    assert changed, f"inject did not emit topology.changed; events were {sse.events!r}"
    limits = [
        edge.get("limit")
        for event in changed
        for edge in (event.get("payload") or {}).get("added_edges") or []
    ]
    assert limits, f"topology.changed carried no added_edges: {changed!r}"
    for value in limits:
        assert "e" not in str(value).lower(), (
            f"topology.changed put limit={value!r} on the wire - money in exponential "
            f"notation, from `app/core/simulator/inject_executor.py`, which rendered the "
            f"scenario's {scenario_limit!r} with `str(Decimal)`.  T1200's measurement that "
            f"`E+` is unreachable holds only for values read out of Numeric(20, 8); this "
            f"producer renders before storage, so both signs are reachable here."
        )
        assert Decimal(value) == Decimal(str(scenario_limit)), (
            f"topology.changed reported limit={value!r} for a scenario limit of "
            f"{scenario_limit!r}: the rendering changed the number."
        )


# ---------------------------------------------------------------------------
# 4. Nothing already-correct moved
# ---------------------------------------------------------------------------


def _old_precision_quantize(v: Decimal, precision: int) -> str:
    """Verbatim the expression `snapshot_builder`, `viz_patch_helper` and the clearing
    happy path used before T1207 (and `edge_patch_builder` before T1201)."""

    return format(v.quantize(Decimal(1) / (Decimal(10) ** precision), rounding=ROUND_DOWN), "f")


def _old_hardcoded_scale_2(v: Decimal) -> str:
    """Verbatim the expression the clearing cancel path used before T1207."""

    return format(v.quantize(Decimal("0.01"), rounding=ROUND_DOWN), "f")


def _representable_at(precision: int) -> list[Decimal]:
    quantum = Decimal(1).scaleb(-precision)
    out: list[Decimal] = []
    for n in (0, 1, 2, 5, 7, 9, 10, 11, 25, 99, 100, 101, 999, 1000, 12345, 99999999, 10**12 - 1):
        for sign in (1, -1):
            value = Decimal(n) * quantum * sign
            out.append(value)
            out.append(value.quantize(quantum))
            out.append(Decimal(format(value, "f")))
    for spelling in ("0", "1", "10", "100.00", "0.10", "10.25", "1000000", "0.05"):
        candidate = Decimal(spelling)
        if candidate == candidate.quantize(quantum, rounding=ROUND_DOWN):
            out.append(candidate)
    return out


@pytest.mark.parametrize("precision", list(range(0, 19)))
def test_a_value_representable_at_its_precision_renders_exactly_as_before(
    precision: int,
) -> None:
    """The counter-check the verification plan asks for, as a differential, not a table.

    «Правка не меняет уже корректные величины.»  Proved by running the OLD expression and the
    NEW one over the same values and requiring byte equality, rather than by asserting a
    hand-written `"0.05"` - which would only confirm that the author and the code agree.
    """

    values = _representable_at(precision)
    assert values
    mismatches = [
        (str(v), _old_precision_quantize(v, precision), to_money_str(v, precision))
        for v in values
        if _old_precision_quantize(v, precision) != to_money_str(v, precision)
    ]
    assert not mismatches, (
        f"at precision {precision}, {len(mismatches)} of {len(values)} values that ARE "
        f"exactly representable at that precision now render differently than they did "
        f"before T1201/T1207.  The change was only ever licensed to add digits to values the "
        f"precision cannot express; anything already expressible must be byte-identical.  "
        f"First few (value, old, new): {mismatches[:5]!r}"
    )


def test_the_clearing_cancel_path_is_byte_identical_at_the_default_precision() -> None:
    """`precision: 2` is what the repository ships by default, so it is the contract."""

    values = _representable_at(2)
    mismatches = [
        (str(v), _old_hardcoded_scale_2(v), to_money_str(v, 2))
        for v in values
        if _old_hardcoded_scale_2(v) != to_money_str(v, 2)
    ]
    assert not mismatches, (
        f"replacing the hard-coded `Decimal(\"0.01\")` in the clearing cancel path moved "
        f"{len(mismatches)} values at precision 2, where the two forms must coincide "
        f"exactly: {mismatches[:5]!r}"
    )


@pytest.mark.parametrize("precision", list(range(0, 19)))
def test_every_value_that_did_move_was_one_the_precision_could_not_express(
    precision: int,
) -> None:
    """The other half of the counter-check: the change is not merely small, it is bounded.

    Every difference between the old renderer and the new one must be a value that the old
    one was destroying.  Stated as three quantifiers rather than as an example, because
    `T1203` recorded a downgrade in this programme that was right about its example and wrong
    about its quantifier.
    """

    quantum = Decimal(1).scaleb(-precision)
    probes = [
        Decimal(s)
        for s in (
            "0.05", "1E-8", "0.00000001", "0.123456789", "99.999999999",
            "0.1", "1.5", "10.25", "0", "100.00", "-0.05", "-1E-8",
        )
    ]
    for v in probes:
        old = _old_precision_quantize(v, precision)
        new = to_money_str(v, precision)
        if old == new:
            continue
        assert v != v.quantize(quantum, rounding=ROUND_DOWN), (
            f"precision {precision}: {v} IS representable at that precision, yet the "
            f"rendering changed from {old!r} to {new!r}"
        )
        assert Decimal(new) == v, (
            f"precision {precision}: the new rendering {new!r} of {v} is not that number"
        )
        assert Decimal(old) != v, (
            f"precision {precision}: the old rendering {old!r} of {v} was already faithful, "
            f"so there was nothing here to fix and {new!r} is an unlicensed change"
        )
        assert "e" not in new.lower()


def test_the_minimum_digit_promise_does_not_depend_on_the_ambient_decimal_context() -> None:
    """THE RULE at the widest corner the door and the schema jointly admit: 12 + 18 digits.

    `quantize` obeys the ambient context's `prec` (default 28), and a value at the door's
    magnitude bound padded to `precision: 18` has a 30-digit coefficient.  Before this pin,
    `to_money_str(Decimal("999999999999.12345678"), 18)` raised `InvalidOperation` inside the
    renderer's `try`, fell through to the show-all branch, and returned 8 fraction digits
    where the docstring promises at least 18 - found by external review (012, second circle).

    MUTATION THIS CATCHES: removing the `localcontext` sizing around the `quantize` call in
    `app/utils/money.py`.
    """

    value = Decimal("999999999999.12345678")
    rendered = to_money_str(value, 18)
    assert rendered == "999999999999.123456780000000000", (
        f"got {rendered!r}: the value at the magnitude bound must render with the full 18 "
        f"fraction digits its equivalent declares, independent of decimal.getcontext().prec"
    )
    assert Decimal(rendered) == value


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_value_is_refused_loudly_not_rendered_as_zero(bad: str) -> None:
    """`to_money_str(NaN)` raises; it must never print `"0"` over corrupt state.

    T1210 finding 13, second half.  The renderer used to answer `"0"` for `NaN`/`Infinity` -
    a number the ledger does not hold, invented at the one layer whose whole job (T1207) is
    to be faithful to the value.  Non-finite values cannot arrive from honest state (the door
    refuses them on input, `Numeric` arithmetic over finites stays finite, the inject path
    gates on `is_storable_money`), so one reaching the renderer IS corrupt state and the
    renderer must be loud about it.

    MUTATION THIS CATCHES: restoring `return "0"` on the `is_finite()` branch in
    `app/utils/money.py`.
    """

    with pytest.raises(ValueError):
        to_money_str(Decimal(bad), 2)
