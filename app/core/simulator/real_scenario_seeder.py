from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
    scenario_default_equivalent,
)
from app.core.trustlines.service import InitialTrustLine, TrustLineService
from app.utils.exceptions import BadRequestException, ConflictException
from app.utils.validation import (
    AMOUNT_PRECISION_EXCEEDED,
    is_storable_money,
    require_money_step,
    validate_equivalent_code,
    validate_trustline_policy,
)


# Programme 024, F-024-4b (SIM-02). A run acts for its participants without signatures (the
# seeder's trustlines, the tick's and Interact Mode's `create_payment_internal`), so it may take
# over an EXISTING participant only if the simulator itself would have created that row: its
# `public_key` is the simulator's pseudo key `sha256(pid)`. Such a row is left over from an earlier
# run of the same scenario, and adopting it keeps "run the scenario again" working. Any other key
# belongs to a real participant, and the run is refused instead of acting in its name.
SIMULATOR_PID_TAKEN = "SIMULATOR_PID_TAKEN"


def simulated_public_key(pid: str) -> str:
    """The pseudo public key the simulator gives a participant it creates. The one owner of the rule."""

    return hashlib.sha256(pid.encode("utf-8")).hexdigest()


class SimulatorPidTakenError(ConflictException):
    """A scenario names a pid that exists and was not created by the simulator."""

    def __init__(self, pid: str) -> None:
        super().__init__(
            f"{SIMULATOR_PID_TAKEN}: participant {pid!r} exists and was not created by the "
            "simulator; a run cannot act for it",
            details={"code": SIMULATOR_PID_TAKEN, "pid": pid},
        )
        self.pid = pid


SCENARIO_TRUSTLINE_REFUSED = "SCENARIO_TRUSTLINE_REFUSED"


class ScenarioTrustLineRefused(ConflictException):
    """028 `F-028-3`/`F-028-24`: a scenario line the trust-line doors would refuse - its policy breaks the policy
    grammar, or its limit is finer than the equivalent's step. Seeding stops naming the line; it is never skipped
    (the policy decides who mediates) and never rounded (owner В-4)."""

    def __init__(self, line: str, reason: str, message: str) -> None:
        super().__init__(f"{SCENARIO_TRUSTLINE_REFUSED}: trust line {line}: {message}",
                         details={"code": SCENARIO_TRUSTLINE_REFUSED, "reason": reason, "line": line})
        self.line = line


def require_simulated_participant(*, pid: str, public_key: str | None) -> None:
    """Refuse (fail closed) when an existing participant is not one the simulator created."""

    if public_key != simulated_public_key(pid):
        raise SimulatorPidTakenError(pid)


class RealScenarioSeeder:
    async def load_real_participants(
        self, *, session: Any, scenario: dict[str, Any]
    ) -> list[tuple[uuid.UUID, str]]:
        pids = [
            str(p.get("id") or "").strip() for p in (scenario.get("participants") or [])
        ]
        pids = [p for p in pids if p]
        if not pids:
            return []

        rows = (
            (
                await session.execute(
                    select(Participant).where(Participant.pid.in_(pids))
                )
            )
            .scalars()
            .all()
        )
        by_pid = {p.pid: p for p in rows}
        out: list[tuple[uuid.UUID, str]] = []
        for pid in sorted(pids):
            rec = by_pid.get(pid)
            if rec is None:
                continue
            out.append((rec.id, rec.pid))
        return out

    async def seed_scenario_into_db(self, *, session: Any, scenario: dict[str, Any]) -> None:
        # Perimeter first (F-024-4b): refuse before anything of this scenario is staged, so a
        # refusal leaves no equivalent, participant or trustline behind on any caller's session.
        scenario_pids = sorted(
            {str(p.get("id") or "").strip() for p in (scenario.get("participants") or [])} - {""}
        )
        if scenario_pids:
            taken = (
                await session.execute(
                    select(Participant.pid, Participant.public_key).where(
                        Participant.pid.in_(scenario_pids)
                    )
                )
            ).all()
            for pid, public_key in sorted(taken):
                require_simulated_participant(pid=pid, public_key=public_key)

        # Equivalents
        # Scenarios may omit the top-level 'equivalents' list (schema doesn't require it).
        # Derive equivalent codes from:
        # - scenario.equivalents[]
        # - optional MVP shorthand: scenario.equivalent
        # - trustlines[].equivalent (and fall back to scenario.equivalent)
        eq_set: set[str] = set(
            str(x).strip().upper() for x in (scenario.get("equivalents") or [])
        )
        eq_set.discard("")

        default_eq = scenario_default_equivalent(scenario)
        if default_eq:
            eq_set.add(default_eq)

        for tl in (scenario.get("trustlines") or []):
            eq = effective_equivalent(scenario, tl)
            if eq:
                eq_set.add(str(eq).strip().upper())

        eq_codes = sorted(eq_set)

        # Scenario JSON is intentionally broader than the persisted Equivalent
        # catalog. Reject noncanonical codes before any ORM objects are staged.
        for code in eq_codes:
            validate_equivalent_code(code)

        if eq_codes:
            existing_eq = (
                (
                    await session.execute(
                        select(Equivalent).where(Equivalent.code.in_(eq_codes))
                    )
                )
                .scalars()
                .all()
            )
            have = {e.code for e in existing_eq}
            for code in eq_codes:
                if code in have:
                    continue
                session.add(Equivalent(code=code, is_active=True, metadata_={}))

        # Participants
        participants = scenario.get("participants") or []
        pids = [str(p.get("id") or "").strip() for p in participants]
        pids = [p for p in pids if p]
        if pids:
            existing_p = (
                (
                    await session.execute(
                        select(Participant).where(Participant.pid.in_(pids))
                    )
                )
                .scalars()
                .all()
            )
            have_p = {p.pid for p in existing_p}
            for p in participants:
                pid = str(p.get("id") or "").strip()
                if not pid or pid in have_p:
                    continue
                name = str(p.get("name") or pid)
                p_type = str(p.get("type") or "person").strip() or "person"
                status = str(p.get("status") or "active").strip().lower()
                if status == "frozen":
                    status = "suspended"
                elif status == "banned":
                    status = "deleted"
                elif status not in {"active", "suspended", "left", "deleted"}:
                    status = "active"
                public_key = simulated_public_key(pid)
                session.add(
                    Participant(
                        pid=pid,
                        display_name=name,
                        public_key=public_key,
                        type=(
                            p_type if p_type in {"person", "business", "hub"} else "person"
                        ),
                        status=status,
                        profile={},
                    )
                )

        # NOTE: app.db.session.AsyncSessionLocal has autoflush=False.
        # We must flush pending inserts before querying IDs for trustlines.
        await session.flush()

        # Trustlines
        trustlines = scenario.get("trustlines") or []
        if trustlines and eq_codes and pids:
            default_policy = {
                "auto_clearing": True,
                "can_be_intermediate": True,
                "max_hop_usage": None,
                "daily_limit": None,
                "blocked_participants": [],
            }

            # `FOR SHARE` with the precision re-read (028 `F-028-25`): the step checked below cannot be lowered by an
            # admin PATCH before this seeding commits.
            eq_rows = (
                (
                    await session.execute(
                        select(Equivalent).where(Equivalent.code.in_(eq_codes)).order_by(Equivalent.id)
                        .with_for_update(read=True).execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            eq_by_code = {e.code: e for e in eq_rows}

            p_rows = (
                (
                    await session.execute(
                        select(Participant).where(Participant.pid.in_(pids))
                    )
                )
                .scalars()
                .all()
            )
            p_by_pid = {p.pid: p for p in p_rows}

            initial: list[InitialTrustLine] = []
            for tl in trustlines:
                eq = str(effective_equivalent(scenario, tl) or "").strip().upper()
                if not eq or eq not in eq_by_code:
                    continue
                from_pid = str(tl.get("from") or "").strip()
                to_pid = str(tl.get("to") or "").strip()
                if not from_pid or not to_pid:
                    continue
                p_from = p_by_pid.get(from_pid)
                p_to = p_by_pid.get(to_pid)
                if p_from is None or p_to is None:
                    continue

                raw_limit = tl.get("limit")
                try:
                    limit = Decimal(str(raw_limit))
                except (InvalidOperation, ValueError):
                    continue
                if limit < 0:
                    continue
                # Storage-capacity door (012 / F-012-1).  A scenario limit that does not fit
                # `Numeric(20, 8)` would be silently rounded on write, or abort the whole
                # seeding transaction with `numeric field overflow`.  Skipping the single
                # trustline keeps the blast radius of a bad config entry where the rest of
                # this loop already puts it -- one edge missing, not a scenario that will not
                # load -- and matches how every other malformed field here is handled.
                if not is_storable_money(limit):
                    continue

                line = f"{from_pid}->{to_pid} {eq}"
                status = str(tl.get("status") or "active").strip().lower()
                if status == "frozen":  # 028 `F-028-29` (owner В-2): no such line status; freeze the participant
                    raise ScenarioTrustLineRefused(line, "trust_line_status_frozen",
                                                   "a trust line is active or closed; a freeze is the participant's")
                if status not in {"active", "closed"}:
                    status = "active"

                policy = tl.get("policy")
                if not isinstance(policy, dict):
                    policy = default_policy
                try:
                    validate_trustline_policy(policy)
                except BadRequestException as exc:
                    raise ScenarioTrustLineRefused(line, "invalid_policy", exc.message) from exc
                try:
                    require_money_step(limit, precision=eq_by_code[eq].precision, equivalent=eq, field="limit")
                except BadRequestException as exc:
                    raise ScenarioTrustLineRefused(line, AMOUNT_PRECISION_EXCEEDED, exc.message) from exc

                initial.append(
                    InitialTrustLine(
                        from_participant=p_from,
                        to_participant=p_to,
                        equivalent=eq_by_code[eq],
                        limit=limit,
                        status=status,
                        policy=policy,
                    )
                )

            # Programme 021, stage 1: the lines go through `TrustLineService`'s narrow import of an initial
            # state - one audit row per imported line, one checkpoint pair per touched equivalent for this
            # whole seeding transaction. The import skips a line whose triple already has a LIVE row (only a
            # live row occupies the triple since migration 019), exactly as the per-line lookup here did.
            # `finish()` flushes and audits; the CALLER commits, and on any failure rolls back.
            service = TrustLineService(session)
            batch = service.begin_internal_batch()
            await service.import_initial_trustlines(batch, initial)
            await batch.finish()
