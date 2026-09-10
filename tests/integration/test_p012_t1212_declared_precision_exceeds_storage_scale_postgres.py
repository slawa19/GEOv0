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
That is the class the whole 012 programme is about: a number the system promises and does not
hold.

MEASURED 2026-08-25, PostgreSQL 16.9, database ``geov0_test_prec8``, schema at alembic head
``019_trust_lines_partial_unique_live``:

  * ``POST /api/v1/admin/equivalents`` with ``{"code": "PREC12", "precision": 12}`` answered
    **200** - the door and the canon admitted a precision the ledger cannot keep;
  * writing ``1.234567890123`` - a value the declaration ``precision: 12`` says this unit can
    express - into a ``numeric(20,8)`` column and reading it back returns ``1.23456789``.  No
    error, no warning: PostgreSQL **rounds**, in both directions
    (``0.123456789 -> 0.12345679``), and a value below the column's quantum disappears entirely
    (``0.000000000123 -> 0.00000000``);
  * ``to_money_str(Decimal("1.23456789"), 12)`` renders ``"1.234567890000"``, i.e. the system
    then shows twelve digits of a number it holds to eight - four digits of invented certainty,
    at exactly the precision the equivalent declares;
  * and the money door itself refuses the declaration's own quantum:
    ``parse_money_amount("1.234567890123")`` raises ``BadRequestException`` / 400 ``E009``,
    because ``is_storable_money`` is False for it.  At ``precision: 12`` the equivalent's own
    unit of account is unpayable.

THE DECISION THIS GUARDS (012 / S1, owner's decision).  ``Equivalent.precision`` is narrowed to
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


def _discovered_equivalent_datasets() -> list[str]:
    found = []
    for path in REPO_ROOT.rglob("equivalents.json"):
        if _NOT_SOURCE.intersection(path.relative_to(REPO_ROOT).parts):
            continue
        found.append(path.relative_to(REPO_ROOT).as_posix())
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
        rows = payload if isinstance(payload, list) else payload.get("items", payload)
        declared[relative] = [int(row["precision"]) for row in rows if "precision" in row]

    assert sum(len(v) for v in declared.values()) == 15, (
        f"the shipped equivalent count changed: {[(k, len(v)) for k, v in declared.items()]}"
    )
    over = {k: [p for p in v if p > MONEY_MAX_SCALE] for k, v in declared.items()}
    assert not any(over.values()), (
        f"a shipped dataset declares a precision above the storage scale: "
        f"{ {k: v for k, v in over.items() if v} }"
    )
