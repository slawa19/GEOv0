"""047 `T4702`: `scripts/reconcile_debts.py` - the operator's on-demand debt reconciliation, and its exit codes.

THE PATH IS THE REAL ONE: the script runs as a child process of this interpreter against a disposable clone of the
tier (`committed_database`), reaching it through `DATABASE_URL` as an operator's run would; the discrepancy is
written around the application through THE named corruption helper (`tests/ledger_corruption.py`). The one
in-process test is the verifier error, which a child process cannot be made to raise.

RED BEFORE: on `b103fee9` the script does not exist; every case fails (the interpreter's own exit 2 for a missing
file is told apart from the command's by its message).

WHAT THESE TESTS DO NOT SEE: a scheduled run publishing at the same moment as the command (`record_outcome`), and
the Redis lock, which the command does not take.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import FAILED, PASSED
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import _seed_triangle
from tests.unit.test_p015_step5a_reconciliation import _around_the_application, _baseline, _fixture_debts, _literal

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "reconcile_debts.py"
#: What the command, and only the command, prints when it could not run.
_DID_NOT_RUN = "the reconciliation did not run"


def _command(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ENV": "test", "ENVIRONMENT": "test", "DATABASE_URL": url, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONIOENCODING": "utf-8"}  # an OS error message may be localised (Windows)
    return subprocess.run([sys.executable, str(_SCRIPT), *args], cwd=str(_ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)


async def _clean_equivalent(factory, *, active: bool = True):
    """A baselined equivalent with one debt `a -> b` the journal explains: PASSED. Returns (triangle, debt)."""

    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    (debt,) = await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    if not active:
        async with factory() as session:
            await session.execute(update(Equivalent).where(Equivalent.id == triangle.equivalent.id).values(is_active=False))
            await session.commit()
    return triangle, debt


async def _results(factory, equivalent_id) -> list[str]:
    columns = debt_reconciliation_results.c
    async with factory() as session:
        return list((await session.execute(select(columns.status).where(
            columns.equivalent_id == equivalent_id).order_by(columns.checked_at))).scalars())


async def _hold(factory, equivalent_id):
    async with factory() as session:
        return (await session.get(Equivalent, equivalent_id)).integrity_hold_result_id


async def _all_codes(factory) -> set[str]:
    async with factory() as session:
        return set((await session.execute(select(Equivalent.code))).scalars())


def _verdicts(stdout: str) -> dict[str, str]:
    """`CODE: VERDICT - ...` lines of the command's output, by code."""

    return {line.split(":", 1)[0]: line.split(":", 1)[1].split()[0]
            for line in stdout.splitlines() if not line.startswith(" ") and ":" in line}


@pytest.mark.asyncio
async def test_all_on_a_clean_database_exits_0_and_checks_every_equivalent_active_or_not(committed_database) -> None:
    """ANTI-VACUUM: `--all` reaches every equivalent of the database, the inactive one too, and stores a PASSED for
    each. MUTATION: list only `is_active` equivalents in the command - the inactive one is missing, red; return 0
    before the loop - no rows, red. An unknown code is exit 2 and checks nothing."""

    factory, url = committed_database.sessionmaker, committed_database.url
    (active, _), (inactive, _) = await _clean_equivalent(factory), await _clean_equivalent(factory, active=False)
    codes = await _all_codes(factory)
    assert codes == {active.equivalent.code, inactive.equivalent.code}, f"stand: the clone holds other rows: {codes}"

    done = _command(url, "--all")
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert _verdicts(done.stdout) == {code: PASSED for code in codes}, done.stdout
    for triangle in (active, inactive):
        assert await _results(factory, triangle.equivalent.id) == [PASSED]

    unknown = _command(url, "--equivalent", f"NO{uuid.uuid4().hex[:6].upper()}")
    assert unknown.returncode == 2 and "unknown equivalent" in unknown.stderr, (unknown.returncode, unknown.stderr)
    assert await _results(factory, active.equivalent.id) == [PASSED], "an unknown code must check nothing"


@pytest.mark.asyncio
async def test_a_debt_moved_around_the_application_exits_1_records_failed_and_holds_that_equivalent(
    committed_database,
) -> None:
    """One atom added to a debt with the journal's triggers off: `--all` exits 1, names the equivalent FAILED with its
    finding, the stored row is FAILED and the equivalent is held on it (the step 5c reaction through the command);
    the clean equivalent is PASSED and not held. `--equivalent` of the clean one alone exits 0.
    MUTATION: exit 0 on a FAILED - red; call the verifier without the reaction - no hold, red."""

    factory, url = committed_database.sessionmaker, committed_database.url
    (faulty, debt), (clean, _) = await _clean_equivalent(factory), await _clean_equivalent(factory)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'")

    done = _command(url, "--all")
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert _verdicts(done.stdout) == {faulty.equivalent.code: FAILED, clean.equivalent.code: PASSED}, done.stdout
    failed_line = next(line for line in done.stdout.splitlines() if line.startswith(faulty.equivalent.code))
    assert "hold=set" in failed_line, failed_line
    findings = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("  ")]
    assert [f["unexplained"] for f in findings if f.get("kind") == "edge_residual"] == ["0.00000001"], done.stdout

    assert await _results(factory, faulty.equivalent.id) == [FAILED]
    async with factory() as session:
        latest = (await session.execute(select(debt_reconciliation_results.c.id).where(
            debt_reconciliation_results.c.equivalent_id == faulty.equivalent.id,
            debt_reconciliation_results.c.is_latest.is_(True)))).scalar_one()
    assert await _hold(factory, faulty.equivalent.id) == latest, "the FAILED did not hold its equivalent"
    assert await _hold(factory, clean.equivalent.id) is None

    narrowed = _command(url, "--equivalent", clean.equivalent.code.lower())
    assert narrowed.returncode == 0, (narrowed.returncode, narrowed.stdout, narrowed.stderr)
    assert _verdicts(narrowed.stdout) == {clean.equivalent.code: PASSED}, narrowed.stdout


def test_an_unreachable_database_exits_2_and_never_0() -> None:
    """A run that could not connect checked nothing: exit 2 with the command's own message. MUTATION: return 0 from
    the command's error handler - red. Nothing listens on port 1 of the loopback address."""

    done = _command("postgresql+asyncpg://geo:p047-secret@127.0.0.1:1/geov0_p047_nowhere", "--all")
    assert done.returncode == 2 and _DID_NOT_RUN in done.stderr, (done.returncode, done.stdout, done.stderr)
    assert "p047-secret" not in done.stderr, "the message must not carry the URL's credentials"
    usage = _command("postgresql+asyncpg://geo:geo@127.0.0.1:1/geov0_p047_nowhere")
    assert usage.returncode == 2 and "--all" in usage.stderr, (usage.returncode, usage.stderr)


@pytest.mark.asyncio
async def test_a_verifier_error_is_not_checked_exit_2_and_a_failed_elsewhere_still_exits_1(
    committed_database, monkeypatch, capsys
) -> None:
    """The verifier raising on one equivalent leaves no row for it; the command prints it NOT CHECKED and exits 2,
    never 0 over the other equivalent's PASSED; with a FAILED elsewhere in the same run it exits 1.
    MUTATION: read the verdict from the latest stored row instead of this run - the old PASSED is printed, exit 0,
    red."""

    spec = importlib.util.spec_from_file_location("p047_reconcile_debts", _SCRIPT)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    factory = committed_database.sessionmaker
    (erring, _), (clean, _) = await _clean_equivalent(factory), await _clean_equivalent(factory)
    assert await command._run([], True, factory) == 0, capsys.readouterr()  # control: a PASSED row for each
    capsys.readouterr()

    original = reconciliation.verify_journal_equals_change

    async def verify(session, equivalent_id):
        if equivalent_id == erring.equivalent.id:
            raise RuntimeError("p047: the verifier fails on this equivalent")
        return await original(session, equivalent_id)

    monkeypatch.setattr(reconciliation, "verify_journal_equals_change", verify)
    assert await command._run([], True, factory) == 2
    out = capsys.readouterr().out
    assert f"{erring.equivalent.code}: NOT CHECKED" in out and _verdicts(out)[clean.equivalent.code] == PASSED, out

    (faulty, debt) = await _clean_equivalent(factory)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'")
    assert await command._run([], True, factory) == 1
    out = capsys.readouterr().out
    assert _verdicts(out)[faulty.equivalent.code] == FAILED and f"{erring.equivalent.code}: NOT CHECKED" in out, out


@pytest.mark.asyncio
async def test_a_failed_stands_when_its_details_cannot_be_read_exit_1_not_2(committed_database, monkeypatch) -> None:
    """047 fix-delta (Codex §15 P2): the run commits FAILED and the hold, then reading the stored evidence fails. The
    command keeps the verdict: exit 1 and `verdict FAILED; details could not be read`, never "did not run" and 2.
    Through `main` (in a thread: it runs its own event loop), on the clone. RED BEFORE (`1304374b`): the read error
    escaped `_run`, `main` printed "the reconciliation did not run" and returned 2. MUTATION: drop the guard around
    the stored-result read - exit 2, red."""

    import app.db.session as db_session_module

    spec = importlib.util.spec_from_file_location("p047_reconcile_debts_details", _SCRIPT)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    factory = committed_database.sessionmaker
    (faulty, debt) = await _clean_equivalent(factory)
    await _around_the_application(
        factory, lambda d: f"UPDATE debts SET amount = '10.00000001' WHERE id = '{_literal(d, debt.id)}'")

    async def unreadable(*_args):
        raise ConnectionError("p047: the connection went away after the verdict")

    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(db_session_module, "engine", committed_database.engine)
    for name in ("_latest_detail", "_stored_result"):  # the reader's name on `1304374b` and after the fix-delta
        monkeypatch.setattr(command, name, unreadable, raising=False)

    out, err = io.StringIO(), io.StringIO()

    def run_main() -> int:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return command.main(["--all"])

    assert await asyncio.to_thread(run_main) == 1, (out.getvalue(), err.getvalue())
    assert f"{faulty.equivalent.code}: FAILED hold=set - verdict FAILED; details could not be read: ConnectionError" in (
        out.getvalue()), out.getvalue()
    assert _DID_NOT_RUN not in err.getvalue(), err.getvalue()
    assert await _results(factory, faulty.equivalent.id) == [FAILED] and await _hold(factory, faulty.equivalent.id)


@pytest.mark.asyncio
async def test_an_empty_code_is_refused_exit_2_even_next_to_a_valid_one(committed_database, capsys) -> None:
    """047 (Argon P3): `--equivalent CODE --equivalent ""` (an unset variable, say) checks nothing and exits 2 with the
    reason; nothing is recorded. RED BEFORE (`34c5c8c4`): the empty code was dropped, CODE checked, exit 0."""

    spec = importlib.util.spec_from_file_location("p047_reconcile_debts_empty", _SCRIPT)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    factory = committed_database.sessionmaker
    (clean, _) = await _clean_equivalent(factory)

    assert await command._run([clean.equivalent.code, ""], False, factory) == 2
    assert "empty equivalent code" in capsys.readouterr().err
    assert await command._run(["  "], False, factory) == 2
    assert "empty equivalent code" in capsys.readouterr().err
    assert await _results(factory, clean.equivalent.id) == [], "an empty code must check nothing"
