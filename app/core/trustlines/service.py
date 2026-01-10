from uuid import UUID
from decimal import Decimal
from typing import List, Optional, Literal
from sqlalchemy import select, and_, or_
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
from app.utils.validation import validate_equivalent_code, validate_trustline_policy
from app.core.integrity import compute_integrity_checkpoint_for_equivalent

class TrustLineService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, from_participant_id: UUID, data: TrustLineCreateRequest) -> TrustLine:
        if not isinstance(getattr(data, "signature", None), str) or not data.signature:
            raise InvalidSignatureException("Missing signature")

        from_participant = await self.session.get(Participant, from_participant_id)
        if not from_participant:
            raise NotFoundException("Sender not found")

        # Signature validation (proof-of-possession + binding of request fields).
        signed_payload: dict = {
            "to": data.to,
            "equivalent": data.equivalent,
            "limit": str(data.limit),
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

        checkpoint_before = None
        try:
            checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=equivalent.id,
            )
        except Exception:
            checkpoint_before = None

        # Check for duplicate active trustline
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
            limit=data.limit,
            policy=data.policy or {},
            status='active'
        )
        self.session.add(trustline)

        await self.session.flush()

        try:
            checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=equivalent.id,
            )
            invariants_status = checkpoint_after.invariants_status or {}
            passed = bool(invariants_status.get("passed", True))
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
        except Exception:
            pass

        await self.session.commit()
        await self.session.refresh(trustline)
        
        # Hydrate extra fields for response
        return await self._hydrate_trustline(trustline)

    async def update(self, trustline_id: UUID, user_id: UUID, data: TrustLineUpdateRequest) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to update this trustline")

        if not isinstance(getattr(data, "signature", None), str) or not data.signature:
            raise InvalidSignatureException("Missing signature")

        user = await self.session.get(Participant, user_id)
        if not user:
            raise NotFoundException("Sender not found")

        signed_payload: dict = {"id": str(trustline_id)}
        if data.limit is not None:
            signed_payload["limit"] = str(data.limit)
        if data.policy is not None:
            signed_payload["policy"] = data.policy

        try:
            verify_signature(user.public_key, canonical_json(signed_payload), data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        checkpoint_before = None
        try:
            checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=trustline.equivalent_id,
            )
        except Exception:
            checkpoint_before = None

        if data.limit is not None:
            trustline.limit = data.limit
        
        if data.policy is not None:
            validate_trustline_policy(data.policy)
            # Merge or replace policy? Usually merge or replace. Assuming replace for now or merge top level.
            # Schema says optional dict. Let's update existing dict.
            current_policy = dict(trustline.policy) if trustline.policy else {}
            current_policy.update(data.policy)
            trustline.policy = current_policy

        await self.session.flush()

        try:
            checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=trustline.equivalent_id,
            )
            invariants_status = checkpoint_after.invariants_status or {}
            passed = bool(invariants_status.get("passed", True))
            before_sum = checkpoint_before.checksum if checkpoint_before else ""
            after_sum = checkpoint_after.checksum or before_sum

            # Resolve PIDs for readability.
            from_pid = (
                await self.session.execute(select(Participant.pid).where(Participant.id == trustline.from_participant_id))
            ).scalar_one_or_none()
            to_pid = (
                await self.session.execute(select(Participant.pid).where(Participant.id == trustline.to_participant_id))
            ).scalar_one_or_none()
            eq_code = (
                await self.session.execute(select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id))
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
        except Exception:
            pass

        await self.session.commit()
        await self.session.refresh(trustline)
        return await self._hydrate_trustline(trustline)

    async def close(self, trustline_id: UUID, user_id: UUID, data: TrustLineCloseRequest) -> None:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to close this trustline")

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

        checkpoint_before = None
        try:
            checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=trustline.equivalent_id,
            )
        except Exception:
            checkpoint_before = None

        trustline.status = 'closed'

        await self.session.flush()

        try:
            checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=trustline.equivalent_id,
            )
            invariants_status = checkpoint_after.invariants_status or {}
            passed = bool(invariants_status.get("passed", True))
            before_sum = checkpoint_before.checksum if checkpoint_before else ""
            after_sum = checkpoint_after.checksum or before_sum

            from_pid = (
                await self.session.execute(select(Participant.pid).where(Participant.id == trustline.from_participant_id))
            ).scalar_one_or_none()
            to_pid = (
                await self.session.execute(select(Participant.pid).where(Participant.id == trustline.to_participant_id))
            ).scalar_one_or_none()
            eq_code = (
                await self.session.execute(select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id))
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
        except Exception:
            pass

        await self.session.commit()

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

        query = query.order_by(TrustLine.created_at.desc())

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

        query = query.order_by(TrustLine.created_at.desc())

        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        return [await self._hydrate_trustline(tl) for tl in trustlines]

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