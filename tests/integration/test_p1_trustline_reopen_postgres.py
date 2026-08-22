"""RT-009-3: closing a trustline must not make the triple permanently unusable.

Program 009, finding `F-009-3` (`B-A3-004`, P1).

WHAT WAS WRONG, and it was the constraint rather than the guard:

* the protocol blocks TRUST_LINE_CREATE only on an **active** line -- «Не существует
  активной линии (from, to, equivalent)», ``docs/ru/02-protocol-spec.md:333``;
* TRUST_LINE_CLOSE *sets* ``status='closed'`` and keeps the row (``:379``), so a closed
  incarnation is history, not an occupied slot;
* but ``uq_trust_lines_from_to_equivalent`` was declared WITHOUT ``status``
  (``migrations/versions/001_initial_schema.py:103-108``, mirroring
  ``docs/ru/03-architecture.md:500``), so the database refused the very row the protocol
  permits;
* ``IntegrityError`` was caught nowhere in ``app/core/trustlines/`` and only
  ``GeoException``/``RequestValidationError`` are registered in ``main.py``, so the user
  got HTTP 500 from two ordinary calls.

Migration ``019_trust_lines_partial_unique_live`` narrows uniqueness to live rows, which
is what this test pins: the second create yields a NEW incarnation, the closed row keeps
its identity, and at most one live line exists per triple.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.core.trustlines.service import TrustLineService
from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest
from app.utils.exceptions import GeoException


pytestmark = pytest.mark.postgres


def _sign(private_key_b64: str, payload: dict) -> str:
    signing_key = SigningKey(base64.b64decode(private_key_b64))
    return base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")


async def _make_pair(db_session):
    """Create one equivalent and two participants with a real keypair for the sender."""
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:9]).upper(),
        symbol="T",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender_pub, sender_priv = generate_keypair()
    sender = Participant(
        id=uuid.uuid4(),
        pid="from-" + nonce,
        display_name="Sender",
        public_key=sender_pub,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid="to-" + nonce,
        display_name="Receiver",
        public_key="pk-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.commit()
    return eq, sender, sender_priv, receiver


@pytest.mark.asyncio
async def test_recreating_a_closed_trustline_does_not_raise_a_raw_db_error(db_session):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only gate: the applied unique constraint is the subject under test")

    eq, sender, sender_priv, receiver = await _make_pair(db_session)
    service = TrustLineService(db_session)

    create_payload = {"to": receiver.pid, "equivalent": eq.code, "limit": str(Decimal("100"))}
    first = await service.create(
        sender.id,
        TrustLineCreateRequest(
            to=receiver.pid,
            equivalent=eq.code,
            limit=Decimal("100"),
            signature=_sign(sender_priv, create_payload),
        ),
    )
    await db_session.commit()

    await service.close(
        first.id,
        sender.id,
        TrustLineCloseRequest(signature=_sign(sender_priv, {"id": str(first.id)})),
    )
    await db_session.commit()

    # Same triple again: this is two ordinary calls by one user.
    second_request = TrustLineCreateRequest(
        to=receiver.pid,
        equivalent=eq.code,
        limit=Decimal("100"),
        signature=_sign(sender_priv, create_payload),
    )

    try:
        second = await service.create(sender.id, second_request)
    except GeoException:
        # A declared domain outcome would still satisfy the general contract, so this
        # branch is not a failure of the reproducer itself.
        second = None
    except Exception as exc:  # noqa: BLE001 - the whole point is to name what leaks out
        pytest.fail(
            "recreating a closed trustline surfaced a non-domain error, which reaches "
            f"the user as HTTP 500: {type(exc).__name__}: {exc}"
        )

    # Acceptance for the behaviour chosen after reading the protocol (variant E):
    # TRUST_LINE_CREATE is blocked only by an ACTIVE line
    # (docs/ru/02-protocol-spec.md:333), and closing keeps the old row as history
    # (`:379`).  So the second create must produce a NEW incarnation.
    assert second is not None, (
        "TRUST_LINE_CREATE is blocked only by an active line per protocol §5.1, "
        "so recreating after close must succeed with a new incarnation"
    )
    assert second.id != first.id, "the new incarnation must be a new row, not a mutated one"
    assert str(second.status) == "active"
    assert Decimal(str(second.limit)) == Decimal("100")

    rows = (
        await db_session.execute(
            select(TrustLine).where(
                TrustLine.from_participant_id == sender.id,
                TrustLine.to_participant_id == receiver.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalars().all()
    live = [r for r in rows if str(r.status) != "closed"]
    closed = [r for r in rows if str(r.status) == "closed"]

    assert len(live) == 1, f"at most one live line per triple, found {len(live)}"
    assert len(closed) == 1, "the closed incarnation must survive as history"
    assert closed[0].id == first.id, "history must keep the original trustline_id"
