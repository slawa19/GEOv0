from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
    scenario_default_equivalent,
)
from app.utils.validation import is_storable_money, validate_equivalent_code


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
                public_key = hashlib.sha256(pid.encode("utf-8")).hexdigest()
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

            eq_rows = (
                (
                    await session.execute(
                        select(Equivalent).where(Equivalent.code.in_(eq_codes))
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

                # Only a LIVE row occupies the triple: since migration 019 a closed
                # incarnation may coexist with it, so an unfiltered lookup can return
                # several rows and raise MultipleResultsFound.
                existing = (
                    await session.execute(
                        select(TrustLine).where(
                            TrustLine.from_participant_id == p_from.id,
                            TrustLine.to_participant_id == p_to.id,
                            TrustLine.equivalent_id == eq_by_code[eq].id,
                            TrustLine.status != "closed",
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                status = str(tl.get("status") or "active").strip().lower()
                if status not in {"active", "frozen", "closed"}:
                    status = "active"

                policy = tl.get("policy")
                if not isinstance(policy, dict):
                    policy = default_policy

                session.add(
                    TrustLine(
                        from_participant_id=p_from.id,
                        to_participant_id=p_to.id,
                        equivalent_id=eq_by_code[eq].id,
                        limit=limit,
                        status=status,
                        policy=policy,
                    )
                )
