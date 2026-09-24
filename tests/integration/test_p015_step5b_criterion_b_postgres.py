"""Programme 015, step 5b on PostgreSQL: the version CHECK on both schema paths, the pre-state read under
real concurrency, and the criterion (b) controls on asyncpg.

WHAT THIS TIER ADDS over `tests/unit/test_p015_step5b_criterion_b.py`:

* THE TWO CONSTRUCTION PATHS. Migration 027 widens `chk_debt_operations_intent_version`; `create_all`
  and `alembic upgrade head` must build the same three version CHECKs, and they must bite the same way:
  intent version 2 accepted, 3 refused, a 2 in the schema or money version refused.
* THE PRE-STATE WINDOW, FORCED. The payment commit reads both directions of every flow pair and records
  them before it applies the flows. A barrier holds the commit right after that read while another writer
  changes one of those rows:
    - a writer OUTSIDE the owner lock, at the application's isolation (SERIALIZABLE): the commit must not
      apply a state its record does not describe - measured, it is refused with 40001 and the retry
      reads again; the stand control at READ COMMITTED shows the same race DOES make the record disagree,
      so the stand can see the failure it guards against;
    - an APPLICATION writer (a clearing over the same edge): it waits on the owner lock until the payment
      commits.
* THE PLACEMENT against the advisory locks, which SQLite does not have.
* ASYNCPG spellings for every SQLite control of criterion (b) that does not need the inject stand.

Every test that commits does so on a disposable clone of the migrated template (`committed_database`),
and the clone's drop is the only disposal of what it wrote (018 B0b; until then each test deleted its
rows by id, journal included). The construction-path test builds its own scratch databases.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import tests.unit.test_p015_step5b_criterion_b as unit
from app.core.clearing.service import ClearingService
from app.core.payments.engine import PaymentEngine
from app.db.base import Base
from app.db.models.debt import Debt
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_step5a_reconciliation_postgres import _sqlstates
from tests.integration.test_p015_t1544_operator_stop_races_postgres import _advisory_waiter_exists
from tests.migrated_schema import run_alembic_upgrade_head, scratch_databases
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - fixture, requested by `factory`
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import (
    _edges,
    _prepare_payment,
    _seed_triangle,
    _tx_state,
)
from tests.unit.test_p015_step5a_reconciliation import _fixture_debts, _verify


def _postgres_url() -> str:
    """The refusal that makes every engine this module builds PostgreSQL-only (T1525 guard): it is
    defined HERE, because the guard reads this module's own source for it."""

    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def factory(tier_on_a_clone):
    """The clone's sessionmaker, with `tests.conftest.TestingSessionLocal` rebound to the same clone:
    shared helpers that observe through that name (`pg_locks ... current_database()`) must look at the
    database the test writes to (018 B0b)."""
    yield tier_on_a_clone.sessionmaker


def _serializable_sessions(url: str):
    engine = create_async_engine(url, isolation_level="SERIALIZABLE", poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


# =================================================================================================
# The version CHECK on both construction paths
# =================================================================================================


_VERSION_CONSTRAINTS = (
    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = CAST('debt_operations' AS regclass) AND conname LIKE 'chk_debt_operations_%version'"
)


class _Undo(Exception):
    """Raised inside `engine.begin()` so an accepted probe row is rolled back."""


async def _version_catalogue_and_bites(url: str) -> tuple[dict, dict]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            definitions = {
                name: " ".join(definition.split())
                for name, definition in (await connection.execute(text(_VERSION_CONSTRAINTS))).all()
            }

        async def _attempt(column: str, value: int) -> str | None:
            versions = {"schema_version": 1, "money_encoding_version": 1, "intent_encoding_version": 1, column: value}
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO debt_operations (id, kind, identity, tx_id, intent, intent_digest, "
                            "schema_version, money_encoding_version, intent_encoding_version, opened_at, state) "
                            "VALUES (:id, 'TEST_FIXTURE', :identity, NULL, CAST('{}' AS json), :digest, "
                            ":schema, :money, :intent, now(), 'OPEN')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "identity": f"step5b-probe-{uuid.uuid4()}",
                            "digest": "0" * 64,
                            "schema": versions["schema_version"],
                            "money": versions["money_encoding_version"],
                            "intent": versions["intent_encoding_version"],
                        },
                    )
                    raise _Undo()
            except _Undo:
                return None
            except Exception as exc:  # noqa: BLE001 - the SQLSTATE is the subject
                states = _sqlstates(exc)
                return next(iter(sorted(states)), repr(exc))

        bites = {
            "intent=2": await _attempt("intent_encoding_version", 2),
            "intent=3": await _attempt("intent_encoding_version", 3),
            "money=2": await _attempt("money_encoding_version", 2),
            "schema=2": await _attempt("schema_version", 2),
        }
        return definitions, bites
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_step5b_p_both_construction_paths_widen_only_the_intent_version_and_it_bites() -> None:
    """`create_all` and `alembic upgrade head` agree on the three version CHECKs and refuse the same rows.

    MUTATION: write `IN (1, 2, 3)` in migration 027's upgrade - the definitions differ between the two
    databases and intent version 3 is accepted on the migrated one, red.
    """

    # NOT a skip when the role cannot create databases (T1701): `scratch_databases` raises. Until
    # 2026-09-21 this said "an ABSENT measurement, not a pass" and then reported a pass anyway.
    async with scratch_databases(_postgres_url(), "s5bmig", "s5bmeta") as (
        migrated_url,
        metadata_url,
    ):
        engine = create_async_engine(metadata_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()
        # No preconditioning here: `migrations/env.py` owns the `alembic_version` widening and
        # establishes it inside this run (T1701).
        run_alembic_upgrade_head(migrated_url)

        from_metadata, metadata_bites = await _version_catalogue_and_bites(metadata_url)
        migrated, migrated_bites = await _version_catalogue_and_bites(migrated_url)

        assert len(migrated) == 3 and migrated == from_metadata, (migrated, from_metadata)
        assert "ARRAY[1, 2]" in migrated["chk_debt_operations_intent_version"], migrated
        expected = {"intent=2": None, "intent=3": "23514", "money=2": "23514", "schema=2": "23514"}
        for label, bites in (("metadata", metadata_bites), ("alembic", migrated_bites)):
            assert bites == expected, f"{label}: {bites}"


# =================================================================================================
# The pre-state read: placement against the locks
# =================================================================================================


@pytest.mark.asyncio
async def test_step5b_p_the_prestate_read_follows_every_advisory_lock_and_the_for_share(
    factory, committed_database
) -> None:
    """On PostgreSQL the anchors are real locks: every `pg_advisory_xact_lock` of the commit, then the
    operator-stop `FOR SHARE`, then EXACTLY ONE statement - the batched read of `debts` - then the envelope.

    MUTATION: move `_read_payment_prestate` above `_acquire_segment_advisory_lock_keys` - it now precedes
    an advisory lock and nothing sits between the stop and the envelope, red.
    """
    engine = committed_database.engine
    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("c", "b", "100")])
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(" ".join(str(statement).split()).upper())

    tx_id = await _prepare_payment(factory, triangle, ["a", "b", "c"], Decimal("5"))
    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        async with factory() as session:
            await PaymentEngine(session).commit(tx_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)
    assert await _tx_state(factory, tx_id) == "COMMITTED"

    locks = [i for i, s in enumerate(statements) if "PG_ADVISORY_XACT_LOCK" in s]
    stops = [i for i, s in enumerate(statements) if s.startswith("SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE")]
    envelopes = [i for i, s in enumerate(statements) if s.startswith("INSERT INTO DEBT_OPERATIONS")]
    assert locks and len(stops) == 1 and len(envelopes) == 1, "\n".join(statements)
    assert "FOR SHARE" in statements[stops[0]], statements[stops[0]]
    assert max(locks) < stops[0] < envelopes[0], (locks, stops, envelopes)
    between = statements[stops[0] + 1 : envelopes[0]]
    assert len(between) == 1 and between[0].startswith("SELECT") and " FROM DEBTS " in f"{between[0]} ", between


# =================================================================================================
# The pre-state window, forced
# =================================================================================================


async def _race_a_writer_into_the_prestate_window(factory, monkeypatch, payment_sessions) -> dict:
    """Pause a payment commit right after its pre-state read; move a reverse debt 3 -> 4 from a TEST_FIXTURE
    operation (no owner lock) and commit it; resume. Returns what the commit recorded and did."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100"), ("a", "b", "100")])
    (reverse,) = await _fixture_debts(factory, triangle, [("b", "a", "3")])
    tx_id = await _prepare_payment(factory, triangle, ["a", "b"], Decimal("5"))

    paused, resume = asyncio.Event(), asyncio.Event()
    reads: list[list[dict]] = []
    original = PaymentEngine._read_payment_prestate

    async def _read(self, validated_locks):
        result = await original(self, validated_locks)
        reads.append(result)
        if len(reads) == 1:
            paused.set()
            await asyncio.wait_for(resume.wait(), timeout=60)
        return result

    monkeypatch.setattr(PaymentEngine, "_read_payment_prestate", _read)

    async def _commit() -> None:
        async with payment_sessions() as session:
            await PaymentEngine(session).commit(tx_id)

    task = asyncio.create_task(_commit())
    error: BaseException | None = None
    try:
        await asyncio.wait_for(paused.wait(), timeout=60)
        async with factory() as session:
            debt = (await session.execute(select(Debt).where(Debt.id == reverse.id))).scalar_one()
            async with debt_fixture_setup(session, label="outside-the-owner-lock"):
                debt.amount = Decimal("4")
            await session.commit()
    finally:
        resume.set()
    try:
        await asyncio.wait_for(task, timeout=120)
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a premise
        error = exc
    monkeypatch.undo()

    def _recorded(read: list[dict]) -> dict:
        return {
            (triangle.name(uuid.UUID(item["debtor"])), triangle.name(uuid.UUID(item["creditor"]))): item["amount"]
            for item in read
        }

    return {
        "triangle": triangle,
        "error": error,
        "tx_state": await _tx_state(factory, tx_id),
        "reads": [_recorded(read) for read in reads],
        "edges": await _edges(factory, triangle),
        "outcome": await _verify(factory, triangle.equivalent.id),
    }


@pytest.mark.asyncio
async def test_step5b_p_at_serializable_a_writer_outside_the_owner_lock_cannot_make_the_record_disagree(
    factory, committed_database, monkeypatch
) -> None:
    """The application's isolation. The payment read B -> A = 3 and paused; a fixture moved it to 4 and
    committed. MEASURED: the commit's own update of that row fails with 40001, the unit of work retries,
    the pre-state is READ AGAIN (4), and what is recorded is what is applied: no criterion (b) finding.

    MUTATION: read the pre-state once, outside the retried unit of work (cache it across attempts) - the
    retry applies to 4 what the record says was 3, criterion (b) FAILS, red.
    """

    engine, sessions = _serializable_sessions(committed_database.url)
    seen = None
    try:
        seen = await _race_a_writer_into_the_prestate_window(factory, monkeypatch, sessions)
        assert seen["error"] is None and seen["tx_state"] == "COMMITTED", seen
        assert len(seen["reads"]) == 2, (
            f"premise: the race did not force a retry of the commit, so this measured nothing: {seen['reads']}"
        )
        assert seen["reads"][0][("b", "a")] == "3.00000000" and seen["reads"][1][("b", "a")] == "4.00000000", seen
        assert seen["edges"] == {("a", "b"): Decimal("1.00000000")}, seen["edges"]
        assert unit._b_findings(seen["outcome"]) == [], seen["outcome"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_step5b_p_stand_control_at_read_committed_the_same_race_does_make_the_record_disagree(
    factory, committed_database, monkeypatch
) -> None:
    """THE METER - A DIAGNOSTIC COUNTER-PROBE AT READ COMMITTED, which the application never runs at. The same
    race with the payment on an engine that ASKS for READ COMMITTED itself: no 40001, one read, the payment
    applies to 4 while its record says 3 - and criterion (b) says so. Without this control the SERIALIZABLE
    test above could be green because the race never reached the record at all.

    T1549, 2026-09-14: this used to inherit READ COMMITTED from the shared test engine. That engine now runs
    at the application's isolation, and the inherited form went red (two reads, a retry) - so the level is
    requested explicitly here and checked, never taken from a default.

    This is also a stated BOUNDARY, not a hidden one: the protection against a writer outside the owner lock
    is the transaction isolation. Every application writer of `debts` takes the owner lock (payment,
    clearing, inject); SEED and TEST_FIXTURE do not, and are refused after the baseline.
    """

    engine = create_async_engine(
        committed_database.url, isolation_level="READ COMMITTED", poolclass=NullPool
    )
    read_committed = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    seen = None
    try:
        async with read_committed() as probe:
            level = (await probe.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert str(level).lower() == "read committed", f"stand: the counter-probe is not at READ COMMITTED: {level}"
        seen = await _race_a_writer_into_the_prestate_window(factory, monkeypatch, read_committed)
        assert seen["error"] is None and seen["tx_state"] == "COMMITTED", seen
        assert len(seen["reads"]) == 1 and seen["reads"][0][("b", "a")] == "3.00000000", seen["reads"]
        assert seen["edges"] == {("a", "b"): Decimal("1.00000000")}, seen["edges"]
        kinds = unit._kinds(unit._b_findings(seen["outcome"]))
        assert ("b_prestate_mismatch", str(seen["triangle"].b.id), str(seen["triangle"].a.id)) in kinds, kinds
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_step5b_p_an_application_writer_waits_on_the_owner_lock_through_the_prestate_window(
    factory, monkeypatch
) -> None:
    """A clearing over the edge the paused payment has just read. It WAITS on the owner advisory lock
    (observed in `pg_locks`), runs after the payment commits on the state the payment left, and both
    operations are recomputed in full with no finding.

    MUTATION: make `_preacquire_equivalent_owner_locks_for_tx` acquire nothing - the clearing is not seen
    waiting and commits inside the window, red.
    """

    triangle = await _seed_triangle(
        factory, trustlines=[("b", "a", "100"), ("c", "b", "100"), ("a", "c", "100"), ("a", "b", "100")]
    )
    try:
        debts = await _fixture_debts(factory, triangle, [("a", "b", "10"), ("b", "c", "10"), ("c", "a", "10")])
        tx_id = await _prepare_payment(factory, triangle, ["b", "a"], Decimal("3"))

        paused, resume = asyncio.Event(), asyncio.Event()
        original = PaymentEngine._read_payment_prestate

        async def _read(self, validated_locks):
            result = await original(self, validated_locks)
            paused.set()
            await asyncio.wait_for(resume.wait(), timeout=60)
            return result

        monkeypatch.setattr(PaymentEngine, "_read_payment_prestate", _read)

        async def _commit() -> None:
            async with factory() as session:
                await PaymentEngine(session).commit(tx_id)

        async def _clear():
            async with factory() as session:
                return await ClearingService(session).execute_clearing_with_amount(
                    [{"debt_id": str(debt.id)} for debt in debts]
                )

        payment = asyncio.create_task(_commit())
        clearing = None
        try:
            await asyncio.wait_for(paused.wait(), timeout=60)
            clearing = asyncio.create_task(_clear())
            assert await _advisory_waiter_exists(), "premise: the clearing did not wait on an advisory lock"
            assert not clearing.done(), "the clearing finished inside the payment's pre-state window"
        finally:
            resume.set()
        await asyncio.wait_for(payment, timeout=60)
        cleared = await asyncio.wait_for(clearing, timeout=60)
        monkeypatch.undo()

        assert await _tx_state(factory, tx_id) == "COMMITTED"
        assert cleared == Decimal("7"), f"the clearing did not run on the state the payment left: {cleared!r}"
        assert await _edges(factory, triangle) == {
            ("b", "c"): Decimal("3.00000000"),
            ("c", "a"): Decimal("3.00000000"),
        }
        outcome = await _verify(factory, triangle.equivalent.id)
        assert unit._b_findings(outcome) == [], outcome
        assert unit._coverage(outcome)["full_recomputation"] == {"CLEARING": 1, "PAYMENT": 1}, outcome.detail()
    finally:
        monkeypatch.undo()


# =================================================================================================
# The SQLite controls of criterion (b), on asyncpg
# =================================================================================================


_ASYNCPG_CONTROLS = [
    ("test_step5b_an_honest_payment_records_both_directions_and_is_recomputed_in_full", {}),
    ("test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind", {}),
    ("test_step5b_a_corrupted_payment_record_is_failed", {"corruption": "recorded_delta"}),
    ("test_step5b_a_corrupted_payment_record_is_failed", {"corruption": "intent_flow"}),
    ("test_step5b_a_corrupted_payment_record_is_failed", {"corruption": "prestate"}),
    ("test_step5b_net_neutral_cycle_inflation_on_a_payment_is_failed", {}),
    ("test_step5b_a_v1_payment_is_structural_only_and_never_a_full_recomputation", {}),
    ("test_step5b_a_v1_payment_with_an_edge_outside_its_flow_pairs_is_failed", {}),
    ("test_step5b_an_honest_clearing_is_recomputed_in_full_and_passed", {}),
    ("test_step5b_the_c6_under_clearing_is_failed_by_b_while_a_stays_blind", {}),
    ("test_step5b_a_corrupted_clearing_record_is_failed", {"corruption": "recorded_delta"}),
    ("test_step5b_a_corrupted_clearing_record_is_failed", {"corruption": "clear_amount"}),
    ("test_step5b_a_corrupted_clearing_record_is_failed", {"corruption": "prestate"}),
    ("test_step5b_a_corrupted_clearing_record_is_failed", {"corruption": "cycle_not_closed"}),
    ("test_step5b_a_corrupted_clearing_record_is_failed", {"corruption": "cycle_inflation"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, kwargs",
    _ASYNCPG_CONTROLS,
    ids=[f"{name.removeprefix('test_step5b_')}{'-' + next(iter(k.values())) if k else ''}" for name, k in _ASYNCPG_CONTROLS],
)
async def test_step5b_p_the_criterion_b_controls_hold_on_asyncpg(factory, name, kwargs) -> None:
    """The same stands and assertions as the SQLite module, through asyncpg: native uuids, `Decimal`
    untouched, JSON intents, and `pg_get_constraintdef`-checked arithmetic on every coordinated rewrite."""

    await getattr(unit, name)(None, **kwargs)


@pytest.mark.asyncio
async def test_step5b_p_a_b_finding_is_stored_in_the_same_row_on_asyncpg(factory, monkeypatch) -> None:
    await unit.test_step5b_a_b_finding_is_stored_in_the_same_row_and_fingerprint_and_never_in_a_checkpoint(
        None, monkeypatch
    )


# =================================================================================================
# A refusal of the version-2 envelope, followed through its caller
# =================================================================================================


@pytest.mark.asyncio
async def test_step5b_p_an_unwidened_version_check_refuses_the_payment_and_the_service_aborts_it(
    factory, committed_database
) -> None:
    """RULE: a refusal is followed through its caller. The one refusal step 5b can add is the database's -
    a version-2 envelope against a CHECK still at `IN (1)` (code deployed without migration 027). Here the
    CHECK is narrowed back (`NOT VALID`, so rows already stored do not block it) and a real payment goes
    through `PaymentService.create_payment_internal` at the application's isolation.

    TRACED AND ASSERTED: `debt_operation` fails on the envelope INSERT with 23514; `_run_uow_with_retry`
    does not retry it (not a serialization class); the service's commit handler treats it as an internal
    error, not a 4xx rejection - rolls back, finds the transaction still PREPARED, ABORTS it and raises a
    5xx `GeoException` caused by the 23514. No debt moved, the prepare locks are released, no envelope.
    (The simulator's real payments phase calls the same service with `commit=False` and records a
    non-4xx failure as `INTERNAL_ERROR`, not `REJECTED` - read in `real_payments_executor.py`, not run here.)
    """
    from app.core.payments.service import PaymentService
    from app.utils.exceptions import GeoException
    from tests.integration.test_p015_p1_money_replay_postgres import (
        _OPENING,
        _debts,
        _forget_the_route_cache,
        _prepare_locks,
        _seed,
        _transactions,
    )

    engine, sessions = _serializable_sessions(committed_database.url)
    world = await _seed(sessions)
    narrowed = False
    try:
        async with factory() as session:
            await session.execute(
                text("ALTER TABLE debt_operations DROP CONSTRAINT chk_debt_operations_intent_version")
            )
            await session.execute(
                text(
                    "ALTER TABLE debt_operations ADD CONSTRAINT chk_debt_operations_intent_version "
                    "CHECK (intent_encoding_version IN (1)) NOT VALID"
                )
            )
            await session.commit()
        narrowed = True

        with pytest.raises(GeoException) as refused:
            async with sessions() as session:
                await PaymentService(session).create_payment_internal(
                    world.sender.id,
                    to_pid=world.receiver.pid,
                    equivalent=world.equivalent.code,
                    amount="10.00",
                    idempotency_key=str(uuid.uuid4()),
                )

        assert int(getattr(refused.value, "status_code", 500) or 500) >= 500, (
            f"the refusal was surfaced as a client rejection: {refused.value!r}"
        )
        assert "23514" in _sqlstates(refused.value), (
            f"premise: the payment did not fail on the version CHECK: {refused.value!r}"
        )
        states = await _transactions(sessions, world)
        assert list(states.values()) == ["ABORTED"], states
        assert await _debts(sessions, world) == {(world.sender.pid, world.receiver.pid): _OPENING}
        assert await _prepare_locks(sessions, world) == 0
        async with factory() as session:
            envelopes = (
                await session.execute(
                    text("SELECT count(*) FROM debt_operations WHERE tx_id = ANY(:ids)"), {"ids": list(states)}
                )
            ).scalar_one()
        assert envelopes == 0, envelopes
    finally:
        if narrowed:
            async with factory() as session:
                await session.execute(
                    text("ALTER TABLE debt_operations DROP CONSTRAINT chk_debt_operations_intent_version")
                )
                await session.execute(
                    text(
                        "ALTER TABLE debt_operations ADD CONSTRAINT chk_debt_operations_intent_version "
                        "CHECK (intent_encoding_version IN (1, 2))"
                    )
                )
                await session.commit()
        _forget_the_route_cache(world)
        await engine.dispose()
