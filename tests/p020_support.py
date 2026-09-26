"""Programme 020, stage 2: the shared stand of the selection characterization. NOT a test module.

One seeding helper with FIXED identities, so an ordered list of cycle identities can be written down in
a test and compared verbatim, and one expected-failure marker in the 019 shape (`tests/p019_support.py`):
`xfail(raises=TargetMismatch, strict=True)`. A broken stand raises `AssertionError`, which the marker does
not accept; a tree that already meets the target XPASSes and `strict=True` turns that into a failure, so
the stage that switches the detector (020 stage 3, `T2003`) must take the marker off.

WHAT A "CANONICAL IDENTITY" IS HERE (spec, "Решения" -> selection rule): the sorted tuple of ALL debt UUIDs
of the cycle, in their canonical lowercase hyphenated spelling. It is the tie key of the owner's rule and
the thing the R-020-1 lists are written in.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

import pytest

from tests.p019_support import TargetMismatch, require_target  # noqa: F401 - re-exported for 020 tests

_PID_NAMESPACE = uuid.UUID("2d0f2e3a-5a0b-4f7e-9d7c-0200000000a0")


def target_xfail_020(what: str):
    """The 020 marker: an expected `TargetMismatch`, strict, removed by the stage-3 switch (`T2003`)."""

    return pytest.mark.xfail(
        raises=TargetMismatch,
        strict=True,
        reason=(
            "020 target UNDELIVERED (owner's amount-first rule, 2026-09-25), superseded by programme 023's "
            "objective (maximum total eligible debt reduction on a snapshot); 023 decides whether these assertions "
            f"are replaced or removed and records the change there: {what}"
        ),
    )


def participant_uuid(pid: str) -> uuid.UUID:
    return uuid.uuid5(_PID_NAMESPACE, pid)


def debt_uuid(group: int, n: int) -> uuid.UUID:
    """A fixed debt id; `group` keeps graphs of one module apart, `n` orders ids inside a group."""

    return uuid.UUID(int=(group << 64) | n)


@dataclass(frozen=True)
class Edge:
    """One debt `debtor -> creditor` and the controlling trust line `creditor -> debtor`."""

    debt_id: uuid.UUID
    debtor: str
    creditor: str
    amount: str
    status: str = "active"
    consent: object = True  # the trust line's `policy.auto_clearing` value


MISSING_KEY = "<p020:missing-key>"
NULL_POLICY = "<p020:null-policy>"


def policy_for(consent: object):
    """The trust line's `policy` for an `Edge.consent`: a value, a policy without the key, or no policy."""

    if consent == MISSING_KEY:
        return {"can_be_intermediate": True}
    if consent == NULL_POLICY:
        return None
    return {"auto_clearing": consent}


def ring(pids: Sequence[str], amounts: Sequence[str], ids: Sequence[uuid.UUID]) -> list[Edge]:
    """A closed cycle pids[0] -> pids[1] -> ... -> pids[0]; one amount and one debt id per edge."""

    assert len(pids) == len(amounts) == len(ids)
    return [
        Edge(ids[i], pids[i], pids[(i + 1) % len(pids)], amounts[i]) for i in range(len(pids))
    ]


def identity(cycle: Iterable[dict]) -> tuple[str, ...]:
    """The canonical identity of a `find_cycles` cycle: the sorted tuple of all its debt UUIDs."""

    return tuple(sorted(str(uuid.UUID(str(e["debt_id"]))) for e in cycle))


def identity_of(ids: Iterable[uuid.UUID]) -> tuple[str, ...]:
    return tuple(sorted(str(i) for i in ids))


async def seed_graph(session, code: str, edges: Sequence[Edge], *, precision: int = 2):
    """Seed one equivalent, the participants named by `edges`, one trust line and one debt per edge.

    Returns the equivalent row. Participant ids are `uuid5(pid)` and debt ids are the edge's, so every
    identity is known before the test runs.
    """

    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.debt_setup import add_debts

    eq = Equivalent(code=code, precision=precision, is_active=True)
    session.add(eq)
    pids = sorted({p for e in edges for p in (e.debtor, e.creditor)})
    for pid in pids:
        session.add(
            Participant(
                id=participant_uuid(pid),
                pid=pid,
                display_name=pid.upper(),
                public_key=hashlib.sha256(pid.encode()).hexdigest(),
                type="person",
                status="active",
                profile={},
            )
        )
    await session.commit()

    for e in edges:
        session.add(
            TrustLine(
                from_participant_id=participant_uuid(e.creditor),
                to_participant_id=participant_uuid(e.debtor),
                equivalent_id=eq.id,
                limit=Decimal("1000000"),
                policy=policy_for(e.consent),
                status=e.status,
            )
        )
    await session.flush()
    await add_debts(
        session,
        [
            Debt(
                id=e.debt_id,
                debtor_id=participant_uuid(e.debtor),
                creditor_id=participant_uuid(e.creditor),
                equivalent_id=eq.id,
                amount=Decimal(e.amount),
            )
            for e in edges
        ],
        label="p020-graph",
    )
    await session.commit()
    return eq
