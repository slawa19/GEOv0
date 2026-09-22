"""T1701, architecture guard: `alembic_version` is created-or-widened in ONE place.

THIS IS A POLICY GUARD AND IT CHECKS FORM, NOT TRUTH (`AGENTS.md` §11). It cannot tell whether the
statements in `migrations/env.py` are correct - `tests/integration/test_p015_t1534_*` and the
provisioning tests do that by building a schema and reading the catalogue. What it holds in place is
the thing that decayed before: on 2026-09-21 the same two effects were spelled in three places at once
(`docker/docker-entrypoint.sh` as a `DO $$ ... $$` block, `tests/migrated_schema.py` as two idempotent
statements, `.github/workflows/quality.yml` as a bare `CREATE TABLE`) while the migration entry, the
one thing that actually needs the precondition, had none of them. Every new caller of
`alembic upgrade head` had to know the secret or die at revision 011.

WHAT IT SCANS, AND WHY THAT LIST GREW (2026-09-22, Codex external review of `e2e1380..37fec08`).
The first version promised "exactly one owner" while scanning `.py`, `.sh`, `.ps1` and `.sql` under
`app docker migrations scripts tests` - so the whole of `.github` and every YAML file were outside it
STRUCTURALLY. The named exception for the container-smoke fixture in `.github/workflows/quality.yml`
therefore excluded nothing, and a brand-new copy in any workflow would have been invisible. `.github`
and `.yml`/`.yaml` are now in scope, the fixture copy is an allow-list entry with a reason, and
`test_every_allowed_file_still_spells_it` fails when that entry goes stale - so the exclusion cannot
outlive the thing it excuses. The patterns also grew: `ALTER TABLE "alembic_version"` and
`CREATE TABLE public.alembic_version` are ordinary valid spellings and used to walk straight past.

WHAT IT STILL DOES NOT SEE, said out loud so its silence is not read as proof (§12):

* Files outside the scanned trees, and suffixes outside the scanned list. Documentation is
  deliberately outside it: `docs/ru/05-deployment.md` still prints the two statements, on purpose,
  for an operator whose tool BYPASSES the migration entry. A copy in prose is not a second owner, but
  this guard would not see one that was.
* It scans the tree as TEXT. A copy written through string building, a template, a heredoc assembled
  at runtime, or a different spelling of the same DDL passes it.
* It says nothing about who CALLS the migrations. A caller that skips them entirely is invisible here.

So the honest claim is narrower than "exactly one owner in this repository": it is "one owner among
the files this guard reads, plus the exceptions listed below, each of which still exists".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: The migration entry, and the only file allowed to spell the DDL.
_OWNER = _ROOT / "migrations" / "env.py"

#: Trees that are searched for a second copy.
_SCANNED_DIRECTORIES = ("app", "docker", "migrations", "scripts", "tests", ".github")

_SCANNED_SUFFIXES = (".py", ".sh", ".ps1", ".sql", ".yml", ".yaml")

#: Files that may spell it although they are not the owner, each with its reason. Every entry is
#: checked for still being true by `test_every_allowed_file_still_spells_it`: an exception that
#: excuses nothing is how a scan quietly stops covering what it claims to cover.
_ALLOWED: dict[Path, str] = {
    Path(__file__).resolve(): "this guard quotes the statements in order to look for them",
    (_ROOT / ".github" / "workflows" / "quality.yml").resolve(): (
        "the container-smoke fixture seeds a database at revision 016 with a bare "
        "CREATE TABLE alembic_version; it is redundant now that the migration entry creates the "
        "table itself, but it belongs to another slice of T1701. When that slice lands, delete "
        "this entry - the test below will already be red."
    ),
}

#: Suffixes that MUST match at least one scanned file today. `.sql` and `.yaml` are in
#: `_SCANNED_SUFFIXES` for the day a file carries them and are deliberately not here: a suffix that
#: matches nothing is forward coverage, while a suffix everyone believes is covered and is not is a
#: blind spot. `test_the_scan_actually_reaches_files_of_every_declared_suffix` keeps the two apart.
_SUFFIXES_THAT_MUST_MATCH_SOMETHING = (".py", ".sh", ".ps1", ".yml")

#: `alembic_version`, however it is ordinarily written: bare, double-quoted, and/or qualified by a
#: schema that is itself bare or quoted. The trailing boundary is what keeps a DIFFERENT table whose
#: name merely starts with it - `alembic_version_history` - out of the findings.
_TABLE = (
    r'(?:(?:[A-Za-z_][A-Za-z0-9_$]*|"[A-Za-z_][A-Za-z0-9_$]*")\s*\.\s*)?'
    r'(?:"alembic_version"|alembic_version(?![A-Za-z0-9_$]))'
)

#: Either half of the create-or-widen, in any of the spellings this repository has used and in the
#: ordinary ones it has not.
_BOOTSTRAP_PATTERNS = (
    re.compile(rf"CREATE\s+TABLE(\s+IF\s+NOT\s+EXISTS)?\s+{_TABLE}", re.IGNORECASE),
    re.compile(rf"ALTER\s+TABLE(\s+IF\s+EXISTS)?(\s+ONLY)?\s+{_TABLE}", re.IGNORECASE),
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
    """One owner among the scanned files. A second copy is how the entry ended up without it.

    MUTATION that must redden this: paste the two statements back into `tests/migrated_schema.py`,
    or add a second `CREATE TABLE alembic_version` to any workflow under `.github/`.
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


def test_every_allowed_file_still_spells_it() -> None:
    """ANTI-VACUUM for the allow-list: an exclusion that excuses nothing must not survive.

    This is the half the first version was missing. `.github` was outside the scan entirely, so the
    recorded exception for `quality.yml` was decoration: it excluded a file the scanner never read,
    and a second workflow copy would have been just as unseen. Now the exception is load-bearing,
    and this test is what stops it from quietly outliving the copy it excuses.
    """

    stale = sorted(
        str(path.relative_to(_ROOT))
        for path in _ALLOWED
        if path.exists() and not _files_that_spell_the_bootstrap([path])
    )
    assert not stale, (
        f"these files are excused from the one-owner rule but no longer spell the DDL: {stale}. "
        "Delete their entries from _ALLOWED - an exclusion nobody needs is how a scan stops "
        "covering what it says it covers."
    )
    missing = sorted(str(path) for path in _ALLOWED if not path.exists())
    assert not missing, f"_ALLOWED names files that are not in this tree any more: {missing}"


def test_the_workflow_copy_is_excluded_by_name_and_not_by_being_out_of_scope() -> None:
    """The exclusion has to be a NAME, not a blind spot, or a new copy costs nothing to add.

    Feeding the scanner the workflow directly must find the fixture's `CREATE TABLE`; the file is
    then subtracted by `_ALLOWED` rather than never read. A planted second workflow is not allowed
    and must be reported.
    """

    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").resolve()
    assert workflow in _ALLOWED
    assert workflow.suffix in _SCANNED_SUFFIXES
    assert ".github" in _SCANNED_DIRECTORIES
    assert workflow in set(_scanned_paths()), (
        "the excused workflow is not in the scanned set, so excusing it proves nothing"
    )
    assert _files_that_spell_the_bootstrap([workflow]), (
        "the workflow no longer spells the DDL; remove it from _ALLOWED"
    )


def test_the_scanner_finds_a_planted_copy(tmp_path: Path) -> None:
    """ANTI-VACUUM: the detector above must be able to fail.

    Every spelling is planted, because a scanner that only knew bare `CREATE TABLE alembic_version`
    would miss the widening - the half that matters on a database that already exists - and would
    miss the two ordinary forms an operator or a dump writes: the quoted identifier and the
    schema-qualified one. Both walked past the first version of these patterns.
    """

    planted = {
        "planted_create.sh": "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(128))\n",
        "planted_alter.py": (
            'SQL = "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"\n'
        ),
        "planted_quoted.sql": (
            'ALTER TABLE "alembic_version" ALTER COLUMN version_num TYPE VARCHAR(128);\n'
        ),
        "planted_qualified.sql": (
            "CREATE TABLE public.alembic_version (version_num VARCHAR(128) NOT NULL);\n"
        ),
        "planted_qualified_quoted.yml": (
            '        run: psql -c \'ALTER TABLE "public"."alembic_version" OWNER TO geo\'\n'
        ),
        "planted_workflow.yml": (
            "        run: psql --command 'CREATE TABLE alembic_version "
            "(version_num VARCHAR(128) NOT NULL PRIMARY KEY)'\n"
        ),
    }
    written = {}
    for name, body in planted.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        written[name] = path

    innocent = tmp_path / "innocent.py"
    innocent.write_text('ROW = "SELECT version_num FROM alembic_version"\n', encoding="utf-8")
    innocent_drop = tmp_path / "innocent_other_table.sql"
    innocent_drop.write_text("CREATE TABLE alembic_version_history (id int);\n", encoding="utf-8")

    found = _files_that_spell_the_bootstrap(
        list(written.values()) + [innocent, innocent_drop]
    )

    missed = sorted(name for name, path in written.items() if path not in found)
    assert not missed, f"the scanner does not recognise these spellings: {missed}"
    assert innocent not in found, (
        "the scanner fires on an ordinary read of `alembic_version`, so its findings would be noise "
        "rather than evidence"
    )
    assert innocent_drop not in found, (
        "the scanner fires on a different table whose name merely starts with `alembic_version`"
    )


@pytest.mark.parametrize("directory", _SCANNED_DIRECTORIES)
def test_every_scanned_directory_exists(directory: str) -> None:
    """A scan over a directory that was renamed away reports zero findings and proves nothing."""

    assert (_ROOT / directory).is_dir(), f"{directory}/ is not in this tree any more"


def test_the_scan_actually_reaches_files_of_every_declared_suffix() -> None:
    """A suffix in the list that matches nothing is the same blind spot in a smaller size.

    `.yml` was added for `.github`; if a future move left the list naming suffixes no scanned file
    carries, the scan would look wider than it is. Only the suffixes that must match something today
    are asserted - see `_SUFFIXES_THAT_MUST_MATCH_SOMETHING` for why `.sql` and `.yaml` are not.
    """

    seen = {path.suffix for path in _scanned_paths()}
    unreachable = sorted(set(_SUFFIXES_THAT_MUST_MATCH_SOMETHING) - seen)
    assert not unreachable, (
        f"these suffixes are declared scanned but no file in the scanned trees has them: "
        f"{unreachable}"
    )
    assert set(_SUFFIXES_THAT_MUST_MATCH_SOMETHING) <= set(_SCANNED_SUFFIXES)
