"""T1701, architecture guard: `alembic_version` is created-or-widened in ONE place.

THIS IS A POLICY GUARD AND IT CHECKS FORM, NOT TRUTH (`AGENTS.md` §11). It cannot tell whether the
statements in `migrations/env.py` are correct - `tests/integration/test_p015_t1534_*` and the
provisioning tests do that by building a schema and reading the catalogue. What it holds in place is
the thing that decayed before: on 2026-09-21 the same two effects were spelled in three places at once
(`docker/docker-entrypoint.sh` as a `DO $$ ... $$` block, `tests/migrated_schema.py` as two idempotent
statements, `.github/workflows/quality.yml` as a bare `CREATE TABLE`) while the migration entry, the
one thing that actually needs the precondition, had none of them. Every new caller of
`alembic upgrade head` had to know the secret or die at revision 011.

WHAT IT DOES NOT SEE, said out loud so its silence is not read as proof (§12):

* `.github/workflows/quality.yml:327` STILL carries a fourth copy, a bare
  `CREATE TABLE alembic_version (version_num VARCHAR(128) ...)` that seeds the container-smoke fixture
  at revision 016. It is redundant now - the migration entry would create the table itself - but that
  file belongs to another slice of T1701 and is not touched here. The scan deliberately excludes it
  and this comment is the record of why; when that slice lands, delete the exclusion.
* It scans `.py`, `.sh`, `.ps1` and `.sql` only. Documentation is deliberately outside it:
  `docs/ru/05-deployment.md` still prints the two statements, on purpose, for an operator whose tool
  BYPASSES the migration entry. A copy in prose is not a second owner, but this guard would not see
  one that was.
* It scans the tree as TEXT. A copy written through string building, a template, or a different
  spelling of the same DDL passes it.
* It says nothing about who CALLS the migrations. A caller that skips them entirely is invisible here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: The migration entry, and the only file allowed to spell the DDL.
_OWNER = _ROOT / "migrations" / "env.py"

#: Trees that are searched for a second copy.
_SCANNED_DIRECTORIES = ("app", "docker", "migrations", "scripts", "tests")

_SCANNED_SUFFIXES = (".py", ".sh", ".ps1", ".sql")

#: Files that may spell it although they are not the owner, each with its reason.
_ALLOWED: dict[Path, str] = {
    Path(__file__).resolve(): "this guard quotes the statements in order to look for them",
}

#: Either half of the create-or-widen, in any of the spellings this repository has used.
_BOOTSTRAP_PATTERNS = (
    re.compile(r"CREATE\s+TABLE(\s+IF\s+NOT\s+EXISTS)?\s+alembic_version", re.IGNORECASE),
    re.compile(r"ALTER\s+TABLE\s+alembic_version", re.IGNORECASE),
)


def _files_that_spell_the_bootstrap(paths) -> dict[Path, list[str]]:
    """Every scanned file holding either half of the DDL, with the lines that hold it.

    Factored out so the scanner itself can be fed a planted file: a guard whose detector is only ever
    run over a tree that satisfies it cannot distinguish "nothing to find" from "finds nothing"
    (§9, anti-vacuum).
    """

    found: dict[Path, list[str]] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = [
            line.strip()
            for line in text.splitlines()
            if any(pattern.search(line) for pattern in _BOOTSTRAP_PATTERNS)
        ]
        if hits:
            found[path] = hits
    return found


def _scanned_paths():
    for directory in _SCANNED_DIRECTORIES:
        for path in sorted((_ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in _SCANNED_SUFFIXES:
                yield path


def test_the_migration_entry_spells_the_bootstrap() -> None:
    """NON-VACUITY, and the half that matters most: the owner really carries it.

    A guard that only forbids copies would be satisfied by a tree where the DDL exists nowhere at
    all - which is exactly the state in which `alembic upgrade head` dies at 010 -> 011.
    """

    hits = _files_that_spell_the_bootstrap([_OWNER])
    assert _OWNER in hits, (
        f"{_OWNER} does not spell the create-or-widen of `alembic_version`. Without it a fresh "
        f"database dies at 010 -> 011 with StringDataRightTruncationError, and every caller of the "
        f"migration entry is back to carrying its own copy."
    )
    body = _OWNER.read_text(encoding="utf-8")
    assert "VARCHAR(128)" in body or "VARCHAR({ALEMBIC_VERSION_COLUMN_LENGTH})" in body, (
        "the owner's DDL does not widen the column to 128; this tree's longest revision id is 46 "
        "characters and Alembic 1.13.1 hardcodes VARCHAR(32)."
    )


def test_no_second_copy_of_the_bootstrap_exists_in_the_scanned_trees() -> None:
    """One owner. A second copy is how the entry ended up without the precondition it needs.

    MUTATION that must redden this: paste the two statements back into `tests/migrated_schema.py`.
    """

    offenders = {
        path: lines
        for path, lines in _files_that_spell_the_bootstrap(_scanned_paths()).items()
        if path.resolve() != _OWNER.resolve() and path.resolve() not in _ALLOWED
    }
    assert not offenders, (
        "the `alembic_version` precondition is spelled outside `migrations/env.py`:\n"
        + "\n".join(
            f"  {path.relative_to(_ROOT)}: {lines}" for path, lines in sorted(offenders.items())
        )
        + "\nCall the migration entry instead of carrying a copy of its DDL (T1701)."
    )


def test_the_scanner_finds_a_planted_copy(tmp_path: Path) -> None:
    """ANTI-VACUUM: the detector above must be able to fail.

    Both spellings are planted, because a scanner that only knew `CREATE TABLE` would miss the
    widening - and the widening is the half that matters on a database that already exists.
    """

    creator = tmp_path / "planted_create.sh"
    creator.write_text(
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(128))\n", encoding="utf-8"
    )
    widener = tmp_path / "planted_alter.py"
    widener.write_text(
        'SQL = "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"\n',
        encoding="utf-8",
    )
    innocent = tmp_path / "innocent.py"
    innocent.write_text(
        'ROW = "SELECT version_num FROM alembic_version"\n', encoding="utf-8"
    )

    found = _files_that_spell_the_bootstrap([creator, widener, innocent])

    assert creator in found and widener in found, found
    assert innocent not in found, (
        "the scanner fires on an ordinary read of `alembic_version`, so its findings would be noise "
        "rather than evidence"
    )


@pytest.mark.parametrize("directory", _SCANNED_DIRECTORIES)
def test_every_scanned_directory_exists(directory: str) -> None:
    """A scan over a directory that was renamed away reports zero findings and proves nothing."""

    assert (_ROOT / directory).is_dir(), f"{directory}/ is not in this tree any more"
