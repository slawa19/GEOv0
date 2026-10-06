from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.balance.service import BalanceService
from app.schemas.participant import ParticipantCreateRequest
from app.schemas.participant import ParticipantEquivalentStats, ParticipantStats, ParticipantUpdateRequest
from app.utils.money import to_money_str
from app.core.auth.crypto import get_pid_from_public_key, verify_signature
from app.core.auth.canonical import canonical_json
from app.utils.exceptions import ConflictException, NotFoundException, BadRequestException, InvalidSignatureException

class ParticipantService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_participant(self, participant_in: ParticipantCreateRequest) -> Participant:
        # 1. Check if public key is valid and derive PID
        try:
            pid = get_pid_from_public_key(participant_in.public_key)
        except Exception:
            raise BadRequestException("Invalid public key")

        # 2. Check if participant already exists (by derived PID or by public_key)
        result = await self.db.execute(
            select(Participant).where(
                or_(
                    Participant.pid == pid,
                    Participant.public_key == participant_in.public_key,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise ConflictException("Participant already exists")

        # 3. Verify signature (proof-of-possession + binding of key to identity fields)
        payload: dict = {
            "display_name": participant_in.display_name,
            "type": participant_in.type,
            "public_key": participant_in.public_key,
        }
        if participant_in.profile is not None:
            payload["profile"] = participant_in.profile.model_dump(exclude_unset=True)

        message = canonical_json(payload)
        try:
            verify_signature(participant_in.public_key, message, participant_in.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        # 4. Create participant
        # 2026-08-22 / p009_t905 (`F-009-6`): read the server-generated values back INSIDE
        # the transaction, so a readback failure undoes the mutation instead of reporting
        # a mutation that already happened as failed. See `RT-009-5`.
        try:
            # The flush is INSIDE the handler on purpose.  Moving the readback before the
            # commit also moves where the uniqueness violation surfaces: the pre-check
            # above cannot see a competitor that inserts between it and this write, and
            # that race is precisely what this handler answers.  Outside the handler it
            # would become an unhandled 500 on the path whose job is to report conflicts.
            participant = await self.insert_participant(
                pid=pid, display_name=participant_in.display_name, public_key=participant_in.public_key,
                type=participant_in.type,
                profile=participant_in.profile.model_dump(exclude_unset=True) if participant_in.profile is not None else None)
            await self.db.refresh(participant)
            await self.db.commit()
        except IntegrityError:
            # Covers race conditions against unique constraints (pid/public_key).
            await self.db.rollback()
            raise ConflictException("Participant already exists")
        return participant

    async def insert_participant(self, *, pid: str, display_name: str, public_key: str, type: str,
                                 profile: dict | None, flush: bool = True) -> Participant:
        """The one insert of a participant: ACTIVE, staged and flushed in the caller's transaction, never committed.

        030 S3b (F-030-19, `T3000` item 3): the public registration calls it after its key and signature checks; the
        trusted simulator (seeder, inject) with its pseudo key. Another status is `set_status`, after the lines."""

        participant = Participant(pid=pid, display_name=display_name, public_key=public_key, type=type,
                                  profile=profile, status="active", verification_level=0)
        self.db.add(participant)
        if flush:
            await self.db.flush()
        return participant

    async def set_status(self, pid: str, status: str) -> tuple[Participant, str]:
        """Set the status in the caller's transaction, flushed, never committed; returns the row and the old status.

        028 `F-028-28` (owner В-1): the row `FOR UPDATE` is the freeze's whole lock - a money writer holds its
        participants `FOR SHARE`, so the freeze waits for one in flight, and one arriving later waits for this commit
        and reads the new status. Moved from the admin handler by 030 S3b; the simulator's freezes call it too."""

        participant = (await self.db.execute(select(Participant).where(Participant.pid == pid).with_for_update()
                                             .execution_options(populate_existing=True))).scalar_one_or_none()
        if participant is None:
            raise NotFoundException(f"Participant {pid} not found")
        before = participant.status
        participant.status = status
        await self.db.flush()
        return participant, before

    async def get_participant(self, pid: str) -> Participant:
        result = await self.db.execute(select(Participant).where(Participant.pid == pid))
        participant = result.scalar_one_or_none()
        if not participant:
            raise NotFoundException(f"Participant {pid} not found")

        incoming = await self._trust_by_equivalent(participant.id)
        participant.public_stats = {
            "total_incoming_trust": [
                {"equivalent": code, "amount": to_money_str(sides["in"], precision)}
                for code, (precision, sides) in sorted(incoming.items())
                if sides["in_lines"]
            ],
            "member_since": participant.created_at,
        }
        return participant

    async def _trust_by_equivalent(self, participant_id) -> dict[str, tuple[int, dict]]:
        """Active trust to and from the participant, summed WITHIN each equivalent (028 F-028-36).

        Owner В-3: equivalents are independent, so a sum never crosses the equivalent boundary.
        """

        line_in = TrustLine.to_participant_id == participant_id
        rows = (
            await self.db.execute(
                select(
                    Equivalent.code,
                    Equivalent.precision,
                    func.coalesce(func.sum(TrustLine.limit).filter(line_in), 0),
                    func.count().filter(line_in),
                    func.coalesce(func.sum(TrustLine.limit).filter(~line_in), 0),
                )
                .join(Equivalent, Equivalent.id == TrustLine.equivalent_id)
                .where(
                    TrustLine.status == "active",
                    or_(line_in, TrustLine.from_participant_id == participant_id),
                )
                .group_by(Equivalent.code, Equivalent.precision)
            )
        ).all()
        return {
            code: (int(precision), {"in": Decimal(incoming), "in_lines": int(n_in), "out": Decimal(outgoing)})
            for code, precision, incoming, n_in, outgoing in rows
        }

    async def get_participant_stats(self, participant_id) -> ParticipantStats:
        trust = await self._trust_by_equivalent(participant_id)
        balance = {row.code: row for row in (await BalanceService(self.db).get_summary(participant_id)).equivalents}

        missing = set(balance) - set(trust)
        precision_by_code = {code: precision for code, (precision, _sides) in trust.items()}
        if missing:
            rows = await self.db.execute(
                select(Equivalent.code, Equivalent.precision).where(Equivalent.code.in_(missing))
            )
            precision_by_code.update({code: int(precision) for code, precision in rows.all()})

        per_equivalent = []
        for code in sorted(set(trust) | set(balance)):
            precision = precision_by_code[code]
            sides = trust.get(code, (precision, {"in": Decimal(0), "out": Decimal(0)}))[1]
            zero = to_money_str(Decimal(0), precision)
            row = balance.get(code)
            per_equivalent.append(
                ParticipantEquivalentStats(
                    equivalent=code,
                    total_incoming_trust=to_money_str(sides["in"], precision),
                    total_outgoing_trust=to_money_str(sides["out"], precision),
                    # BalanceService already renders these with `to_money_str` at this precision.
                    total_debt=row.total_debt if row else zero,
                    total_credit=row.total_credit if row else zero,
                    net_balance=row.net_balance if row else zero,
                )
            )
        return ParticipantStats(per_equivalent=per_equivalent)

    async def update_participant(self, participant_id, data: ParticipantUpdateRequest) -> Participant:
        participant = await self.db.get(Participant, participant_id)
        if not participant:
            raise NotFoundException("Participant not found")

        signed_payload: dict = {}
        if data.display_name is not None:
            signed_payload["display_name"] = data.display_name
        if data.profile is not None:
            signed_payload["profile"] = data.profile.model_dump(exclude_unset=True)

        if not signed_payload:
            raise BadRequestException("No changes provided")

        # `canonical_json` outside the try, as in `create_participant` above: `profile` is
        # free-form (`extra="allow"`), so a JSON number with a fraction arrives as `float`,
        # which `canonical_json` refuses by design - that refusal is a 400 about the payload,
        # not a signature failure, and inside the try it was relabelled "Invalid signature".
        message = canonical_json(signed_payload)
        try:
            verify_signature(participant.public_key, message, data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        if data.display_name is not None:
            participant.display_name = data.display_name

        if data.profile is not None:
            current_profile = dict(participant.profile) if participant.profile else {}
            current_profile.update(data.profile.model_dump(exclude_unset=True))
            participant.profile = current_profile

        # See the note in `register`: readback before commit.
        await self.db.flush()
        await self.db.refresh(participant)
        await self.db.commit()
        return participant

    async def list_participants(
        self, 
        query: Optional[str] = None, 
        type_filter: Optional[str] = None, 
        limit: int = 20, 
        offset: int = 0
    ) -> List[Participant]:
        stmt = select(Participant).order_by(Participant.id.asc())
        
        if query:
            stmt = stmt.where(or_(
                Participant.display_name.ilike(f"%{query}%"),
                Participant.pid.ilike(f"%{query}%")
            ))
        
        if type_filter:
            stmt = stmt.where(Participant.type == type_filter)
            
        stmt = stmt.limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())