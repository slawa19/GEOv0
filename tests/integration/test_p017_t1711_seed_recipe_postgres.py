"""The recipe seed on PostgreSQL: it runs, its acceptance passes, and the acceptance can FAIL.

Programme 017, `T1711`. Three groups, and the last is the one that makes the first mean anything:

* the control - the Riverside recipe runs end to end on a freshly migrated database and every
  acceptance check passes; a second run of the same command is REFUSED rather than replayed;
* the paths where it must NOT finish - a description declaring a state no operation reaches
  (`greenfield-village-100`) is refused before the first write; a command that its validator accepts
  but a database cannot perform stops the run and names itself, leaving what came before it in
  place; an absent database and an empty one are named refusals rather than tracebacks or green
  verdicts;
* the counter-checks - for each of the seven acceptance checks, the database is doctored in the one
  way that check exists to notice, the check is re-run and must FAIL, and the doctoring is undone
  and the check must pass again (`AGENTS.md` §9, anti-vacuum). A check that stayed green through
  its own doctoring is a check nobody should read as evidence.

PostgreSQL and not SQLite: this is the engine programme 017 is moving to, `execute_clearing_with_amount`
takes its one-connection interlock only here, and the seed's reconciliation is the thing being
trusted. The doctoring statements go through `exec_driver_sql`, which fires no `before_execute` and
therefore passes the journal's armed write guard - the same door `tests/conftest.py` uses for its
test-database reset, and for the same reason: this is the disposal of a scratch database, not a
money write.

EVERY TEST GETS ITS OWN CLONE of a migrated template, because the seed refuses a database that is
not empty and the tier database is not.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from scripts.seed_recipe import SeedRefusal, reverify, seed_community
from tests.migrated_schema import cloned_database, provision_migrated_template

COMMUNITY = "riverside-town-50"

#: 33 payments + 2 clearings (one executed, one asserted) + 2 freezes = the Riverside recipe.
EXPECTED_COMMANDS = {"payment": 33, "clearing": 2, "freeze": 2}
EXPECTED_PARTICIPANTS = 50
EXPECTED_TRUSTLINES = 316


def _postgres_url() -> str:
    from tests.conftest import TEST_DATABASE_URL

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def template_name() -> str:
    _, name = await provision_migrated_template(_postgres_url(), suffix="p017t1711tpl")
    return name


def _factory_for(url: str):
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL,
    )
    return engine, async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False
    )


async def _driver_sql(factory, statements: list[str]) -> None:
    """Statements that go ROUND the journal's write guard, as a database disposal may.

    No bound parameters, deliberately: `exec_driver_sql` hands the statement to the driver's own
    paramstyle, and every value written below is a `uuid` or a `numeric` this module just read back
    out of this same database, rendered by `_literal`. One less thing between the doctoring and what
    reaches PostgreSQL.
    """

    async with factory() as session:
        connection = await session.connection()
        for sql in statements:
            await connection.exec_driver_sql(sql)
        await session.commit()


def _literal(value) -> str:
    """A UUID, a decimal or a boolean as SQL. Refuses anything else rather than quoting it."""

    import uuid as _uuid

    if isinstance(value, _uuid.UUID):
        return f"'{value}'::uuid"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, str) and len(value) == 64 and value.isalnum():
        return f"'{value}'"  # a sha256 digest
    raise AssertionError(f"refusing to write {value!r} into a doctoring statement")


async def _scalar(factory, sql: str, params: dict | None = None):
    async with factory() as session:
        return (await session.execute(text(sql), params or {})).scalar_one()


async def _rows(factory, sql: str, params: dict | None = None):
    async with factory() as session:
        return (await session.execute(text(sql), params or {})).all()


# =================================================================================================
# The control
# =================================================================================================


async def test_the_recipe_runs_and_every_acceptance_check_passes(template_name):
    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1711ok"
    ) as clone_url:
        engine, factory = _factory_for(clone_url)
        try:
            report = await seed_community(factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)

            assert report.participants == EXPECTED_PARTICIPANTS
            assert report.trustlines == EXPECTED_TRUSTLINES
            assert report.commands == EXPECTED_COMMANDS
            assert report.equivalents == 3

            # The baseline was taken on empty debts, which is what lets the reconciliation below
            # speak about the whole seed rather than about the part written after it.
            assert set(report.baselines) == {"UAH", "EUR", "HOUR"}
            for code, taken in report.baselines.items():
                assert taken == {
                    "offsets_recorded": 0,
                    "edges_seen": 0,
                    "entries_read": 0,
                }, f"{code}: {taken}"

            failed = {
                name: check
                for name, check in report.acceptance.items()
                if not check["passed"]
            }
            assert not failed, failed

            examined = report.acceptance["every_operation_examined"]
            assert examined["operations_recorded"] == 34  # 33 payments + 1 executed clearing
            assert examined["operations_examined"] == 34
            assert examined["limited"] == {"UAH": [], "EUR": [], "HOUR": []}

            # The bottleneck the recipe builds, measured rather than asserted by name.
            assert Decimal(
                report.acceptance["bottleneck_edge_below_threshold"]["tightest_residual_share"]
            ) < Decimal("0.10")

            # A second run is REFUSED. The keys of the first run are gone, so its participants
            # cannot be addressed again: replaying onto them is not possible, and pretending
            # otherwise would double the money.
            with pytest.raises(SeedRefusal, match="not empty"):
                await seed_community(factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)

            # And the refusal changed nothing.
            assert await _scalar(factory, "SELECT count(*) FROM participants") == EXPECTED_PARTICIPANTS
            assert await _scalar(factory, "SELECT count(*) FROM debt_operations") == 34
        finally:
            await engine.dispose()


async def test_a_community_declaring_an_unreachable_state_is_refused_before_anything_is_written(
    template_name,
):
    """`greenfield-village-100` declares nine frozen trust lines and no product path writes that
    status. The refusal has to come BEFORE the first participant, or the database is left holding
    half a community whose keys no longer exist."""

    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1711gf"
    ) as clone_url:
        engine, factory = _factory_for(clone_url)
        try:
            with pytest.raises(SeedRefusal) as refusal:
                await seed_community(
                    factory, community_id="greenfield-village-100", env="test", allow_scratch_suffix=True
                )
            assert "9 state(s)" in str(refusal.value)
            assert "frozen" in str(refusal.value)

            for table in ("equivalents", "participants", "trust_lines", "debts", "debt_operations"):
                assert await _scalar(factory, f"SELECT count(*) FROM {table}") == 0, table
        finally:
            await engine.dispose()


async def test_a_command_that_cannot_be_performed_stops_the_seed_and_names_itself(
    template_name, tmp_path
):
    """A recipe can be valid on paper and impossible in a database, and then the seed must STOP.

    `recipe_schema` says so itself: it models no capacity and no router, so an `open` payment it
    accepts may find no route at runtime. The recipe below is the real Riverside description with a
    two-command recipe - one payment that works, one that asks for five hundred times the only EUR
    line's limit - plus the freezes the description's declared statuses require.

    Two things are asserted about the wreckage, because both are contracts:

    * the refusal NAMES the command, so an operator knows which line of the recipe to read, and
    * what the earlier command did is still there. The seed is not one transaction and does not
      pretend to be; a half-written database is exactly the "partial initialization" the next run
      refuses, because the keys that could continue it are gone.
    """

    import json
    import shutil

    communities = Path(__file__).resolve().parents[2] / "seeds" / "communities"
    root = tmp_path / "communities"
    (root / COMMUNITY).mkdir(parents=True)
    shutil.copy(communities / COMMUNITY / "community.json", root / COMMUNITY / "community.json")

    recipe = {
        "schema_version": "recipe/1",
        "community_id": COMMUNITY,
        "title": "A recipe that cannot be performed",
        "summary": "One payment that works and one that asks for capacity nobody has.",
        "commands": [
            {
                "id": "impossible.001.market-takes-ivan-catch",
                "op": "payment",
                "equivalent": "UAH",
                "payer": "fish_market_and_cold_storage",
                "payee": "ivan_kozak",
                "amount": "1180.00",
                "routing": "direct",
                "why": "The first command succeeds, so there is something to find afterwards.",
                "expect": "Debt market -> ivan is 1180.00 UAH.",
            },
            {
                "id": "impossible.002.guide-pays-more-than-exists",
                "op": "payment",
                "equivalent": "EUR",
                "payer": "anna_turystka",
                "payee": "riverside_fishing_co_operative",
                "amount": "100000.00",
                "routing": "open",
                "why": "The only EUR line into the co-operative has a limit of 200.00.",
                "expect": "This command cannot be performed and the seed must say so.",
            },
            {
                "id": "impossible.003.freeze-guide",
                "op": "freeze",
                "participant": "anna_turystka",
                "why": "The description declares this participant frozen.",
                "expect": "Never reached: the run stops above.",
            },
            {
                "id": "impossible.004.freeze-pharmacy",
                "op": "freeze",
                "participant": "riverside_pharmacy",
                "why": "The description declares this participant frozen.",
                "expect": "Never reached: the run stops above.",
            },
        ],
    }
    (root / COMMUNITY / "recipe.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1711mid"
    ) as clone_url:
        engine, factory = _factory_for(clone_url)
        try:
            with pytest.raises(SeedRefusal) as refusal:
                await seed_community(
                    factory,
                    community_id=COMMUNITY,
                    communities_root=root,
                    env="test", allow_scratch_suffix=True,
                )
            assert "impossible.002.guide-pays-more-than-exists" in str(refusal.value)

            # The command before it happened, and stayed.
            assert await _scalar(factory, "SELECT count(*) FROM debts") == 1
            assert await _scalar(
                factory, "SELECT count(*) FROM debt_operations WHERE kind = 'PAYMENT'"
            ) == 1
            # Nothing was frozen: the run never reached those commands.
            assert await _scalar(
                factory, "SELECT count(*) FROM participants WHERE status = 'suspended'"
            ) == 0

            # And a fresh attempt on the half-written database is refused rather than continued.
            with pytest.raises(SeedRefusal, match="not empty"):
                await seed_community(factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)
        finally:
            await engine.dispose()


async def test_the_acceptance_refuses_an_empty_database_instead_of_passing_it(template_name):
    """The acceptance run against a database that holds NOTHING must not come back green.

    A verdict that is true of an empty database is a verdict about nothing, and `reverify` is the
    entry point where that could happen: it takes the `ref -> PID` table of a run and re-checks
    whatever database it is pointed at. Pointed at an empty one, it has to say so.
    """

    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1711seed"
    ) as seeded_url:
        seeded_engine, seeded_factory = _factory_for(seeded_url)
        try:
            report = await seed_community(seeded_factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)
        finally:
            await seeded_engine.dispose()

        async with cloned_database(
            _postgres_url(), template_name=template_name, suffix="p017t1711empty"
        ) as empty_url:
            engine, factory = _factory_for(empty_url)
            try:
                with pytest.raises(SeedRefusal) as refusal:
                    await reverify(
                        factory, community_id=COMMUNITY, refs_to_pid=report.refs_to_pid
                    )
                assert "not in this database" in str(refusal.value)
            finally:
                await engine.dispose()


async def test_an_absent_database_is_a_named_refusal(template_name):
    """A URL that satisfies the name contract but points at nothing must refuse by name rather than
    surface a driver traceback - and the password must not be in the message (`AGENTS.md` §12)."""

    from sqlalchemy.engine import make_url

    absent = make_url(_postgres_url()).set(database="geov0_test_p017t1711_absent")
    engine, factory = _factory_for(absent.render_as_string(hide_password=False))
    try:
        with pytest.raises(SeedRefusal) as refusal:
            await seed_community(factory, community_id=COMMUNITY, env="test")
        message = str(refusal.value)
        assert "geov0_test_p017t1711_absent" in message
        assert "cannot read the target database" in message
        assert ":geo@" not in message, message
    finally:
        await engine.dispose()


# =================================================================================================
# The counter-checks: each acceptance check, doctored until it fails, then restored
# =================================================================================================


async def _surviving_cycle_line(factory, refs_to_pid: dict[str, str]):
    """The trust line that carries one edge of the cycle the recipe asserts survives.

    Read out of the RECIPE, not guessed: doctoring "some small debt" would leave the surviving cycle
    intact and the counter-check would prove nothing.

    THE LINE AND NOT THE DEBT, because the debt cannot be doctored reversibly: `chk_debt_amount_positive`
    refuses a zero amount (measured 2026-09-22 - the first version of this counter-check died on it),
    and deleting the row would mean rebuilding it, timestamps and optimistic-lock version included.
    Closing the line is one `UPDATE` with one `UPDATE` back, and it removes the cycle for the reason
    clearing itself cares about: `find_cycles` only joins trust lines whose status is `active` or
    `frozen` (`app/core/clearing/service.py:49`).
    """

    import sys
    from pathlib import Path

    communities = Path(__file__).resolve().parents[2] / "seeds" / "communities"
    if str(communities) not in sys.path:
        sys.path.insert(0, str(communities))
    import recipe_schema  # noqa: PLC0415

    recipe = recipe_schema.load_recipe(COMMUNITY, root=communities)
    asserted = [
        command
        for command in recipe["commands"]
        if command["op"] == "clearing" and command["mode"] == "assert_clearable"
    ]
    assert asserted, "the recipe must assert a surviving cycle, or this counter-check is vacuous"
    cycle = asserted[0]["cycle"]

    rows = await _rows(
        factory,
        "SELECT t.id FROM trust_lines t "
        "JOIN participants pc ON pc.id = t.from_participant_id "
        "JOIN participants pd ON pd.id = t.to_participant_id "
        "JOIN equivalents e ON e.id = t.equivalent_id "
        "JOIN debts d ON d.debtor_id = pd.id AND d.creditor_id = pc.id "
        "           AND d.equivalent_id = e.id AND d.amount > 0 "
        "WHERE pd.pid = :debtor AND pc.pid = :creditor AND e.code = :code "
        "  AND t.status = 'active'",
        {
            "debtor": refs_to_pid[cycle[0]],
            "creditor": refs_to_pid[cycle[1]],
            "code": asserted[0]["equivalent"],
        },
    )
    assert len(rows) == 1, (
        f"the asserted cycle's first edge {cycle[0]} owes {cycle[1]} has no live debt on a live "
        f"trust line: {rows}"
    )
    return rows[0][0]


async def _doctorings(factory, refs_to_pid: dict[str, str]) -> list[tuple[str, list[str], list[str]]]:
    """`(check name, break it, put it back)`, built from what is actually in this database."""

    debt_id, _ = (
        await _rows(factory, "SELECT id, amount FROM debts ORDER BY amount DESC LIMIT 1")
    )[0]

    # An edge that exists in `debts`: an offset planted on it makes the baseline claim it adopted a
    # debt the journal in fact explains.
    eq_id, debtor_id, creditor_id = (
        await _rows(
            factory,
            "SELECT equivalent_id, debtor_id, creditor_id FROM debts ORDER BY amount DESC LIMIT 1",
        )
    )[0]

    clearing_ids = [
        row[0] for row in await _rows(factory, "SELECT id FROM debt_operations WHERE kind = 'CLEARING'")
    ]
    assert clearing_ids, "the recipe executes one clearing; without it this counter-check is vacuous"

    eur_id = await _scalar(factory, "SELECT id FROM equivalents WHERE code = 'EUR'")
    eur_links = await _rows(
        factory,
        "SELECT operation_id, in_intent, in_scope, effect_count, effect_digest "
        "FROM debt_operation_equivalents WHERE equivalent_id = :eq",
        {"eq": eur_id},
    )
    assert eur_links, "EUR must carry operations, or the activity check has nothing to lose"

    surviving_line = await _surviving_cycle_line(factory, refs_to_pid)

    clearing_list = ", ".join(_literal(value) for value in clearing_ids)

    return [
        (
            "reconciliation_passed",
            [f"UPDATE debts SET amount = amount + 1 WHERE id = {_literal(debt_id)}"],
            [f"UPDATE debts SET amount = amount - 1 WHERE id = {_literal(debt_id)}"],
        ),
        (
            "baseline_offsets_are_zero",
            [
                "INSERT INTO debt_reconciliation_baseline_offsets "
                "(equivalent_id, debtor_id, creditor_id, offset_amount) VALUES ("
                f"{_literal(eq_id)}, {_literal(debtor_id)}, {_literal(creditor_id)}, 1)"
            ],
            [
                "DELETE FROM debt_reconciliation_baseline_offsets WHERE equivalent_id = "
                f"{_literal(eq_id)}"
            ],
        ),
        (
            "every_operation_examined",
            ["UPDATE debt_operations SET intent_encoding_version = 1 WHERE kind = 'PAYMENT'"],
            ["UPDATE debt_operations SET intent_encoding_version = 2 WHERE kind = 'PAYMENT'"],
        ),
        (
            "bottleneck_edge_below_threshold",
            ['UPDATE trust_lines SET "limit" = "limit" * 1000'],
            ['UPDATE trust_lines SET "limit" = "limit" / 1000'],
        ),
        (
            "clearing_executed",
            ["UPDATE debt_operations SET kind = 'PAYMENT' WHERE kind = 'CLEARING'"],
            [f"UPDATE debt_operations SET kind = 'CLEARING' WHERE id IN ({clearing_list})"],
        ),
        (
            "surviving_cycle_still_clearable",
            [f"UPDATE trust_lines SET status = 'closed' WHERE id = {_literal(surviving_line)}"],
            [f"UPDATE trust_lines SET status = 'active' WHERE id = {_literal(surviving_line)}"],
        ),
        (
            "activity_in_every_equivalent",
            [f"DELETE FROM debt_operation_equivalents WHERE equivalent_id = {_literal(eur_id)}"],
            [
                "INSERT INTO debt_operation_equivalents "
                "(operation_id, equivalent_id, in_intent, in_scope, effect_count, effect_digest) "
                f"VALUES ({_literal(operation_id)}, {_literal(eur_id)}, {_literal(in_intent)}, "
                f"{_literal(in_scope)}, {_literal(effect_count)}, {_literal(effect_digest)})"
                for operation_id, in_intent, in_scope, effect_count, effect_digest in eur_links
            ],
        ),
    ]


async def test_every_acceptance_check_reddens_on_the_state_it_exists_to_notice(template_name):
    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1711cc"
    ) as clone_url:
        engine, factory = _factory_for(clone_url)
        try:
            report = await seed_community(factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)
            refs_to_pid = report.refs_to_pid

            async def verdicts():
                return await reverify(
                    factory, community_id=COMMUNITY, refs_to_pid=refs_to_pid
                )

            doctorings = await _doctorings(factory, refs_to_pid)
            assert {name for name, _, _ in doctorings} == set(report.acceptance), (
                "every acceptance check needs a doctoring, or the ones without are unmeasured"
            )

            for name, break_it, put_it_back in doctorings:
                before = await verdicts()
                assert before[name]["passed"], f"control failed before doctoring {name}: {before[name]}"

                await _driver_sql(factory, break_it)
                after = await verdicts()
                assert not after[name]["passed"], (
                    f"{name} stayed green through the state it exists to notice: {after[name]}"
                )

                await _driver_sql(factory, put_it_back)
                restored = await verdicts()
                assert restored[name]["passed"], (
                    f"{name} did not recover after the doctoring was undone: {restored[name]}"
                )
        finally:
            await engine.dispose()


# =================================================================================================
# Readiness is not acceptance: a database the product has USED must still start
# =================================================================================================


async def test_the_launcher_readiness_survives_a_clearing_the_product_ran(
    template_name, monkeypatch, tmp_path
):
    """Clearing is the product's main function; running it must not make the next start refuse.

    Codex external review of `37fec08..5e687dd`, F1 (2026-09-23): `dev_database.py ready` re-ran all
    seven acceptance checks, three of which describe the DEMONSTRATION state right after the seed -
    a surviving cycle, a bottleneck, an executed clearing. One `POST /clearing/auto?equivalent=UAH`
    extinguishes the surviving cycle, and the launcher then refused a correct database and advised
    resetting it. Readiness is narrowed to what a start needs; the acceptance stays whole.

    Driven through `cmd_ready` itself - the function both launchers call - and not through a helper,
    so that what goes red here is what refused the owner's stack.
    """

    import json

    import app.db.session as db_session
    from sqlalchemy.engine import make_url

    from app.core.clearing.service import ClearingService
    from scripts import dev_database

    async with cloned_database(
        _postgres_url(), template_name=template_name, suffix="p017t1710ready"
    ) as clone_url:
        engine, factory = _factory_for(clone_url)
        try:
            report = await seed_community(factory, community_id=COMMUNITY, env="test", allow_scratch_suffix=True)
            table = tmp_path / "participants.json"
            table.write_text(
                json.dumps(
                    {
                        "community_id": COMMUNITY,
                        "participants": {
                            ref: {"pid": pid} for ref, pid in report.refs_to_pid.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
            monkeypatch.setattr(dev_database, "adopted_key_table_path", lambda _database: table)
            monkeypatch.setattr(db_session, "AsyncSessionLocal", factory)
            url = make_url(clone_url)

            # Control: the freshly seeded database is ready.
            assert await dev_database.cmd_ready(url, community=COMMUNITY) == 0

            # The product clears UAH - the same service call `POST /clearing/auto` makes.
            async with factory() as session:
                cleared = await ClearingService(session).auto_clear("UAH", max_depth=6)
            assert cleared >= 1, "auto-clearing found nothing to clear; the scenario is vacuous"

            # The demonstration state is gone, and the ACCEPTANCE still says so - it is not weakened.
            after = await reverify(factory, community_id=COMMUNITY, refs_to_pid=report.refs_to_pid)
            assert not after["surviving_cycle_still_clearable"]["passed"], after[
                "surviving_cycle_still_clearable"
            ]
            assert after["reconciliation_passed"]["passed"], after["reconciliation_passed"]

            # The database is still correct, so the stack may start on it.
            assert await dev_database.cmd_ready(url, community=COMMUNITY) == 0

            # Counter-check (AGENTS.md section 9): narrowed readiness still refuses money that the
            # journal does not explain.
            debt_id = (await _rows(factory, "SELECT id FROM debts ORDER BY amount DESC LIMIT 1"))[0][0]
            await _driver_sql(
                factory, [f"UPDATE debts SET amount = amount + 1 WHERE id = {_literal(debt_id)}"]
            )
            with pytest.raises(dev_database.DevDatabaseRefusal, match="reconciliation_passed"):
                await dev_database.cmd_ready(url, community=COMMUNITY)
            await _driver_sql(
                factory, [f"UPDATE debts SET amount = amount - 1 WHERE id = {_literal(debt_id)}"]
            )
            assert await dev_database.cmd_ready(url, community=COMMUNITY) == 0
        finally:
            await engine.dispose()
