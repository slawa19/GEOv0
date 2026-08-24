from uuid import UUID
from decimal import Decimal
from typing import List, Literal
from sqlalchemy import func, select, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.exceptions import (
    BadRequestException,
    NotFoundException,
    ForbiddenException,
    ConflictException,
    InvalidSignatureException,
)
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import verify_signature

from app.db.models.trustline import TrustLine
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.db.models.debt import Debt
from app.db.models.audit_log import IntegrityAuditLog
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest, TrustLineUpdateRequest
from sqlalchemy import inspect as sa_inspect
from app.utils.validation import (
    parse_money_amount,
    validate_equivalent_code,
    validate_trustline_policy,
)
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.payments.router import PaymentRouter

_LIVE_TRUSTLINE_INDEX = "uq_trust_lines_live_from_to_equivalent"


def _is_live_trustline_uniqueness_violation(exc: IntegrityError) -> bool:
    """True only for a clash with the live-trustline partial unique index.

    Identity matters: renaming an unrelated IntegrityError into "trustline already exists"
    hands the caller a conflict they can neither understand nor act on.

    Only the DRIVER error is inspected, never `str(exc)`: the latter embeds the INSERT
    statement, whose column list contains `from_participant_id`/`to_participant_id`, so a
    text match against it classifies *every* failing INSERT on this table as a uniqueness
    clash.  An external review demonstrated exactly that.

    A second review then showed the text fallback was still too loose.  It now requires the
    full triple, an explicit uniqueness signal AND the table name, and it walks the whole
    `__cause__` chain rather than one level.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False

    # Collect the whole cause chain once: drivers wrap differently, nesting depth is not
    # fixed, and the facts we need are spread across several links of it.  This is the
    # shape `ClearingService._postgres_error_codes` already uses in this codebase.
    chain: list = []
    seen: set[int] = set()
    node = orig
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        chain.append(node)
        node = getattr(node, "__cause__", None) or getattr(node, "__context__", None)

    for link in chain:
        name = getattr(link, "constraint_name", None)
        if name:
            # The driver told us exactly which constraint failed -- no guessing needed.
            return str(name) == _LIVE_TRUSTLINE_INDEX

    # PostgreSQL without a constraint name: 23505 is unique_violation.  The sqlstate and
    # the table/detail need NOT live on the same link: SQLAlchemy's asyncpg adapter raises
    # a wrapper carrying only `pgcode`/`sqlstate` and chains the real asyncpg error, which
    # is the one holding `table_name` and `detail`
    # (sqlalchemy/dialects/postgresql/asyncpg.py:785-796).  Reading them off the first link
    # with a sqlstate therefore saw an empty table and a message without the DETAIL line,
    # and rejected a genuine live-triple conflict -- a 500 on exactly the path this
    # classifier exists to keep declared.
    if any(
        (getattr(link, "sqlstate", None) or getattr(link, "pgcode", None)) == "23505"
        for link in chain
    ):
        tables = [str(getattr(link, "table_name", "") or "") for link in chain]
        tables = [t for t in tables if t]
        if tables and all(t != "trust_lines" for t in tables):
            return False
        detail = " ".join(
            f"{getattr(link, 'detail', '') or ''} {link}" for link in chain
        )
        return _matches_live_triple(detail)

    text = str(orig)
    lowered = text.lower()
    if "unique" not in lowered:
        # CHECK, NOT NULL and foreign-key violations keep their own meaning.
        return False
    if _LIVE_TRUSTLINE_INDEX in text:
        return True
    if "trust_lines" not in lowered:
        return False
    return _matches_live_triple(text)


def _matches_live_triple(text: str) -> bool:
    """All three columns of the live index must be named, not just two of them."""
    lowered = text.lower()
    return all(
        column in lowered
        for column in ("from_participant_id", "to_participant_id", "equivalent_id")
    )


class TrustLineService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, from_participant_id: UUID, data: TrustLineCreateRequest) -> TrustLine:
        if not isinstance(getattr(data, "signature", None), str) or not data.signature:
            raise InvalidSignatureException("Missing signature")

        from_participant = await self.session.get(Participant, from_participant_id)
        if not from_participant:
            raise NotFoundException("Sender not found")

        # Storage-capacity door (012 / F-012-1).  `TrustLine.limit` is Numeric(20, 8) and this
        # service never validated the amount at all -- the schema only bounds it with `ge=0`.
        # Checked BEFORE `verify_signature` and before any write, so a limit the column cannot
        # hold can never become a signed commitment.
        #
        # `data.limit` is the client's own STRING (`TrustLineCreateRequest.limit: str`, as
        # `api/openapi.yaml` has declared all along), for the same reason `request.amount` is
        # one at `POST /payments`: the signature below is taken over it verbatim.  While the
        # schema typed it `Decimal`, pydantic destroyed the client's spelling before this
        # method ran, and whatever we signed was a spelling `str(Decimal)` re-invented -- for
        # `"0.00000001"` that is `"1E-8"`, so the client's signature over its own bytes could
        # never verify and the smallest storable limit was unsignable.  `require_non_negative`
        # is the schema's former `ge=0`, now behind the door with the other money rules.
        limit = parse_money_amount(data.limit, field="limit", require_non_negative=True)

        # Signature validation (proof-of-possession + binding of request fields).  `limit` is
        # the client's string verbatim -- see the door note above.
        signed_payload: dict = {
            "to": data.to,
            "equivalent": data.equivalent,
            "limit": data.limit,
        }
        if data.policy is not None:
            signed_payload["policy"] = data.policy

        try:
            verify_signature(from_participant.public_key, canonical_json(signed_payload), data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        validate_equivalent_code(data.equivalent)
        if data.policy is not None:
            validate_trustline_policy(data.policy)

        # Check existence of 'to' participant (by PID)
        stmt = select(Participant).where(Participant.pid == data.to)
        result = await self.session.execute(stmt)
        to_participant = result.scalar_one_or_none()
        if not to_participant:
            raise NotFoundException("Recipient participant not found")

        # Check if self-trust
        if from_participant_id == to_participant.id:
            raise BadRequestException("Cannot create trustline to self")

        # Check equivalent
        stmt = select(Equivalent).where(Equivalent.code == data.equivalent)
        result = await self.session.execute(stmt)
        equivalent = result.scalar_one_or_none()
        if not equivalent:
            raise NotFoundException(f"Equivalent '{data.equivalent}' not found")

        checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=equivalent.id,
        )

        # Only a LIVE line blocks a new one.  This matches the protocol precondition of
        # TRUST_LINE_CREATE — «Не существует активной линии (from, to, equivalent)»
        # (docs/ru/02-protocol-spec.md:333) — and, since migration
        # 019_trust_lines_partial_unique_live, it also matches the database: uniqueness is
        # enforced over `status <> 'closed'` only.
        #
        # Before that migration the constraint was unconditional, so a closed incarnation
        # made the INSERT below fail with a raw IntegrityError -> HTTP 500 on two ordinary
        # user calls (finding F-009-3 / B-A3-004).
        stmt = select(TrustLine).where(
            and_(
                TrustLine.from_participant_id == from_participant_id,
                TrustLine.to_participant_id == to_participant.id,
                TrustLine.equivalent_id == equivalent.id,
                TrustLine.status != 'closed'
            )
        )
        result = await self.session.execute(stmt)
        existing_trustline = result.scalar_one_or_none()
        if existing_trustline:
            raise ConflictException("Active trustline already exists")

        # Create TrustLine
        trustline = TrustLine(
            from_participant_id=from_participant_id,
            to_participant_id=to_participant.id,
            equivalent_id=equivalent.id,
            limit=limit,
            policy=data.policy or {},
            status='active'
        )
        # NOTE ON TRANSACTION SHAPE.  The INSERT is deliberately left staged in the
        # caller-owned transaction rather than isolated in a SAVEPOINT.  The fail-closed
        # contract of this service depends on it: if any later step fails (checkpoint,
        # audit), the exception propagates and the caller's rollback must remove the row.
        # `tests/unit/test_trustline_audit_fail_closed.py` pins exactly that, and an
        # earlier attempt to wrap the flush in a savepoint broke it.
        #
        # The uniqueness race is handled at the commit below instead, which is where the
        # conflict actually surfaces: a competing transaction that has not committed yet
        # does not block this INSERT.
        self.session.add(trustline)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # The conflict surfaces here when the competing transaction has already
            # committed.  Translate it into a declared conflict WITHOUT rolling back
            # ourselves: the transaction is already aborted, and the caller's own rollback
            # is what removes the staged row -- the same mechanism the fail-closed contract
            # relies on.
            if not _is_live_trustline_uniqueness_violation(exc):
                raise
            raise ConflictException(
                "Active trustline already exists",
                details={"reason": "CONCURRENT_TRUSTLINE_CREATE"},
            ) from exc

        checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=equivalent.id,
        )
        invariants_status = checkpoint_after.invariants_status or {}
        passed = bool(invariants_status.get("passed", False))
        before_sum = checkpoint_before.checksum if checkpoint_before else ""
        after_sum = checkpoint_after.checksum or before_sum

        self.session.add(
            IntegrityAuditLog(
                operation_type="TRUST_LINE_CREATE",
                tx_id=None,
                equivalent_code=equivalent.code,
                state_checksum_before=before_sum,
                state_checksum_after=after_sum,
                affected_participants={
                    "from": from_participant.pid,
                    "to": to_participant.pid,
                },
                invariants_checked=invariants_status.get("checks") or invariants_status,
                verification_passed=passed,
                error_details=None if passed else invariants_status,
            )
        )

        # The uniqueness conflict can surface HERE rather than at the flush above: a
        # competing transaction that has not committed yet does not block the INSERT, and
        # PostgreSQL raises only when the winner commits.  Both points must therefore map
        # to the same declared conflict.
        # 2026-08-22 / p009_t905 (`F-009-6`).  Everything the response needs is read and
        # materialised INSIDE the uncommitted transaction, and after the commit this path
        # performs no mandatory database read.  Before, the readback happened after the
        # commit, so a failure there reported a mutation that had already happened as
        # failed -- and the retry it invites is not idempotent.  `RT-009-5` shows the
        # failure is reachable, not theoretical.  With the readback moved before the
        # commit, the same failure now happens while the transaction is still open and
        # honestly undoes the mutation instead of misreporting it.
        await self.session.refresh(trustline)
        response = await self._hydrate_trustline(trustline)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if not _is_live_trustline_uniqueness_violation(exc):
                raise
            raise ConflictException(
                "Active trustline already exists",
                details={"reason": "CONCURRENT_TRUSTLINE_CREATE"},
            ) from exc

        # In-memory only; `expire_on_commit=False` (`app/db/session.py:78`) keeps the
        # hydrated attributes valid, so serialising the response touches no connection.
        PaymentRouter.invalidate_cache(equivalent.code)
        return response

    async def update(self, trustline_id: UUID, user_id: UUID, data: TrustLineUpdateRequest) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to update this trustline")

        # TRUST_LINE_UPDATE requires an ACTIVE line (docs/ru/02-protocol-spec.md:355).
        # Before migration 019 a closed row was the only row for its triple, so this was
        # merely a missing check; now a closed incarnation coexists with a live one, and
        # without this guard its id stays patchable forever -- i.e. recorded history could
        # be rewritten after the fact.
        if str(trustline.status) == "closed":
            raise ConflictException(
                "Cannot update a closed trustline",
                details={"reason": "TRUSTLINE_CLOSED", "trustline_id": str(trustline_id)},
            )

        if not isinstance(getattr(data, "signature", None), str) or not data.signature:
            raise InvalidSignatureException("Missing signature")

        user = await self.session.get(Participant, user_id)
        if not user:
            raise NotFoundException("Sender not found")

        # Same storage-capacity door as `create`, before the signature and before the write;
        # `data.limit` is the client's string and the signature covers it verbatim, so the
        # parsed `Decimal` is kept apart from the signed payload -- see the note in `create`.
        new_limit = None
        if data.limit is not None:
            new_limit = parse_money_amount(
                data.limit, field="limit", require_non_negative=True
            )

        signed_payload: dict = {"id": str(trustline_id)}
        if data.limit is not None:
            signed_payload["limit"] = data.limit
        if data.policy is not None:
            signed_payload["policy"] = data.policy

        try:
            verify_signature(user.public_key, canonical_json(signed_payload), data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=trustline.equivalent_id,
        )

        if new_limit is not None:
            used = await self._get_used_amount(trustline)
            if new_limit < used:
                raise BadRequestException(
                    "Cannot reduce trustline limit below used amount",
                    details={"used": str(used), "limit": data.limit},
                )
            trustline.limit = new_limit
        
        if data.policy is not None:
            validate_trustline_policy(data.policy)
            # Merge or replace policy? Usually merge or replace. Assuming replace for now or merge top level.
            # Schema says optional dict. Let's update existing dict.
            current_policy = dict(trustline.policy) if trustline.policy else {}
            current_policy.update(data.policy)
            trustline.policy = current_policy

        await self.session.flush()

        checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=trustline.equivalent_id,
        )
        invariants_status = checkpoint_after.invariants_status or {}
        passed = bool(invariants_status.get("passed", False))
        before_sum = checkpoint_before.checksum if checkpoint_before else ""
        after_sum = checkpoint_after.checksum or before_sum

        # Resolve PIDs for readability.
        from_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.from_participant_id
                )
            )
        ).scalar_one_or_none()
        to_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.to_participant_id
                )
            )
        ).scalar_one_or_none()
        eq_code = (
            await self.session.execute(
                select(Equivalent.code).where(
                    Equivalent.id == trustline.equivalent_id
                )
            )
        ).scalar_one_or_none()

        self.session.add(
            IntegrityAuditLog(
                operation_type="TRUST_LINE_UPDATE",
                tx_id=None,
                equivalent_code=str(eq_code or trustline.equivalent_id),
                state_checksum_before=before_sum,
                state_checksum_after=after_sum,
                affected_participants={
                    "from": str(from_pid or trustline.from_participant_id),
                    "to": str(to_pid or trustline.to_participant_id),
                    "trustline_id": str(trustline_id),
                },
                invariants_checked=invariants_status.get("checks") or invariants_status,
                verification_passed=passed,
                error_details=None if passed else invariants_status,
            )
        )

        equivalent_code = (
            await self.session.execute(
                select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id)
            )
        ).scalar_one()
        # See the note in `create`: readback before commit, no mandatory read after it.
        await self.session.refresh(trustline)
        response = await self._hydrate_trustline(trustline)

        await self.session.commit()
        PaymentRouter.invalidate_cache(equivalent_code)
        return response

    async def close(self, trustline_id: UUID, user_id: UUID, data: TrustLineCloseRequest) -> None:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to close this trustline")

        # Symmetry with `update()`: a closed row is history.  Closing it again would write a
        # fresh TRUST_LINE_CLOSE audit entry and recompute checkpoints for a line that was
        # closed long ago -- history written after the fact.  Harmless to the state, wrong
        # in the journal.  Found by an independent scan after migration 019 made a closed
        # incarnation coexist with a live one.
        if str(trustline.status) == "closed":
            raise ConflictException(
                "Trustline is already closed",
                details={"reason": "TRUSTLINE_CLOSED", "trustline_id": str(trustline_id)},
            )

        if not isinstance(getattr(data, "signature", None), str) or not data.signature:
            raise InvalidSignatureException("Missing signature")

        user = await self.session.get(Participant, user_id)
        if not user:
            raise NotFoundException("Sender not found")

        signed_payload: dict = {"id": str(trustline_id)}
        try:
            verify_signature(user.public_key, canonical_json(signed_payload), data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        # Check debt
        used = await self._get_used_amount(trustline)
        reverse_used = await self._get_reverse_used_amount(trustline)
        if used > 0 or reverse_used > 0:
            raise BadRequestException("Cannot close trustline with non-zero debt")

        checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=trustline.equivalent_id,
        )

        trustline.status = 'closed'

        await self.session.flush()

        checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
            self.session,
            equivalent_id=trustline.equivalent_id,
        )
        invariants_status = checkpoint_after.invariants_status or {}
        passed = bool(invariants_status.get("passed", False))
        before_sum = checkpoint_before.checksum if checkpoint_before else ""
        after_sum = checkpoint_after.checksum or before_sum

        from_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.from_participant_id
                )
            )
        ).scalar_one_or_none()
        to_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.to_participant_id
                )
            )
        ).scalar_one_or_none()
        eq_code = (
            await self.session.execute(
                select(Equivalent.code).where(
                    Equivalent.id == trustline.equivalent_id
                )
            )
        ).scalar_one_or_none()

        self.session.add(
            IntegrityAuditLog(
                operation_type="TRUST_LINE_CLOSE",
                tx_id=None,
                equivalent_code=str(eq_code or trustline.equivalent_id),
                state_checksum_before=before_sum,
                state_checksum_after=after_sum,
                affected_participants={
                    "from": str(from_pid or trustline.from_participant_id),
                    "to": str(to_pid or trustline.to_participant_id),
                    "trustline_id": str(trustline_id),
                },
                invariants_checked=invariants_status.get("checks") or invariants_status,
                verification_passed=passed,
                error_details=None if passed else invariants_status,
            )
        )

        equivalent_code = (
            await self.session.execute(
                select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id)
            )
        ).scalar_one()
        await self.session.commit()
        PaymentRouter.invalidate_cache(equivalent_code)

    async def get_by_participant(
        self,
        participant_id: UUID,
        *,
        direction: str = "all",
        equivalent: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> List[TrustLine]:
        # direction: 'outgoing' (I trust someone) | 'incoming' (someone trusts me) | 'all'
        if status is None:
            query = select(TrustLine).where(TrustLine.status == 'active')
        else:
            query = select(TrustLine).where(TrustLine.status == status)

        if direction == "outgoing":
            query = query.where(TrustLine.from_participant_id == participant_id)
        elif direction == "incoming":
            query = query.where(TrustLine.to_participant_id == participant_id)
        else:
            query = query.where(
                or_(
                    TrustLine.from_participant_id == participant_id,
                    TrustLine.to_participant_id == participant_id,
                )
            )

        if equivalent:
            validate_equivalent_code(equivalent)
            eq = (
                await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent))
            ).scalar_one_or_none()
            if not eq:
                raise NotFoundException(f"Equivalent '{equivalent}' not found")
            query = query.where(TrustLine.equivalent_id == eq.id)

        # `created_at` is not unique -- fixtures write identical values in bulk, and since
        # migration 019 a triple can hold several rows.  Without a unique tie-break the
        # offset/limit pages below may repeat or skip rows between requests.
        query = query.order_by(TrustLine.created_at.desc(), TrustLine.id.asc())

        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        
        hydrated = []
        for tl in trustlines:
            hydrated.append(await self._hydrate_trustline(tl))
        return hydrated

    async def get_one(self, trustline_id: UUID) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()
        if not trustline:
            raise NotFoundException("Trustline not found")
        return await self._hydrate_trustline(trustline)

    async def list_all(
        self,
        *,
        equivalent: str | None = None,
        creditor_pid: str | None = None,
        debtor_pid: str | None = None,
        status: Literal["active", "frozen", "closed"] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[TrustLine]:
        query = select(TrustLine)

        if status:
            query = query.where(TrustLine.status == status)

        if creditor_pid:
            creditor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == creditor_pid)
                )
            ).scalar_one_or_none()
            if creditor_id is None:
                return []
            query = query.where(TrustLine.from_participant_id == creditor_id)

        if debtor_pid:
            debtor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == debtor_pid)
                )
            ).scalar_one_or_none()
            if debtor_id is None:
                return []
            query = query.where(TrustLine.to_participant_id == debtor_id)

        if equivalent:
            eq = (
                await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent))
            ).scalar_one_or_none()
            if eq is None:
                return []
            query = query.where(TrustLine.equivalent_id == eq.id)

        # `created_at` is not unique -- fixtures write identical values in bulk, and since
        # migration 019 a triple can hold several rows.  Without a unique tie-break the
        # offset/limit pages below may repeat or skip rows between requests.
        query = query.order_by(TrustLine.created_at.desc(), TrustLine.id.asc())

        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        return [await self._hydrate_trustline(tl) for tl in trustlines]

    async def count_all(
        self,
        *,
        equivalent: str | None = None,
        creditor_pid: str | None = None,
        debtor_pid: str | None = None,
        status: Literal["active", "frozen", "closed"] | None = None,
    ) -> int:
        query = select(func.count()).select_from(TrustLine)

        if status:
            query = query.where(TrustLine.status == status)

        if creditor_pid:
            creditor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == creditor_pid)
                )
            ).scalar_one_or_none()
            if creditor_id is None:
                return 0
            query = query.where(TrustLine.from_participant_id == creditor_id)

        if debtor_pid:
            debtor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == debtor_pid)
                )
            ).scalar_one_or_none()
            if debtor_id is None:
                return 0
            query = query.where(TrustLine.to_participant_id == debtor_id)

        if equivalent:
            eq = (
                await self.session.execute(
                    select(Equivalent).where(Equivalent.code == equivalent)
                )
            ).scalar_one_or_none()
            if eq is None:
                return 0
            query = query.where(TrustLine.equivalent_id == eq.id)

        return int((await self.session.execute(query)).scalar_one())

    async def _hydrate_trustline(self, trustline: TrustLine) -> TrustLine:
        state = sa_inspect(trustline)

        # Fetch equivalent code (avoid triggering async lazy-load)
        if "equivalent" in state.unloaded or getattr(trustline, "equivalent", None) is None:
            stmt = select(Equivalent).where(Equivalent.id == trustline.equivalent_id)
            result = await self.session.execute(stmt)
            trustline.equivalent = result.scalar_one()

        # Fetch participant PIDs (avoid triggering async lazy-load)
        if "from_participant" in state.unloaded or getattr(trustline, "from_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.from_participant_id)
            result = await self.session.execute(stmt)
            trustline.from_participant = result.scalar_one()

        if "to_participant" in state.unloaded or getattr(trustline, "to_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.to_participant_id)
            result = await self.session.execute(stmt)
            trustline.to_participant = result.scalar_one()

        used = await self._get_used_amount(trustline)
        
        # Attach dynamic properties for Pydantic schema
        # Pydantic model expects: equivalent_code, used, available
        # We can attach them to the object, or return a dict, or let Pydantic extract from methods if we used getter.
        # But since we return the ORM object, we can monkey-patch or use a wrapper.
        # The schema uses aliases.
        # schema.TrustLine: equivalent_code, used, available.
        
        trustline.equivalent_code = trustline.equivalent.code
        trustline.from_pid = trustline.from_participant.pid
        trustline.to_pid = trustline.to_participant.pid
        trustline.from_display_name = trustline.from_participant.display_name
        trustline.to_display_name = trustline.to_participant.display_name
        trustline.used = used
        trustline.available = trustline.limit - used
        
        return trustline

    async def _get_used_amount(self, trustline: TrustLine) -> Decimal:
        # A CLOSED line is history: the debt on this pair belongs to whatever incarnation is
        # live now, not to it.  Reporting the successor's debt as a closed line's `used`
        # would show an operator a foreign amount -- and, with `available = limit - used`,
        # a negative capacity on a line that no longer exists.  Closing requires zero debt
        # (protocol §5.3), so a closed line's own `used` is zero by construction.
        if str(getattr(trustline, "status", "")) == "closed":
            return Decimal("0")

        # used = debt where debtor is 'to' and creditor is 'from'
        stmt = select(Debt.amount).where(
            and_(
                Debt.debtor_id == trustline.to_participant_id,
                Debt.creditor_id == trustline.from_participant_id,
                Debt.equivalent_id == trustline.equivalent_id
            )
        )
        result = await self.session.execute(stmt)
        amount = result.scalar_one_or_none()
        return amount if amount is not None else Decimal('0')

    async def _get_reverse_used_amount(self, trustline: TrustLine) -> Decimal:
        # Same reasoning as `_get_used_amount`: a closed incarnation must not display the
        # live successor's debt.
        if str(getattr(trustline, "status", "")) == "closed":
            return Decimal("0")

        # Reverse debt: debtor is 'from' and creditor is 'to'
        stmt = select(Debt.amount).where(
            and_(
                Debt.debtor_id == trustline.from_participant_id,
                Debt.creditor_id == trustline.to_participant_id,
                Debt.equivalent_id == trustline.equivalent_id,
            )
        )
        result = await self.session.execute(stmt)
        amount = result.scalar_one_or_none()
        return amount if amount is not None else Decimal('0')
