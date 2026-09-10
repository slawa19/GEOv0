"""012 / ``T1212`` - an equivalent may declare a precision the ledger cannot keep.

WHAT WAS MEASURED, AND WHY IT IS A DEFECT.  One quantity - "how many fraction digits does a unit
of this equivalent have" - is written down three times in this repository, and until this module
the three did not agree:

  * the PROTOCOL says ``precision`` is an integer ``0-8``
    (``docs/ru/02-protocol-spec.md:143`` and ``:155`` - the supported documentation tree);
  * the STORAGE says eight, and says it in the column type: ``debts.amount`` and
    ``trust_lines.limit`` are ``Numeric(20, 8)`` (migration ``001_initial_schema.py:33`` region,
    ``app/db/models/trustline.py``, ``app/db/models/debt.py``);
  * the CODE said eighteen - ``app/utils/validation.py::validate_equivalent_precision``,
    ``app/schemas/equivalents.py``, ``app/schemas/admin.py`` and ``api/openapi.yaml`` all
    declared ``0..18``.

So an administrator could create ``precision: 12``, the door and the canon accepted it, and the
DECLARED precision was then wider than the precision anything in the system can actually keep.
WHAT THAT IS AND IS NOT (corrected 2026-09-10, external review).  It is NOT a live money path
admitting unstorable money: the door refuses such a value before signing and before writing
(``is_storable_money``, ``F-012-1``), and the rounding below is reached through raw SQL, around
that door.  It IS two things the door cannot answer for - the code contradicted the normative
protocol, and an equivalent could declare a quantum its own door refuses, i.e. a unit of account
that cannot be paid.

MEASURED 2026-08-25, PostgreSQL 16.9, database ``geov0_test_prec8``, schema at alembic head
``019_trust_lines_partial_unique_live``:

  * ``POST /api/v1/admin/equivalents`` with ``{"code": "PREC12", "precision": 12}`` answered
    **200** - the door and the canon admitted a precision the ledger cannot keep;
  * writing ``1.234567890123`` - a value the declaration ``precision: 12`` says this unit can
    express - into a ``numeric(20,8)`` column and reading it back returns ``1.23456789``.  No
    error, no warning: PostgreSQL **rounds**, in both directions
    (``0.123456789 -> 0.12345679``), and a value below the column's quantum disappears entirely
    (``0.000000000123 -> 0.00000000``);
  * ``to_money_str(Decimal("1.23456789"), 12)`` renders ``"1.234567890000"`` - padding to the
    declared precision.  (The first edition of this line called those digits "invented
    certainty"; external review refuted that and it is withdrawn: padding an exact value with
    zeros invents no value, and the rule is the same at every precision.);
  * and the money door itself refuses the declaration's own quantum:
    ``parse_money_amount("1.234567890123")`` raises ``BadRequestException`` / 400 ``E009``,
    because ``is_storable_money`` is False for it.  At ``precision: 12`` the equivalent's own
    unit of account is unpayable.

THE DECISION THIS GUARDS (012 / S1; recorded 2026-09-10 as the orchestrator's, under the owner's
delegation, after an external review that argued for keeping 18).  ``Equivalent.precision`` is narrowed to
``0..8``, matching the protocol and the column.  Real usage never needed more: across every
shipped equivalent dataset (``seeds/equivalents.json``, ``admin-fixtures/**/equivalents.json``,
``admin-ui/public/admin-fixtures/...``) the only declared precisions are ``2`` (14
occurrences) and ``1`` (one occurrence) - counted, not assumed, and re-counted by
``test_no_shipped_equivalent_declares_a_precision_the_ledger_cannot_keep`` below.

THIS MODULE IS THE REGRESSION GUARD ON THAT DECISION.  ``test_the_equivalent_door_refuses...``
was RED before the narrowing (the create answered 200) and is green after.  The other two tests
were green before and after: they are the MEASUREMENT the decision rests on, kept executable so
the reason cannot rot into a story.

PostgreSQL tier on purpose.  On SQLite ``Numeric(20, 8)`` is type affinity only - the value comes
back unrounded - so the defect is invisible there and a SQLite run would be evidence of nothing.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from app.config import settings
from app.db.models.equivalent import Equivalent
from app.utils.exceptions import BadRequestException
from app.utils.money import to_money_str
from app.utils.validation import (
    MONEY_MAX_SCALE,
    is_storable_money,
    parse_money_amount,
    validate_equivalent_precision,
)

# `asyncio` is NOT in this list on purpose: `pytest.ini` sets `asyncio_mode = auto`, so the async
# tests below need no mark, and marking the module put the mark on the one SYNCHRONOUS test here
# as well - which pytest-asyncio reports as a warning, i.e. a real inconsistency printed on every
# postgres run.
pytestmark = [pytest.mark.postgres]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every file in the SOURCE tree that ships `Equivalent` rows.
#:
#: The list is written down AND re-derived below, because each half fails in a way the other does
#: not: a hard-coded list misses a dataset added somewhere new, and a bare glob quietly reports
#: zero files if the layout moves.  The two are compared, so both failures are named.
#:
#: `admin-ui/dist/...` WAS in this list and is deliberately gone (external review, gpt-6-astra,
#: 2026-08-25).  It is BUILD OUTPUT: `.gitignore:152` ignores `admin-ui/dist/`, so it exists only
#: on a machine that has run `npm --prefix admin-ui run build`.  This test passed locally for
#: exactly that reason and would have failed on any clean checkout - the postgres CI job installs
#: backend dependencies only.  A guard whose verdict depends on whether someone built the front
#: end is the false-red twin of a false green, and the copy under `admin-ui/public/` is the source
#: that `dist` is generated FROM, so nothing is left unmeasured by dropping it.
SHIPPED_EQUIVALENT_DATASETS = [
    "seeds/equivalents.json",
    "admin-fixtures/v1/datasets/equivalents.json",
    "admin-fixtures/packs/greenfield-village-100-v2/v1/datasets/equivalents.json",
    "admin-fixtures/packs/riverside-town-50-v2/v1/datasets/equivalents.json",
    "admin-ui/public/admin-fixtures/v1/datasets/equivalents.json",
]

#: Directories that are not source: build output and installed dependencies.  A dataset found
#: under one of these is a copy of a source dataset, and its presence depends on what has been
#: built or installed on this machine rather than on what the repository ships.
_NOT_SOURCE = {"dist", "node_modules", ".git", ".venv", ".local-run", "__pycache__"}


def _carries_equivalent_rows(path: Path) -> bool:
    """Does this JSON file ship rows that are `Equivalent` declarations, whatever it is called?

    THE EVASION THIS CLOSES (external review, 2026-09-10).  Until this function the discovery
    matched the FILENAME `equivalents.json` only, so a shipped dataset under any other name -
    `seeds/currencies.json`, `admin-fixtures/v1/datasets/units.json` - escaped the walk AND the
    written list at once, and the census above would have gone on reporting a clean count of 15
    while a sixteenth declaration sat unmeasured.  That is the worst shape a guard can take: it
    is not that it fails, it is that it reports success about a question it never asked.

    A row is an equivalent declaration if it carries both `code` and `precision` - the two keys
    the census actually reads (`row["precision"]`, keyed by `code` in every shipped file).
    Matching on what the census CONSUMES rather than on where the file lives is what makes the
    two halves cover the same ground.

    MEASURED before choosing this over merely documenting the hole (this tree, 2026-09-10):
    145 JSON files survive the exclusions, 3.6 MB in total; parsing all of them costs **0.097 s**
    against a **0.087 s** directory walk, and returns exactly the five files the filename match
    returned - no false positive, in a tree that contains `package-lock.json` (218 KB) and four
    six-figure fixture datasets.  Total 0.184 s, against **4.9 s** for the `rglob` this replaces
    (which descended into `node_modules/`, `.git/` and `.venv/` in full and only then discarded
    the results by path component).  Closing the hole made the guard 27x cheaper, so the
    "is it proportionate" question answered itself.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # Not a readable JSON document, so not a dataset this repository ships rows in.  This is
        # deliberately silent: the census's job is to find declarations, and a file that cannot
        # be parsed carries none.  A malformed *shipped* dataset is caught by the census itself,
        # which reads every file in `SHIPPED_EQUIVALENT_DATASETS` and would raise there.
        return False

    rows = payload if isinstance(payload, list) else None
    if rows is None and isinstance(payload, dict):
        wrapped = payload.get("items")
        rows = wrapped if isinstance(wrapped, list) else None
    if rows is None:
        return False
    return any(
        isinstance(row, dict) and "code" in row and "precision" in row for row in rows
    )


def _discovered_equivalent_datasets(root: Path = REPO_ROOT) -> list[str]:
    """Re-derive the shipped datasets by walking `root` - the repository by default.

    `root` is a parameter so the discovery can be probed against a SYNTHETIC tree.  The tests
    below do exactly that and never against the real repository, because a probe that needs a
    dataset planted in the source tree to prove the guard can fail is a probe that cannot be run.

    The exclusions are applied by PRUNING the walk rather than by filtering its results, which is
    both faster (see `_carries_equivalent_rows`) and the same predicate: `os.walk` never descends
    into an excluded directory, so no path under one is ever produced.
    """

    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _NOT_SOURCE]
        for filename in filenames:
            if not filename.endswith(".json"):
                continue
            path = Path(dirpath) / filename
            # The filename match is kept as well as the content match, so this function is a
            # strict superset of the one it replaces: an `equivalents.json` that ships an empty
            # list still has to be accounted for by the written list, rather than dropping out
            # of both halves the moment its rows are removed.
            if filename == "equivalents.json" or _carries_equivalent_rows(path):
                found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def test_the_equivalent_door_refuses_a_precision_the_ledger_cannot_keep(
    client, db_session
) -> None:
    """THE GUARD. Red before the narrowing: the create answered 200 for ``precision: 12``.

    Both entrances are probed, because they are two different pieces of code and either one
    left at 18 re-opens the finding: the HTTP door (`AdminEquivalentCreateRequest`, and the
    canon it is checked against) and the ORM writer (`Equivalent.validate_precision` ->
    `validate_equivalent_precision`, which is what scenario seeding and `scripts/seed_db.py`
    go through).

    The counter-check is the half that keeps this from being "refuse everything": 8 must still
    be accepted at both entrances, so the test reacts to the BOUND being moved rather than to
    the door being nailed shut.

    MUTATION THIS CATCHES: restoring `precision > 18` in `app/utils/validation.py`, or `le=18`
    in `app/schemas/admin.py` / `app/schemas/equivalents.py`.
    """

    suffix = uuid.uuid4().hex[:6].upper()

    # The storage scale itself is still admitted - at both entrances.
    accepted = await client.post(
        "/api/v1/admin/equivalents",
        headers=_admin_headers(),
        json={"code": f"OK{suffix}", "precision": MONEY_MAX_SCALE},
    )
    assert accepted.status_code == 200, (
        f"precision {MONEY_MAX_SCALE} is the storage scale and must remain creatable; "
        f"got {accepted.status_code}: {accepted.text}"
    )
    assert accepted.json()["precision"] == MONEY_MAX_SCALE
    assert (
        Equivalent(code=f"ORMOK{suffix}", precision=MONEY_MAX_SCALE).precision
        == MONEY_MAX_SCALE
    )

    # One past the storage scale must not be declarable any more.
    for precision in (MONEY_MAX_SCALE + 1, 12, 18):
        refused = await client.post(
            "/api/v1/admin/equivalents",
            headers=_admin_headers(),
            json={"code": f"BAD{precision}{suffix}", "precision": precision},
        )
        assert refused.status_code in (400, 422), (
            f"an equivalent declaring precision {precision} was created "
            f"({refused.status_code}). `Numeric(20, 8)` keeps eight fraction digits and the "
            f"protocol declares 0-8 (docs/ru/02-protocol-spec.md:155), so this row promises "
            f"a resolution nothing in the system can hold: {refused.text}"
        )
        assert (
            await db_session.execute(
                select(Equivalent).where(Equivalent.code == f"BAD{precision}{suffix}")
            )
        ).scalar_one_or_none() is None, (
            f"the door answered {refused.status_code} for precision {precision} but the row "
            f"was written anyway"
        )

        with pytest.raises(BadRequestException, match="Invalid equivalent precision"):
            Equivalent(code=f"ORMBAD{precision}{suffix}", precision=precision)
        with pytest.raises(BadRequestException, match="Invalid equivalent precision"):
            validate_equivalent_precision(precision)


async def test_a_value_at_a_declared_precision_past_the_storage_scale_is_silently_rounded(
    db_session,
) -> None:
    """THE MEASUREMENT the decision rests on. Green before and after - it is a fact, not a fix.

    A legacy row is inserted through `Equivalent.__table__` rather than the model, because after
    the narrowing the ORM writer refuses `precision: 12` - which is the point of the narrowing,
    and which is also exactly what a row already in a live database looks like.

    Three losses, all silent to the caller, all measured here rather than argued:
    rounding up, rounding down, and complete disappearance below the column's quantum.
    """

    code = f"LEGACY{uuid.uuid4().hex[:6].upper()}"
    declared = 12
    await db_session.execute(
        insert(Equivalent.__table__).values(
            code=code, precision=declared, metadata={}, is_active=True
        )
    )
    stored_precision = (
        await db_session.execute(select(Equivalent.precision).where(Equivalent.code == code))
    ).scalar_one()
    assert stored_precision == declared, (
        "the legacy row was not written as declared, so nothing below measures what it claims"
    )

    await db_session.execute(
        text("CREATE TEMP TABLE IF NOT EXISTS p012_t1212_money (v numeric(20,8))")
    )

    #: (what a `precision: 12` unit says is expressible, what the column actually keeps)
    losses = [
        ("rounded up", "1.234567890123", "1.23456789"),
        ("rounded up across the last kept digit", "0.123456789", "0.12345679"),
        ("erased entirely, below the column quantum", "0.000000000123", "0.00000000"),
    ]
    for label, written, expected in losses:
        await db_session.execute(text("DELETE FROM p012_t1212_money"))
        await db_session.execute(text(f"INSERT INTO p012_t1212_money VALUES ({written})"))
        back = (
            await db_session.execute(text("SELECT v::text FROM p012_t1212_money"))
        ).scalar_one()
        assert back == expected, f"{label}: expected {expected!r} from the column, got {back!r}"
        assert Decimal(back) != Decimal(written), (
            f"{label}: the column returned the written value unchanged ({back!r}). If this "
            f"fails, the run is not against PostgreSQL - on SQLite `Numeric(20, 8)` is affinity "
            f"only and this whole module measures nothing."
        )

    # And what the reader is then shown at the declared precision: twelve digits of a number
    # the ledger holds to eight.
    rendered = to_money_str(Decimal("1.23456789"), declared)
    assert rendered == "1.234567890000", rendered
    assert rendered != "1.234567890123", (
        "the rendering coincidentally equals the value that was written, which would make the "
        "invented-digits half of this measurement vacuous"
    )


async def test_the_money_door_refuses_the_declared_quantum_of_such_an_equivalent(
    db_session,
) -> None:
    """The second, independent consequence: at `precision > 8` the unit itself is unpayable.

    `parse_money_amount` (the one money door, 012/T1201) admits only values `Numeric(20, 8)`
    holds exactly.  So an equivalent declaring `precision: 12` declares a unit of account -
    `1e-12` - that no payment and no trust line may ever carry.  The declaration and the door
    disagree about what one unit of this equivalent is, and the door is the one that matches
    the ledger.
    """

    quantum = Decimal(1).scaleb(-12)
    assert not is_storable_money(quantum)
    with pytest.raises(BadRequestException):
        parse_money_amount(format(quantum, "f"), field="limit")

    # The counter-check: the storage scale's own quantum passes, so the refusal above is about
    # the extra digits and not about small numbers.
    storable_quantum = Decimal(1).scaleb(-MONEY_MAX_SCALE)
    assert is_storable_money(storable_quantum)
    assert parse_money_amount(format(storable_quantum, "f"), field="limit") == storable_quantum


def test_no_shipped_equivalent_declares_a_precision_the_ledger_cannot_keep() -> None:
    """The migration question, answered by counting rather than by assuming.

    Nothing ships a precision above 8, so narrowing the bound needs no data migration.  This is
    a measurement and it is kept executable: if a future dataset ships one, the narrowing
    silently becomes a breaking change for that dataset, and this fails instead.
    """

    assert _discovered_equivalent_datasets() == sorted(SHIPPED_EQUIVALENT_DATASETS), (
        "the set of shipped equivalent datasets moved: found "
        f"{_discovered_equivalent_datasets()}, expected {sorted(SHIPPED_EQUIVALENT_DATASETS)}. "
        "Update the list AND re-run the count below - a dataset that escapes the list escapes "
        "the migration measurement this test is."
    )

    declared: dict[str, list[int]] = {}
    for relative in SHIPPED_EQUIVALENT_DATASETS:
        path = REPO_ROOT / relative
        assert path.exists(), f"{relative} is gone; re-derive the list before trusting the count"
        payload = json.loads(path.read_text(encoding="utf-8"))
        # The `items` fallback used to end in `payload` itself, which is a trap rather than a
        # tolerance: for a dict with no `items` key, iteration then yields KEY STRINGS,
        # `"precision" in row` degrades into a substring test, and the census dies with
        # `TypeError: string indices must be integers` instead of naming the shape it met.
        # Unreachable today - all five shipped datasets are lists - but this is the counting
        # half of a guard whose whole job is to survive a dataset nobody anticipated.
        # Found by the agent that built the discovery probes, 2026-09-10.
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            rows = payload["items"]
        else:
            raise AssertionError(
                f"{relative} is neither a list of equivalents nor an object with an `items` "
                f"list; the census cannot count it, and silently counting zero rows would make "
                f"the migration claim look measured when it is not. Teach this test the shape."
            )
        declared[relative] = [int(row["precision"]) for row in rows if "precision" in row]

    assert sum(len(v) for v in declared.values()) == 15, (
        f"the shipped equivalent count changed: {[(k, len(v)) for k, v in declared.items()]}"
    )
    over = {k: [p for p in v if p > MONEY_MAX_SCALE] for k, v in declared.items()}
    assert not any(over.values()), (
        f"a shipped dataset declares a precision above the storage scale: "
        f"{ {k: v for k, v in over.items() if v} }"
    )


# --------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS ON THE CENSUS ITSELF (added 2026-09-10, external review).
#
# WHAT WAS MEASURED, AND WHY IT WAS A DEFECT.  The census above is the executable half of the
# claim "narrowing `Equivalent.precision` to 0..8 needs no data migration, because nothing
# shipped declares more than 2".  It rested entirely on `_discovered_equivalent_datasets`, and
# the only evidence the tree recorded for that discovery was its own passing result on the real
# repository - where the answer is five files and has been five files throughout.  A discovery
# hard-coded to return those five strings would have produced exactly the same green.  So the
# guard was UNFALSIFIED: nothing in the tree demonstrated that it could report a dataset it
# should report, or refuse one it should refuse.  That is not the same as the guard being wrong;
# it is the guard being unmeasured, which is what the review found and what the four tests below
# fix.
#
# All four run against a tree built under `tmp_path`.  Probing discovery by planting a dataset
# in the real repository would either dirty a shared working tree or, worse, leave behind a file
# whose later removal silently re-greens the probe.
# --------------------------------------------------------------------------------------------

#: The excluded directory names, written down a SECOND time, on purpose.
#:
#: `_NOT_SOURCE` is the set the code consults; this tuple is what the probes below iterate.  If
#: they were the same object, deleting a name from `_NOT_SOURCE` would delete its probe with it
#: and the loss of coverage would be invisible - the exact failure mode the census itself was in.
#: `test_every_excluded_directory_name_still_has_its_own_probe` ties the two together, so a name
#: added to one and not the other is a red rather than a quiet gap.
_EXCLUDED_DIRECTORY_PROBES = (
    "dist",
    "node_modules",
    ".venv",
    ".local-run",
    "__pycache__",
    ".git",
)

#: The smallest thing that is unambiguously an equivalent declaration: the two keys the census
#: reads.  `precision: 2` because that is what fourteen of the fifteen shipped rows declare.
_ONE_EQUIVALENT_ROW = [{"code": "UAH", "precision": 2}]


def _plant(root: Path, relative: str, rows: object = None) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _ONE_EQUIVALENT_ROW if rows is None else rows
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _mirror_of_the_shipped_tree(root: Path) -> None:
    """Reproduce the five real datasets at their real paths, under a synthetic root."""

    for relative in SHIPPED_EQUIVALENT_DATASETS:
        _plant(root, relative)


def test_the_census_reports_an_equivalent_dataset_shipped_somewhere_new(tmp_path) -> None:
    """THE NEGATIVE CONTROL. A sixth dataset must break the comparison the census makes.

    WHY A TEST THAT MERELY PASSES PROVES NOTHING HERE.  The census asserts
    `_discovered_equivalent_datasets() == sorted(SHIPPED_EQUIVALENT_DATASETS)`, and on the real
    repository both sides have been the same five strings since the guard was written.  Every
    green it has ever produced is equally consistent with a working discovery and with one that
    returns a hard-coded copy of the list it is compared against - the two halves that are
    supposed to check each other would then be one half, written twice.  Passing on the real
    tree is therefore not evidence; the only evidence is a tree in which the guard SHOULD fail
    and does.  This test builds that tree.

    The assertion is deliberately made about the discovered LIST rather than by running the
    census under a patched root: it is the discovery that has to notice the new file, and
    asserting on its return value says which of the two halves is being measured.

    MUTATION THIS CATCHES: `_discovered_equivalent_datasets` returning a constant, or reduced to
    a lookup of `SHIPPED_EQUIVALENT_DATASETS`, or narrowed to the five known directories.
    """

    _mirror_of_the_shipped_tree(tmp_path)

    # The baseline first, because the rejection below means nothing unless the same code agrees
    # with the written list when the tree matches it.  This is what pins the probe to the census:
    # a discovery that over-reports everywhere would fail here instead.
    assert _discovered_equivalent_datasets(tmp_path) == sorted(SHIPPED_EQUIVALENT_DATASETS), (
        "the synthetic mirror of the shipped tree does not reproduce the census's own baseline, "
        "so the rejection measured below would not be attributable to the extra dataset"
    )

    _plant(tmp_path, "app/data/equivalents.json")

    discovered = _discovered_equivalent_datasets(tmp_path)
    assert "app/data/equivalents.json" in discovered, (
        "a sixth equivalent dataset was planted in the tree and the discovery did not report "
        "it. The census cannot fail, so its green says nothing about whether the migration "
        "measurement covers everything shipped."
    )
    assert discovered != sorted(SHIPPED_EQUIVALENT_DATASETS), (
        "the discovery returned exactly the written list even though the tree holds one more "
        "dataset than the list does - the comparison in "
        "`test_no_shipped_equivalent_declares_a_precision_the_ledger_cannot_keep` is therefore "
        "incapable of reporting a dataset added somewhere new"
    )


@pytest.mark.parametrize("excluded", _EXCLUDED_DIRECTORY_PROBES)
def test_an_exclusion_hides_only_its_own_directory_and_not_the_source_beside_it(
    tmp_path, excluded: str
) -> None:
    """The other side of the discovery: the exclusions must not swallow a real dataset.

    Both directions are needed and they fail differently.  An exclusion that is too WIDE makes
    the census under-report - a shipped dataset drops out of the walk, the comparison still
    matches the written list, and the migration count silently stops covering it.  An exclusion
    that is too NARROW makes it over-report - `admin-ui/dist/` reappears and the guard's verdict
    starts depending on whether someone has run `npm --prefix admin-ui run build` on this
    machine, which is the false red the `SHIPPED_EQUIVALENT_DATASETS` note above already records
    as having happened once.

    One case per excluded name, from a tuple written down independently of `_NOT_SOURCE`, so a
    future edit that drops a name leaves a red test rather than a quietly missing probe.

    MUTATION THIS CATCHES: `_NOT_SOURCE` emptied, or the pruning line in
    `_discovered_equivalent_datasets` deleted, or an individual name removed from the set.
    """

    source = "admin-fixtures/v1/datasets/equivalents.json"
    _plant(tmp_path, source)
    # The same dataset copied under the excluded directory - the shape build output and installed
    # dependencies actually take: a duplicate of a source file, at a deeper path.
    copied = f"admin-ui/{excluded}/admin-fixtures/v1/datasets/equivalents.json"
    _plant(tmp_path, copied)
    # And once with the excluded name as the FIRST component, because the previous implementation
    # matched path components anywhere while the current one prunes at every level; a rewrite
    # that only checked, say, the parent directory would pass the case above and fail this one.
    top_level = f"{excluded}/equivalents.json"
    _plant(tmp_path, top_level)

    discovered = _discovered_equivalent_datasets(tmp_path)

    assert source in discovered, (
        f"excluding {excluded!r} also removed a dataset on a legitimate source path "
        f"({source}). The census would then compare a short list against the written one and "
        f"report a dataset MISSING - or, if the written list were trimmed to match, stop "
        f"counting a file the repository really ships."
    )
    assert copied not in discovered, (
        f"a copy under {excluded!r} was reported as a shipped dataset. {excluded!r} is build "
        f"output or an installed dependency, so the census's verdict would depend on what has "
        f"been built or installed on this machine rather than on what the repository ships."
    )
    assert top_level not in discovered, (
        f"a dataset directly under a top-level {excluded!r} was reported as shipped"
    )


def test_every_excluded_directory_name_still_has_its_own_probe() -> None:
    """The tie between `_NOT_SOURCE` and the probes above, so neither can drift alone.

    Without this, adding a name to `_NOT_SOURCE` would exclude a directory that no test ever
    demonstrates is excluded, and removing one would delete its probe along with it.  Either way
    the coverage would change with nothing to show for it - the same defect, one level up, that
    this whole block exists to fix.
    """

    assert set(_EXCLUDED_DIRECTORY_PROBES) == _NOT_SOURCE, (
        "`_NOT_SOURCE` and the probe list have drifted: "
        f"excluded but never probed {_NOT_SOURCE - set(_EXCLUDED_DIRECTORY_PROBES)}, "
        f"probed but no longer excluded {set(_EXCLUDED_DIRECTORY_PROBES) - _NOT_SOURCE}. "
        "Add the missing case rather than deleting the assertion."
    )
    assert len(_EXCLUDED_DIRECTORY_PROBES) == len(set(_EXCLUDED_DIRECTORY_PROBES)), (
        "a duplicated name in the probe list inflates the case count without adding coverage"
    )


def test_a_dataset_shipped_under_another_filename_does_not_evade_the_census(tmp_path) -> None:
    """The evasion the review named, closed rather than documented - because closing was cheaper.

    THE HOLE.  Discovery used to match the filename `equivalents.json`, and the census compares
    the result with a written list of five such filenames.  A dataset shipped as
    `seeds/currencies.json` was therefore invisible to BOTH halves at once, and the census would
    have gone on asserting a clean count of 15 while a sixteenth declaration - possibly
    `precision: 12`, the very thing this module exists to forbid - sat in the tree unmeasured.

    WHY CLOSED AND NOT MERELY NAMED.  Measured on this tree, 2026-09-10: reading and parsing
    every JSON file that survives the exclusions - 145 files, 3.6 MB, `package-lock.json`
    (218 KB) and four six-figure fixture datasets among them - costs 0.097 s on top of a 0.087 s
    pruned walk, and returns exactly the five files the filename match returned, with no false
    positive.  The whole discovery is now 27x cheaper than the `rglob` it replaces (4.9 s,
    because that walk descended through `node_modules/`, `.git/` and `.venv/` in full and only
    then discarded them by path component).  A hole that costs 0.097 s to close is not a limit
    worth documenting instead.

    WHAT REMAINS, NAMED PRECISELY.  Discovery now matches any JSON file carrying rows with both
    `code` and `precision`.  A dataset shipped in a format the census cannot read anyway - YAML,
    CSV, SQL seed statements, rows nested under a key other than `items`, or precision written
    under a different key - still evades it.  That is a smaller and more honest limit than the
    one it replaces: such a file is not merely undiscovered, it is unreadable by the counting
    code above, so closing it would mean teaching the census a second format rather than
    widening a glob.  What would catch it meanwhile is the count itself - `== 15` - which reds
    as soon as the readable datasets change, and the fixture loader, which would have to learn
    the new format before anything could ship in it.

    MUTATION THIS CATCHES: `_carries_equivalent_rows` reduced to `return False`, or the content
    branch dropped from `_discovered_equivalent_datasets`.
    """

    renamed = _plant(tmp_path, "seeds/currencies.json")
    wrapped = _plant(
        tmp_path, "admin-fixtures/v1/datasets/units.json", {"items": _ONE_EQUIVALENT_ROW}
    )
    assert renamed.exists() and wrapped.exists()

    discovered = _discovered_equivalent_datasets(tmp_path)
    assert "seeds/currencies.json" in discovered, (
        "an equivalent dataset under a different filename was not discovered, so it escapes the "
        "written list and the walk at the same time and is counted by neither"
    )
    assert "admin-fixtures/v1/datasets/units.json" in discovered, (
        "an equivalent dataset wrapped in `items` was not discovered, although the census reads "
        "exactly that shape (`payload.get('items', payload)`)"
    )

    # The counter-check, and the half that keeps the content match from degenerating into "every
    # JSON file": the tree is full of JSON that is not an equivalent dataset, and a matcher that
    # claimed all of it would red the census permanently - a false alarm being just another way
    # for a guard to stop carrying information.
    _plant(
        tmp_path,
        "admin-fixtures/v1/datasets/trustlines.json",
        [{"from": "alice", "to": "bob", "limit": "100.00"}],
    )
    _plant(tmp_path, "admin-ui/package-lock.json", {"name": "admin-ui", "lockfileVersion": 3})
    _plant(tmp_path, "tsconfig.json", {"compilerOptions": {"strict": True}})
    # A row carrying only ONE of the two keys is not a declaration: `code` alone is every
    # equivalent REFERENCE in the fixtures, and would match nearly every dataset in the tree.
    _plant(tmp_path, "admin-fixtures/v1/datasets/accounts.json", [{"code": "UAH"}])
    _plant(tmp_path, "docs/precisions.json", [{"precision": 2}])
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    discovered = _discovered_equivalent_datasets(tmp_path)
    for not_a_dataset in (
        "admin-fixtures/v1/datasets/trustlines.json",
        "admin-ui/package-lock.json",
        "tsconfig.json",
        "admin-fixtures/v1/datasets/accounts.json",
        "docs/precisions.json",
        "broken.json",
    ):
        assert not_a_dataset not in discovered, (
            f"{not_a_dataset} was reported as a shipped equivalent dataset. The census would "
            f"then compare a list containing it against `SHIPPED_EQUIVALENT_DATASETS` and fail "
            f"permanently, or try to read `row['precision']` out of it and raise."
        )
