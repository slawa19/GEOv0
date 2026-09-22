"""The seed's refusals, and the proof that each of them still ACCEPTS something.

Programme 017, `T1711`. Every rule in `scripts/seed_recipe.py` that discards or refuses is paired
here with a counter-check (`AGENTS.md` §9, anti-vacuum): a guard that refuses everything is
indistinguishable from a guard that works, and a guard that refuses nothing is a false green. So
each test below states BOTH halves - what is refused, and what is still let through.

Nothing here touches a database. The database-backed half - that the seed's acceptance reddens on a
doctored database - is `tests/integration/test_p017_t1711_seed_recipe_postgres.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMUNITIES = _REPO_ROOT / "seeds" / "communities"
if str(_COMMUNITIES) not in sys.path:
    sys.path.insert(0, str(_COMMUNITIES))

import community_schema  # noqa: E402

from app.utils.exceptions import RetryablePaymentConflictException  # noqa: E402
from scripts.seed_recipe import (  # noqa: E402
    ACCEPTANCE_CHECKS,
    REACHABLE_TRUSTLINE_STATUSES,
    SeedRefusal,
    _check_activity_per_equivalent,
    _is_transient,
    _match_cycle,
    _Run,
    assert_environment_is_safe,
    assert_every_check_reported,
    assert_target_is_disposable,
    unreachable_declared_states,
)


# =================================================================================================
# The declared states no operation reaches
# =================================================================================================


def test_riverside_is_seedable_and_greenfield_is_not_and_the_difference_is_named():
    """The counter-check pair for the refusal that stops greenfield being seeded at all.

    Riverside declares nothing unreachable, so the rule lets a real description through; greenfield
    declares nine frozen trust lines, and every one of them is NAMED. A rule that refused both would
    look identical from the greenfield side alone.
    """

    riverside = community_schema.load_community("riverside-town-50", root=_COMMUNITIES)
    greenfield = community_schema.load_community("greenfield-village-100", root=_COMMUNITIES)

    assert unreachable_declared_states(riverside) == []

    offenders = unreachable_declared_states(greenfield)
    assert len(offenders) == 9, offenders
    declared_frozen = [
        (line["equivalent"], line["from"], line["to"])
        for line in greenfield["trustlines"]
        if line["status"] not in REACHABLE_TRUSTLINE_STATUSES
    ]
    assert len(declared_frozen) == 9
    for equivalent, creditor, debtor in declared_frozen:
        assert any(
            creditor in line and debtor in line and equivalent in line for line in offenders
        ), f"{creditor} -> {debtor} in {equivalent} is not named in the refusal"


async def test_a_community_with_no_recipe_is_a_refusal_that_lists_the_ones_that_have_one():
    """`scripts/seed_db.py --community` also offers the two `-v2` fixture pack ids, which have no
    description and no recipe at all. The counter-check is the other half of the same call: the two
    real communities ARE listed, so this is a wrong-argument message and not a blanket failure."""

    from scripts.seed_recipe import seed_community

    with pytest.raises(SeedRefusal) as refusal:
        await seed_community(
            lambda: None,  # never reached: the description is loaded first
            community_id="riverside-town-50-v2",
            communities_root=_COMMUNITIES,
            env="test",
        )
    message = str(refusal.value)
    assert "riverside-town-50-v2" in message
    assert "riverside-town-50'" in message
    assert "greenfield-village-100'" in message


def test_a_frozen_participant_is_reachable_and_therefore_not_refused():
    """The freeze operation exists (`app/api/v1/admin.py:977`), so a frozen PARTICIPANT is not an
    unreachable state - and the rule must not sweep it up with the frozen LINES."""

    greenfield = community_schema.load_community("greenfield-village-100", root=_COMMUNITIES)
    frozen_people = [p["ref"] for p in greenfield["participants"] if p["status"] == "frozen"]
    assert len(frozen_people) == 5

    offenders = unreachable_declared_states(greenfield)
    for ref in frozen_people:
        assert not any(
            line.startswith(f"participant {ref} ") for line in offenders
        ), f"{ref} is a frozen participant, which a freeze command reaches"


# =================================================================================================
# Where the seed is allowed to write
# =================================================================================================


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_launcher",
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p017t1711",
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p017t1711__clone",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_a_disposable_database_is_accepted(url):
    assert_target_is_disposable(make_url(url))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/postgres",
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0",
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_prod_main",
        "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_",
        "mysql+aiomysql://geo:geo@127.0.0.1:3306/geov0_dev_x",
    ],
)
def test_a_database_that_is_not_provably_disposable_is_refused(url):
    with pytest.raises(SeedRefusal):
        assert_target_is_disposable(make_url(url))


def test_a_sqlite_file_is_accepted_under_local_run_and_refused_outside_it():
    """`tmp_path` is NOT used as the outside case on purpose: the canonical runner points pytest's
    basetemp at `.local-run/test-runs/<slug>/pytest`, so a temporary directory is INSIDE the very
    tree this guard allows, and the refusal half of this test passed vacuously until it was run.
    The repository root is the case the rule exists for (`AGENTS.md` §12: no `*.db` there)."""

    inside = _REPO_ROOT / ".local-run" / "seed-guard-probe" / "probe.db"
    assert_target_is_disposable(make_url(f"sqlite+aiosqlite:///{inside.as_posix()}"))

    outside = _REPO_ROOT / "probe.db"
    with pytest.raises(SeedRefusal, match="local-run"):
        assert_target_is_disposable(make_url(f"sqlite+aiosqlite:///{outside.as_posix()}"))


@pytest.mark.parametrize("env", ["dev", "test"])
def test_a_safe_environment_is_accepted(env):
    assert_environment_is_safe(env)


@pytest.mark.parametrize("env", ["prod", "staging", "", "production", "DEV"])
def test_an_unsafe_environment_is_refused(env):
    with pytest.raises(SeedRefusal, match="ENV="):
        assert_environment_is_safe(env)


# =================================================================================================
# Transient versus logical: the retry predicate must not swallow a logical failure
# =================================================================================================


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _dbapi_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT 1", {}, _DriverError(sqlstate))


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_a_serialization_failure_is_transient(sqlstate):
    assert _is_transient(_dbapi_error(sqlstate)) is True


def test_a_typed_retryable_conflict_is_transient():
    assert _is_transient(RetryablePaymentConflictException()) is True


@pytest.mark.parametrize(
    "exc",
    [
        _dbapi_error("23505"),  # unique violation: retrying cannot help
        _dbapi_error("23514"),  # check violation
        ValueError("the payee has no capacity"),
        RuntimeError("no route"),
    ],
)
def test_a_logical_failure_is_not_transient(exc):
    """The half that matters. Programme 004's defect class is a predicate that answers "transient"
    to everything: the seed would then retry a refusal three times and report it as a conflict."""

    assert _is_transient(exc) is False


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _run_with_fake_sessions():
    community = {
        "community_id": "probe",
        "participants": [],
        "equivalents": [],
        "trustlines": [],
    }
    return _Run(_FakeSession, community, {"commands": []})


async def test_a_transient_failure_is_retried_and_counted():
    """The retry is the reason the command id is the payment's `tx_id`: a second attempt re-sends
    the same identifier, so either the first attempt left nothing or the replay returns what it
    stored. Here only the loop is measured - that it retries, that it stops, and that it counts."""

    run = _run_with_fake_sessions()
    attempts = []

    async def body(session):
        attempts.append(1)
        if len(attempts) < 3:
            raise _dbapi_error("40001")
        return "done"

    assert await run._attempt("probe", body) == "done"
    assert len(attempts) == 3
    assert run.report.retries == 2


async def test_a_logical_failure_is_not_retried_at_all():
    """The half that programme 004 paid for. A refusal retried three times is a refusal reported as
    a conflict, and the operator reads the wrong problem."""

    run = _run_with_fake_sessions()
    attempts = []

    async def body(session):
        attempts.append(1)
        raise _dbapi_error("23505")

    with pytest.raises(SeedRefusal, match="probe"):
        await run._attempt("probe", body)
    assert attempts == [1]
    assert run.report.retries == 0


async def test_a_transient_failure_that_never_clears_ends_as_a_refusal():
    run = _run_with_fake_sessions()
    attempts = []

    async def body(session):
        attempts.append(1)
        raise _dbapi_error("40P01")

    with pytest.raises(SeedRefusal, match="still a transient database conflict"):
        await run._attempt("probe", body)
    assert len(attempts) == 4, "four attempts is the declared bound, not an accident"


# =================================================================================================
# Cycle matching
# =================================================================================================


def _cycle(*pairs):
    return [{"debtor": d, "creditor": c, "amount": "1.00", "debt_id": f"{d}{c}"} for d, c in pairs]


def test_a_rotation_of_the_same_cycle_matches():
    expected = frozenset({("A", "B"), ("B", "C"), ("C", "A")})
    detected = _cycle(("B", "C"), ("C", "A"), ("A", "B"))
    assert _match_cycle([detected], expected) == detected


def test_the_reversed_cycle_does_not_match():
    """Direction is the trap this repository has fallen into before: a trustline is written
    creditor -> debtor and a cycle debtor -> creditor, so a matcher blind to direction would accept
    the mirror image of the cycle the recipe named."""

    expected = frozenset({("A", "B"), ("B", "C"), ("C", "A")})
    reversed_cycle = _cycle(("B", "A"), ("C", "B"), ("A", "C"))
    assert _match_cycle([reversed_cycle], expected) is None


def test_a_different_cycle_of_the_same_length_does_not_match():
    expected = frozenset({("A", "B"), ("B", "C"), ("C", "A")})
    other = _cycle(("A", "B"), ("B", "D"), ("D", "A"))
    assert _match_cycle([other], expected) is None


def test_nothing_detected_matches_nothing():
    assert _match_cycle([], frozenset({("A", "B"), ("B", "C"), ("C", "A")})) is None


# =================================================================================================
# The activity check counts what the DESCRIPTION declares, not what the database happens to hold
# =================================================================================================


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _Result(self._rows)


def _activity_run(equivalent_ids):
    community = community_schema.load_community("riverside-town-50", root=_COMMUNITIES)
    run = _Run(lambda: _StubSession([]), community, {"commands": []})
    run.equivalent_ids = equivalent_ids
    return run, community


async def test_activity_passes_when_every_declared_equivalent_carries_operations():
    import uuid

    ids = {code: uuid.uuid4() for code in ("UAH", "EUR", "HOUR")}
    run, _ = _activity_run(ids)
    rows = [(equivalent_id, 3) for equivalent_id in ids.values()]

    verdict = await _check_activity_per_equivalent(lambda: _StubSession(rows), run)
    assert verdict["passed"] is True
    assert verdict["operations_per_equivalent"] == {"UAH": 3, "EUR": 3, "HOUR": 3}


async def test_activity_fails_when_a_declared_equivalent_is_not_even_in_the_database():
    """The hole this rule had. It used to walk the equivalents the DATABASE holds and filter them by
    the declared set, so an equivalent that was never created produced no idle entry and the check
    passed by having nothing to look at."""

    run, community = _activity_run({})
    verdict = await _check_activity_per_equivalent(lambda: _StubSession([]), run)

    declared_active = sorted(e["code"] for e in community["equivalents"] if e["is_active"])
    assert declared_active, "the description must declare active equivalents for this to mean anything"
    assert verdict["passed"] is False
    assert sorted(verdict["absent_equivalents"]) == declared_active


# =================================================================================================
# A check that did not run is not a check that passed
# =================================================================================================


def _all_passing() -> dict[str, dict]:
    return {name: {"passed": True, "detail": "ok"} for name in ACCEPTANCE_CHECKS}


def test_the_full_set_of_checks_is_accepted():
    assert_every_check_reported(_all_passing())


def test_a_missing_check_is_refused():
    checks = _all_passing()
    dropped = checks.pop("clearing_executed")
    assert dropped["passed"] is True
    with pytest.raises(SeedRefusal, match="did not produce a verdict"):
        assert_every_check_reported(checks)


def test_an_undeclared_check_is_refused():
    checks = _all_passing()
    checks["clearing_probably_happened"] = {"passed": True, "detail": "ok"}
    with pytest.raises(SeedRefusal, match="unknown check"):
        assert_every_check_reported(checks)
