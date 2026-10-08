from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.simulator.cache_invalidator import (
    invalidate_caches_after_inject as _invalidate_caches_after_inject,
)
from app.core.money_boundary import MoneyBoundary
from app.core.participants.service import ParticipantService
from app.core.trustlines.service import TrustLineService
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import InjectResult, RunRecord
from app.core.simulator.real_scenario_seeder import (
    SimulatorPidTakenError,
    require_simulated_participant,
    scenario_participant_status,
    simulated_public_key,
)
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCreateRequest
from app.schemas.simulator import (
    TopologyChangedEdgeRef,
    TopologyChangedNodeRef,
    TopologyChangedPayload,
)
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.utils.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.utils.validation import money_storability_violation

#: `skipped_reasons` key of an effect refused over a participant that is not active (028 `F-028-28`).
PARTICIPANT_SUSPENDED = MoneyBoundary.PARTICIPANT_SUSPENDED_REASON

#: `skipped_reasons` key prefix of an effect whose op this executor does not have, followed by `:<op>` (031).
UNSUPPORTED_OP_REASON = "unsupported_op"

# How many effects of one inject event are processed. Shared by staging and by the lock-set
# helper below, so the helper never names fewer equivalents than staging can reach.
_MAX_INJECT_EFFECTS = 500

#: The policy of a trust line the simulator creates (inject and Interact create): the model's default
#: (`app/db/models/trustline.py`), spelled out because the trust-line service stores `{}` for "no policy".
SIMULATED_TRUSTLINE_POLICY = {
    "auto_clearing": True,
    "can_be_intermediate": True,
    "max_hop_usage": None,
    "daily_limit": None,
    "blocked_participants": [],
}

# Programme 021, stage 2: what the request model needs in its required `signature` field on the internal
# path. `require_signature=False` is what makes the service skip the check - never this value, which the service
# does not read on that path (the drift engine's convention).
_UNSIGNED = "__internal__"

# The trust-line service's REFUSALS: each is raised before the service stages anything or touches the batch, so
# an inject effect it refuses is skipped like any other unusable effect.
_TRUST_LINE_REFUSALS = (BadRequestException, ConflictException, ForbiddenException, NotFoundException)


class InjectTrustLineWriteFailed(Exception):
    """A trust-line write of an inject event failed for a reason other than a refusal.

    Programme 021, stage 2 (spec, "Решения" item 7). Raised instead of the effect handler's "skipped", so the
    failure reaches the owner of the transaction (`RealRunnerImpl._apply_due_scenario_events`), which rolls the
    whole event back - a checkpoint the checker could not compute must not leave the event's earlier writes for
    the commit. The cause is chained.
    """


class InjectOwnerLockSetTooNarrow(Exception):
    """Staging reached a write in an equivalent whose owner lock the unit of work does not hold.

    Programme 015, phase B step 3. The owner acquires the owner locks before staging, and the
    equivalent set of `freeze_participant` is only known once its incident trustlines are read.
    Raised BEFORE the write is staged, so the owner can roll back, add `missing_equivalent_ids`
    and restart the unit of work with the wider set.
    """

    def __init__(self, missing_equivalent_ids: frozenset[uuid.UUID]) -> None:
        self.missing_equivalent_ids = frozenset(missing_equivalent_ids)
        super().__init__(
            "inject staging needs owner locks it does not hold: "
            + ", ".join(sorted(str(i) for i in self.missing_equivalent_ids))
        )


@dataclass(frozen=True)
class StagedInjectEvent:
    """What one staged, not yet committed, inject event leaves for its owner.

    Nothing here has been published: caches, the scenario, `run`, SSE and artifacts are untouched
    until the owner confirms the commit and calls `publish_committed_inject`. `pid_additions` are
    the pid -> participant id pairs staging learned (new participants, resolved sponsors and
    trustline ends); the owner merges them into its own map only after the commit, because a
    rolled-back participant id must not survive into the next unit of work.
    """

    affected_equivalents: set[str] = field(default_factory=set)
    new_participants: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    new_participants_scenario: list[dict[str, Any]] = field(default_factory=list)
    new_trustlines_scenario: list[dict[str, Any]] = field(default_factory=list)
    frozen_participant_pids: list[str] = field(default_factory=list)
    pid_additions: dict[str, uuid.UUID] = field(default_factory=dict)
    applied: int = 0
    skipped: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)


def inject_event_equivalent_codes(
    *, scenario: Mapping[str, Any], event: Mapping[str, Any] | None
) -> set[str]:
    """Equivalent codes an inject event names by itself, before touching the database.

    Programme 015, phase B step 3: the owner locks these together with the run's equivalents
    before staging. `create_trustline` names one equivalent; the initial
    trustlines of `add_participant` name theirs, falling back to the scenario default exactly as
    staging does. `freeze_participant` names none - its trustlines are discovered while staging,
    which raises `InjectOwnerLockSetTooNarrow` if they fall outside the held set.
    """

    codes: set[str] = set()
    effects = (event or {}).get("effects")
    if not isinstance(effects, list):
        return codes
    for eff in effects[:_MAX_INJECT_EFFECTS]:
        if not isinstance(eff, dict):
            continue
        op = str(eff.get("op") or "").strip()
        if op == "create_trustline":
            eq = effective_equivalent(scenario=scenario, payload=eff)
            if eq:
                codes.add(eq)
        elif op == "add_participant":
            initial_tls = eff.get("initial_trustlines")
            if not isinstance(initial_tls, list):
                continue
            for itl in initial_tls:
                if not isinstance(itl, dict):
                    continue
                eq = effective_equivalent(scenario=scenario, payload=itl)
                if eq:
                    codes.add(eq)
    return codes


def inject_event_freeze_participant_pids(*, event: Mapping[str, Any] | None) -> set[str]:
    """The targets of the event's `freeze_participant` effects: the owner takes their rows `FOR UPDATE` before the
    event's first other lock (028 `F-028-28`), never upgrading a shared lock mid-event. Since `F-028-29` a freeze
    writes no trust line, so `freeze_trustlines` no longer matters. Mirrors staging: an empty pid is skipped."""

    effects = (event or {}).get("effects")
    return {
        pid
        for eff in (effects if isinstance(effects, list) else [])[:_MAX_INJECT_EFFECTS]
        if isinstance(eff, dict) and str(eff.get("op") or "").strip() == "freeze_participant"
        for pid in (str(eff.get("participant_id") or "").strip(),)
        if pid
    }


def inject_event_participant_pids(*, event: Mapping[str, Any] | None) -> set[str]:
    """Every participant an effect of the event names - freezes, new lines (both ends) and the sponsors of a
    new participant's lines: the owner locks their rows first, in `participants.id` order (028 `F-028-28`)."""

    pids = inject_event_freeze_participant_pids(event=event)
    effects = (event or {}).get("effects")
    for eff in (effects if isinstance(effects, list) else [])[:_MAX_INJECT_EFFECTS]:
        if not isinstance(eff, dict):
            continue
        op = str(eff.get("op") or "").strip()
        named = [eff.get("from"), eff.get("to")] if op == "create_trustline" else []
        if op == "add_participant" and isinstance(eff.get("initial_trustlines"), list):
            named = [itl.get("sponsor") for itl in eff["initial_trustlines"] if isinstance(itl, dict)]
        pids |= {str(pid).strip() for pid in named if str(pid or "").strip()}
    return pids


def invalidate_caches_after_inject(
    *,
    logger: logging.Logger,
    run: RunRecord,
    scenario: dict[str, Any],
    affected_equivalents: set[str],
    new_participants: list[tuple[uuid.UUID, str]],
    new_participants_scenario: list[dict[str, Any]],
    new_trustlines_scenario: list[dict[str, Any]],
    frozen_pids: list[str],
) -> None:
    """Invalidate in-memory caches after a successful inject commit.

    Uses Variant A (mutate shared dicts in-place) so that the running tick
    picks up the topology changes immediately.

    Best-effort: failures here are logged but do not crash the tick.
    """

    _invalidate_caches_after_inject(
        logger=logger,
        run=run,
        scenario=scenario,
        affected_equivalents=affected_equivalents,
        new_participants=new_participants,
        new_participants_scenario=new_participants_scenario,
        new_trustlines_scenario=new_trustlines_scenario,
        frozen_pids=frozen_pids,
    )


def broadcast_topology_changed(
    *,
    sse: SseBroadcast,
    utc_now,
    logger: logging.Logger,
    run_id: str,
    run: RunRecord,
    affected_equivalents: set[str],
    new_participants_scenario: list[dict[str, Any]],
    new_trustlines_scenario: list[dict[str, Any]],
    frozen_pids: list[str],
    frozen_edges: list[dict[str, str]],
) -> None:
    """Broadcast SSE topology.changed events per affected equivalent."""

    try:
        if not affected_equivalents:
            return

        emitter = SseEventEmitter(sse=sse, utc_now=utc_now, logger=logger)

        added_nodes = [
            TopologyChangedNodeRef(
                pid=str(p.get("id") or ""),
                name=str(p.get("name") or "") or None,
                type=str(p.get("type") or "") or None,
            )
            for p in new_participants_scenario
            if str(p.get("id") or "").strip()
        ]

        frozen_nodes = [pid for pid in frozen_pids if pid.strip()]

        added_edges_by_eq: dict[str, list[TopologyChangedEdgeRef]] = {}
        for tl in new_trustlines_scenario:
            eq = str(tl.get("equivalent") or "").strip().upper()
            if not eq:
                continue
            ref = TopologyChangedEdgeRef(
                from_pid=str(tl.get("from") or ""),
                to_pid=str(tl.get("to") or ""),
                equivalent_code=eq,
                limit=str(tl.get("limit") or "") or None,
            )
            added_edges_by_eq.setdefault(eq, []).append(ref)

        frozen_edges_by_eq: dict[str, list[TopologyChangedEdgeRef]] = {}
        for fe in frozen_edges:
            eq = str(fe.get("equivalent_code") or "").strip().upper()
            if not eq:
                continue
            ref = TopologyChangedEdgeRef(
                from_pid=str(fe.get("from_pid") or ""),
                to_pid=str(fe.get("to_pid") or ""),
                equivalent_code=eq,
            )
            frozen_edges_by_eq.setdefault(eq, []).append(ref)

        for eq in affected_equivalents:
            eq_upper = eq.strip().upper()
            payload = TopologyChangedPayload(
                added_nodes=added_nodes,
                removed_nodes=[],
                frozen_nodes=frozen_nodes,
                added_edges=added_edges_by_eq.get(eq_upper, []),
                removed_edges=[],
                frozen_edges=frozen_edges_by_eq.get(eq_upper, []),
            )

            if (
                not payload.added_nodes
                and not payload.removed_nodes
                and not payload.frozen_nodes
                and not payload.added_edges
                and not payload.removed_edges
                and not payload.frozen_edges
            ):
                continue

            emitter.emit_topology_changed(
                run_id=run_id,
                run=run,
                equivalent=eq_upper,
                payload=payload,
            )
            logger.info(
                "simulator.real.inject.topology_changed eq=%s added_nodes=%d removed_nodes=%d added_edges=%d removed_edges=%d",
                eq_upper,
                len(payload.added_nodes),
                len(payload.removed_nodes),
                len(payload.added_edges),
                len(payload.removed_edges),
            )

    except Exception:
        logger.warning(
            "simulator.real.inject.topology_changed_broadcast_error",
            exc_info=True,
        )


class InjectExecutor:
    def __init__(
        self,
        *,
        sse: SseBroadcast,
        artifacts: ArtifactsManager,
        utc_now,
        logger: logging.Logger,
    ) -> None:
        self._sse = sse
        self._artifacts = artifacts
        self._utc_now = utc_now
        self._logger = logger

    def enqueue_inject_note(
        self,
        run_id: str,
        *,
        run: RunRecord,
        event_index: int,
        event_time_ms: int,
        description: str,
        stats: dict[str, Any] | None = None,
    ) -> None:
        """Record the outcome of one inject event as a scenario note artifact."""

        scenario_note: dict[str, Any] = {
            "event_index": int(event_index),
            "time": event_time_ms,
            "description": description,
        }
        if stats is not None:
            scenario_note["stats"] = stats
        self._artifacts.enqueue_event_artifact(
            run_id,
            {
                "type": "note",
                "ts": self._utc_now().isoformat(),
                "sim_time_ms": int(run.sim_time_ms),
                "tick_index": int(run.tick_index),
                "scenario": scenario_note,
            },
        )

    async def stage_inject_event(
        self,
        session,
        *,
        scenario: dict[str, Any],
        event: dict[str, Any] | None,
        pid_to_participant_id: Mapping[str, uuid.UUID],
        locked_equivalent_ids: Iterable[uuid.UUID],
    ) -> StagedInjectEvent:
        """Stage one inject event's reads and writes in the caller's transaction.

        Programme 015, phase B step 3. This used to be `apply_inject_event`, which committed a
        transaction it had not opened. The equivalent owner lock is a transactional advisory lock,
        so that commit released the lock the tick orchestrator had taken: the next inject event of
        the same tick wrote `debts` with no lock at all, and the payments phase read its snapshot
        without it. Ownership of the transaction now stays with the caller
        (`RealRunnerImpl._apply_due_scenario_events`), which acquires the owner locks, calls this,
        and commits or rolls back.

        Contract:
        - never calls `commit`, `rollback` or `close`;
        - never touches `run`, `scenario`, caches, SSE, artifacts or `pid_to_participant_id`
          (a local copy is used; what it learned comes back in `pid_additions`);
        - an unusable effect is still counted in `skipped`, but a database error
          (`SQLAlchemyError`) propagates, so the owner can tell a serialization failure from a
          bad scenario entry instead of committing a poisoned transaction;
        - every write that belongs to an equivalent is checked against `locked_equivalent_ids`
          first, and raises `InjectOwnerLockSetTooNarrow` before the write is staged.
        """

        locked_ids = frozenset(locked_equivalent_ids)

        def require_owner_locks(equivalent_ids: Iterable[uuid.UUID]) -> None:
            missing = frozenset(equivalent_ids) - locked_ids
            if missing:
                raise InjectOwnerLockSetTooNarrow(missing)

        # Local copy: a participant id staged here does not exist until the owner commits.
        pids: dict[str, uuid.UUID] = dict(pid_to_participant_id)
        pid_additions: dict[str, uuid.UUID] = {}

        def remember_pid(pid: str, participant_id: uuid.UUID) -> None:
            pids[pid] = participant_id
            pid_additions[pid] = participant_id

        effects = (event or {}).get("effects")
        if not isinstance(effects, list):
            effects = []

        max_edges = _MAX_INJECT_EFFECTS

        applied = 0
        skipped = 0
        skipped_reasons: dict[str, int] = {}

        # Resolve equivalents lazily.
        eq_id_by_code: dict[str, uuid.UUID] = {}
        # 012 / T1207: the same lookup now also carries `Equivalent.precision`, because the
        # trustline limits this method reports over `topology.changed` are money strings and
        # had none.
        eq_precision_by_code: dict[str, int] = {}

        # Track new entities for cache invalidation after the owner's commit.
        affected_equivalents: set[str] = set()
        new_participants_for_cache: list[tuple[uuid.UUID, str]] = []
        new_participants_for_scenario: list[dict[str, Any]] = []
        new_trustlines_for_scenario: list[dict[str, Any]] = []
        frozen_participant_pids: list[str] = []

        async def resolve_eq_id(eq_code: str) -> uuid.UUID | None:
            """Lazily resolve equivalent code → UUID (and cache its precision) from DB."""

            eq_upper = eq_code.strip().upper()
            cached = eq_id_by_code.get(eq_upper)
            if cached is not None:
                return cached
            row = (
                await session.execute(
                    select(Equivalent.id, Equivalent.precision).where(
                        Equivalent.code == eq_upper
                    )
                )
            ).one_or_none()
            if row is not None:
                eq_id_by_code[eq_upper] = row[0]
                eq_precision_by_code[eq_upper] = int(2 if row[1] is None else row[1])  # 024 T2416.1
                return row[0]
            return None

        def eq_precision(eq_code: str) -> int:
            return int(eq_precision_by_code.get(eq_code.strip().upper(), 2))

        # Programme 021, stage 2: the event's trust-line writes go through the service's internal path, on ONE
        # batch for this event - one audit row per line, one checkpoint pair per touched equivalent. `finish()`
        # runs at the end of staging; the owner commits, and rolls back on any failure.
        trust_lines = TrustLineService(session)
        batch = trust_lines.begin_internal_batch()

        async def write_trustline(
            *, from_id: uuid.UUID, to_pid: str, eq_code: str, limit: Decimal
        ) -> bool:
            """Create one active line; False if the service refuses it (the effect is then skipped)."""

            try:
                await trust_lines.execute_create(
                    batch,
                    from_id,
                    TrustLineCreateRequest(
                        to=to_pid,
                        equivalent=eq_code.strip().upper(),
                        limit=format(limit, "f"),
                        policy=dict(SIMULATED_TRUSTLINE_POLICY),
                        signature=_UNSIGNED,
                    ),
                    require_signature=False,
                    # The event's flush points stay the event's own (see `execute_create`).
                    flush=False,
                    # 028 F-028-14: bounded like the event's other lock waits; a `55P03` propagates as the
                    # runner's transient class (`SQLAlchemyError` below), and the event stays pending.
                    lock_timeout_ms=MoneyBoundary.lock_budget_ms(),
                )
            except _TRUST_LINE_REFUSALS as exc:
                self._logger.warning(
                    "simulator.real.inject.trustline_refused to=%s eq=%s reason=%s",
                    to_pid,
                    eq_code,
                    type(exc).__name__,
                )
                # 029 `F-029-13`: the service's own reason, whichever it is (028: only `participant_suspended` was).
                if reason := (exc.details or {}).get("reason"):
                    skipped_reasons[str(reason)] = skipped_reasons.get(str(reason), 0) + 1
                return False
            except SQLAlchemyError:
                raise
            except Exception as exc:
                raise InjectTrustLineWriteFailed(
                    f"inject trust-line write failed: {type(exc).__name__}: {exc}"
                ) from exc
            return True

        def unstorable_limit(limit: Decimal) -> bool:
            """029 `F-029-13`: a limit the column cannot hold is skipped, and the note says by which predicate."""
            reason = money_storability_violation(limit)
            if reason is not None:
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            return reason is not None

        async def op_add_participant(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                p_data = eff.get("participant")
                if not isinstance(p_data, dict):
                    self._logger.warning(
                        "simulator.real.inject.add_participant: missing participant dict"
                    )
                    skipped += 1
                    return False

                pid = str(p_data.get("id") or "").strip()
                if not pid:
                    skipped += 1
                    return False

                # Idempotency: skip if participant already exists. Outside the run's perimeter it
                # must be one the simulator created; a real participant's pid refuses the run
                # (programme 024, F-024-4b). Perimeter members were vetted when they entered it.
                existing_p = (
                    await session.execute(
                        select(Participant.id, Participant.public_key).where(Participant.pid == pid)
                    )
                ).one_or_none()
                if existing_p is not None:
                    if pid not in pids:
                        require_simulated_participant(pid=pid, public_key=existing_p.public_key)
                    self._logger.info(
                        "simulator.real.inject.add_participant.skip_exists pid=%s",
                        pid,
                    )
                    skipped += 1
                    return False

                name = str(p_data.get("name") or pid)
                p_type = str(p_data.get("type") or "person").strip()
                if p_type not in {"person", "business", "hub"}:
                    p_type = "person"
                status = scenario_participant_status(p_data.get("status"))  # the seeder's rule (034 S4, F-034-14)
                public_key = simulated_public_key(pid)

                # Inserted ACTIVE through the participant service (030 S3b, F-030-19): the lines below are created by
                # the trust-line service, which refuses a suspended end, so any other status is set AFTER them.
                new_p = await ParticipantService(session).insert_participant(
                    pid=pid, display_name=name, public_key=public_key, type=p_type, profile={})

                new_participants_for_cache.append((new_p.id, pid))
                new_participants_for_scenario.append(
                    {
                        "id": pid,
                        "name": name,
                        "type": p_type,
                        "status": status,
                        "groupId": str(p_data.get("groupId") or ""),
                        "behaviorProfileId": str(p_data.get("behaviorProfileId") or ""),
                    }
                )
                remember_pid(pid, new_p.id)

                # Create initial trustlines (sponsor ↔ new participant).
                initial_tls = eff.get("initial_trustlines")
                if isinstance(initial_tls, list):
                    for itl in initial_tls:
                        if not isinstance(itl, dict):
                            continue
                        sponsor_pid = str(itl.get("sponsor") or "").strip()
                        eq_code = effective_equivalent(scenario=scenario, payload=(itl or {}))
                        raw_limit = itl.get("limit")
                        direction = str(itl.get("direction") or "sponsor_credits_new").strip()

                        if not sponsor_pid or not eq_code:
                            continue
                        try:
                            tl_limit_val = Decimal(str(raw_limit))
                        except Exception:
                            continue
                        # 034 S4b: a non-finite limit (`NaN` parses, and comparing it RAISES) is a bad line like its
                        # neighbours - it goes to the storability door below, which names it (`money_finiteness`) and
                        # skips the line. Raised here, it left the effect after the participant was inserted `active`
                        # and before its declared status was set, and the event still committed.
                        if tl_limit_val.is_finite() and tl_limit_val <= 0:
                            continue
                        # Storage-capacity door (012 / F-012-1).
                        if unstorable_limit(tl_limit_val):
                            self._logger.warning(
                                "simulator.real.inject.add_participant.limit_unstorable "
                                "sponsor=%s limit=%s",
                                sponsor_pid,
                                tl_limit_val,
                            )
                            skipped += 1  # 029 `F-029-13`: a line of this effect that did not land is counted
                            continue

                        # Resolve sponsor participant ID.
                        sponsor_id = pids.get(sponsor_pid)
                        if sponsor_id is None:
                            sponsor_row = (
                                await session.execute(
                                    select(Participant.id, Participant.public_key).where(
                                        Participant.pid == sponsor_pid
                                    )
                                )
                            ).one_or_none()
                            if sponsor_row is None:
                                self._logger.warning(
                                    "simulator.real.inject.add_participant.sponsor_not_found sponsor=%s",
                                    sponsor_pid,
                                )
                                continue
                            require_simulated_participant(
                                pid=sponsor_pid, public_key=sponsor_row.public_key
                            )
                            sponsor_id = sponsor_row.id
                            remember_pid(sponsor_pid, sponsor_id)

                        eq_id = await resolve_eq_id(eq_code)
                        if eq_id is None:
                            continue
                        # Programme 015, phase B step 3: the trustline below belongs to this
                        # equivalent.
                        require_owner_locks({eq_id})

                        # Determine trustline direction.
                        if direction == "sponsor_credits_new":
                            from_id = sponsor_id
                            to_id = new_p.id
                            from_pid_str = sponsor_pid
                            to_pid_str = pid
                        else:
                            from_id = new_p.id
                            to_id = sponsor_id
                            from_pid_str = pid
                            to_pid_str = sponsor_pid

                        # Idempotency: skip if a LIVE trustline already exists.
                        # A closed incarnation does not occupy the triple (migration 019).
                        existing_tl = (
                            await session.execute(
                                select(TrustLine.id).where(
                                    TrustLine.from_participant_id == from_id,
                                    TrustLine.to_participant_id == to_id,
                                    TrustLine.equivalent_id == eq_id,
                                    TrustLine.status != "closed",
                                )
                            )
                        ).scalar_one_or_none()
                        if existing_tl is not None:
                            continue

                        if not await write_trustline(
                            from_id=from_id, to_pid=to_pid_str, eq_code=eq_code, limit=tl_limit_val
                        ):
                            skipped += 1
                            continue
                        affected_equivalents.add(eq_code)
                        new_trustlines_for_scenario.append(
                            {
                                "from": from_pid_str,
                                "to": to_pid_str,
                                "equivalent": eq_code,
                                # 012 / T1207: was `str(Decimal)`, which puts
                                # `1E-8` (and, from a scenario written as
                                # `"1e3"`, `1E+3`) into `topology.changed`.
                                "limit": to_money_str(
                                    tl_limit_val, eq_precision(eq_code)
                                ),
                                "status": "active",
                            }
                        )

                if status != "active":
                    # The row was inserted ACTIVE above, in this transaction (032 A-5: the scenario's status from it).
                    await ParticipantService(session).set_status(pid, status, from_statuses=("active",))
                applied += 1
                return True
            except (
                InjectOwnerLockSetTooNarrow,
                SQLAlchemyError,
                SimulatorPidTakenError,
                InjectTrustLineWriteFailed,
            ):
                # Programme 015, phase B step 3: the owner decides - widen the lock set, retry a
                # serialization failure, or record a database failure. Counting either as a
                # skipped entry would commit whatever the poisoned transaction still holds.
                raise
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.add_participant.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        async def op_create_trustline(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                from_pid_val = str(eff.get("from") or "").strip()
                to_pid_val = str(eff.get("to") or "").strip()
                eq_code = effective_equivalent(scenario=scenario, payload=(eff or {}))
                raw_limit = eff.get("limit")

                if not from_pid_val or not to_pid_val or not eq_code:
                    skipped += 1
                    return False

                try:
                    tl_limit_val = Decimal(str(raw_limit))
                except Exception:
                    skipped += 1
                    return False
                if tl_limit_val.is_finite() and tl_limit_val <= 0:  # non-finite: the storability door below (034 S4b)
                    skipped += 1
                    return False
                # Storage-capacity door (012 / F-012-1).
                if unstorable_limit(tl_limit_val):
                    self._logger.warning(
                        "simulator.real.inject.create_trustline.limit_unstorable "
                        "from=%s to=%s limit=%s",
                        from_pid_val,
                        to_pid_val,
                        tl_limit_val,
                    )
                    skipped += 1
                    return False

                # Resolve participant IDs.
                from_id = pids.get(from_pid_val)
                if from_id is None:
                    row = (
                        await session.execute(
                            select(Participant.id, Participant.public_key).where(
                                Participant.pid == from_pid_val
                            )
                        )
                    ).one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.from_not_found pid=%s",
                            from_pid_val,
                        )
                        skipped += 1
                        return False
                    require_simulated_participant(pid=from_pid_val, public_key=row.public_key)
                    from_id = row.id
                    remember_pid(from_pid_val, from_id)

                to_id = pids.get(to_pid_val)
                if to_id is None:
                    row = (
                        await session.execute(
                            select(Participant.id, Participant.public_key).where(
                                Participant.pid == to_pid_val
                            )
                        )
                    ).one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.to_not_found pid=%s",
                            to_pid_val,
                        )
                        skipped += 1
                        return False
                    require_simulated_participant(pid=to_pid_val, public_key=row.public_key)
                    to_id = row.id
                    remember_pid(to_pid_val, to_id)

                eq_id = await resolve_eq_id(eq_code)
                if eq_id is None:
                    skipped += 1
                    return False
                # Programme 015, phase B step 3: the trustline below belongs to this equivalent.
                require_owner_locks({eq_id})

                # Idempotency: skip if a LIVE trustline already exists.
                # A closed incarnation does not occupy the triple (migration 019).
                existing_tl = (
                    await session.execute(
                        select(TrustLine.id).where(
                            TrustLine.from_participant_id == from_id,
                            TrustLine.to_participant_id == to_id,
                            TrustLine.equivalent_id == eq_id,
                            TrustLine.status != "closed",
                        )
                    )
                ).scalar_one_or_none()
                if existing_tl is not None:
                    self._logger.info(
                        "simulator.real.inject.create_trustline.skip_exists from=%s to=%s eq=%s",
                        from_pid_val,
                        to_pid_val,
                        eq_code,
                    )
                    skipped += 1
                    return False

                if not await write_trustline(
                    from_id=from_id, to_pid=to_pid_val, eq_code=eq_code, limit=tl_limit_val
                ):
                    skipped += 1
                    return False
                affected_equivalents.add(eq_code)
                new_trustlines_for_scenario.append(
                    {
                        "from": from_pid_val,
                        "to": to_pid_val,
                        "equivalent": eq_code,
                        # 012 / T1207: see the sibling site above.
                        "limit": to_money_str(tl_limit_val, eq_precision(eq_code)),
                        "status": "active",
                    }
                )
                applied += 1
                return True
            except (
                InjectOwnerLockSetTooNarrow,
                SQLAlchemyError,
                SimulatorPidTakenError,
                InjectTrustLineWriteFailed,
            ):
                # Programme 015, phase B step 3 - see op_add_participant.
                raise
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.create_trustline.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        async def op_freeze_participant(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped

            try:
                freeze_pid = str(eff.get("participant_id") or "").strip()

                if not freeze_pid:
                    skipped += 1
                    return False

                # 028 `F-028-28`/`F-028-29`: the participant row only, `FOR UPDATE` (the owner took it first, with the
                # event's other participants), its status read by the locking statement - never from an ORM object;
                # no trust line is written (`frozen` is gone). The mutation is the participant service's, the one the
                # admin freeze calls (030 S3b), and it is SENT here: the session runs with `autoflush=False`, and every
                # later effect of the event must read `suspended`.
                p_row = (await session.execute(select(Participant.id, Participant.public_key, Participant.status).where(
                    Participant.pid == freeze_pid).with_for_update())).one_or_none()
                if p_row is None:
                    self._logger.warning(
                        "simulator.real.inject.freeze_participant.not_found pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False
                if freeze_pid not in pids:
                    # Outside the perimeter only a simulator-created row may be frozen (F-024-4b).
                    require_simulated_participant(pid=freeze_pid, public_key=p_row.public_key)

                if p_row.status == "suspended":
                    self._logger.info(
                        "simulator.real.inject.freeze_participant.already_suspended pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False
                if p_row.status != "active":
                    # 032 A-5: a freeze starts from `active` only; `left`/`deleted` is skipped like `suspended` above,
                    # where before 032 it was silently turned into `suspended`.
                    self._logger.info(
                        "simulator.real.inject.freeze_participant.not_active pid=%s status=%s",
                        freeze_pid,
                        p_row.status,
                    )
                    skipped += 1
                    return False

                await ParticipantService(session).set_status(freeze_pid, "suspended", from_statuses=("active",))

                # Invalidate only incident equivalents (best-effort).
                # Freezing a participant affects routing; avoid evicting all equivalents.
                incident_eqs: set[str] = set()
                s_tls = scenario.get("trustlines")
                if isinstance(s_tls, list):
                    for tl in s_tls:
                        if not isinstance(tl, dict):
                            continue
                        frm = str(tl.get("from") or "").strip()
                        to = str(tl.get("to") or "").strip()
                        if frm != freeze_pid and to != freeze_pid:
                            continue
                        eq = effective_equivalent(scenario=scenario, payload=(tl or {}))
                        if eq:
                            incident_eqs.add(eq)

                if incident_eqs:
                    affected_equivalents.update(incident_eqs)

                frozen_participant_pids.append(freeze_pid)
                applied += 1
                return True
            except (
                InjectOwnerLockSetTooNarrow,
                SQLAlchemyError,
                SimulatorPidTakenError,
                InjectTrustLineWriteFailed,
            ):
                # Programme 015, phase B step 3 - see op_add_participant.
                raise
            except Exception as exc:
                self._logger.warning(
                    "simulator.real.inject.freeze_participant.error: %s",
                    exc,
                    exc_info=True,
                )
                skipped += 1
                return False

        for eff in effects[:max_edges]:
            if not isinstance(eff, dict):
                skipped += 1
                continue

            op = str(eff.get("op") or "").strip()

            if op == "add_participant":
                await op_add_participant(eff)
                continue
            if op == "create_trustline":
                await op_create_trustline(eff)
                continue
            if op == "freeze_participant":
                await op_freeze_participant(eff)
                continue
            # An op this executor does not have. The scenario schema refuses it on upload, but a scenario stored before
            # an op was removed (`inject_debt`, 030 S3) is still loaded as it is; the note names the op (031, BACKLOG 18).
            skipped += 1
            reason = f"{UNSUPPORTED_OP_REASON}:{op[:64] or '<empty>'}"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

        # Programme 021, stage 2: flush, the after-checkpoint of each touched equivalent, the audit rows. A failure
        # here propagates to the owner, which rolls the event back.
        await batch.finish()

        # No commit here: the owner commits (programme 015, phase B step 3).
        return StagedInjectEvent(
            affected_equivalents=set(affected_equivalents),
            new_participants=list(new_participants_for_cache),
            new_participants_scenario=list(new_participants_for_scenario),
            new_trustlines_scenario=list(new_trustlines_for_scenario),
            frozen_participant_pids=list(frozen_participant_pids),
            pid_additions=dict(pid_additions),
            applied=int(applied),
            skipped=int(skipped),
            skipped_reasons=dict(skipped_reasons),
        )

    async def publish_committed_inject(
        self,
        *,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        event_index: int,
        event_time_ms: int,
        staged: StagedInjectEvent,
    ) -> InjectResult:
        """Publish an inject event whose staged effects the owner has committed.

        Programme 015, phase B step 3. Called only after a confirmed commit: it mutates the
        run's caches and the in-memory scenario, emits `topology.changed` and records the "inject
        applied" note. It does not mark the event
        fired - the owner did that before its commit - and a failure here must never undo,
        retry or re-stage a commit that already happened.
        """

        frozen_participant_pids = staged.frozen_participant_pids

        # ---- collect frozen edges before cache invalidation ------
        frozen_edges_for_sse: list[dict[str, str]] = []
        if frozen_participant_pids:
            frozen_set = set(frozen_participant_pids)
            s_tls = scenario.get("trustlines")
            if isinstance(s_tls, list):
                for tl in s_tls:
                    if not isinstance(tl, dict):
                        continue
                    frm = str(tl.get("from") or "").strip()
                    to = str(tl.get("to") or "").strip()
                    eq = effective_equivalent(scenario=scenario, payload=(tl or {}))
                    st = str(tl.get("status") or "").strip().lower()
                    if (frm in frozen_set or to in frozen_set) and st == "active":
                        frozen_edges_for_sse.append(
                            {
                                "from_pid": frm,
                                "to_pid": to,
                                "equivalent_code": eq.upper(),
                            }
                        )

        result = InjectResult(
            affected_equivalents=set(staged.affected_equivalents),
            new_participants=list(staged.new_participants),
            new_participants_scenario=list(staged.new_participants_scenario),
            new_trustlines_scenario=list(staged.new_trustlines_scenario),
            frozen_participant_pids=list(frozen_participant_pids),
            frozen_edges=list(frozen_edges_for_sse),
            applied=int(staged.applied),
            skipped=int(staged.skipped),
            skipped_reasons=dict(staged.skipped_reasons),
        )

        # ---- cache invalidation after successful commit -----------
        self.invalidate_caches_after_inject(
            run=run,
            scenario=scenario,
            affected_equivalents=result.affected_equivalents,
            new_participants=result.new_participants,
            new_participants_scenario=result.new_participants_scenario,
            new_trustlines_scenario=result.new_trustlines_scenario,
            frozen_pids=result.frozen_participant_pids,
        )

        # ---- SSE topology.changed (per affected equivalent) -------
        self.broadcast_topology_changed(
            run_id=run_id,
            run=run,
            affected_equivalents=result.affected_equivalents,
            new_participants_scenario=result.new_participants_scenario,
            new_trustlines_scenario=result.new_trustlines_scenario,
            frozen_pids=result.frozen_participant_pids,
            frozen_edges=result.frozen_edges,
        )

        self.enqueue_inject_note(
            run_id,
            run=run,
            event_index=event_index,
            event_time_ms=event_time_ms,
            description="inject applied",
            stats={
                "applied": int(result.applied),
                "skipped": int(result.skipped),
                **({"skipped_reasons": dict(result.skipped_reasons)} if result.skipped_reasons else {}),
            },
        )

        return result

    def invalidate_caches_after_inject(
        self,
        *,
        run: RunRecord,
        scenario: dict[str, Any],
        affected_equivalents: set[str],
        new_participants: list[tuple[uuid.UUID, str]],
        new_participants_scenario: list[dict[str, Any]],
        new_trustlines_scenario: list[dict[str, Any]],
        frozen_pids: list[str],
    ) -> None:
        invalidate_caches_after_inject(
            logger=self._logger,
            run=run,
            scenario=scenario,
            affected_equivalents=affected_equivalents,
            new_participants=new_participants,
            new_participants_scenario=new_participants_scenario,
            new_trustlines_scenario=new_trustlines_scenario,
            frozen_pids=frozen_pids,
        )

    def broadcast_topology_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        affected_equivalents: set[str],
        new_participants_scenario: list[dict[str, Any]],
        new_trustlines_scenario: list[dict[str, Any]],
        frozen_pids: list[str],
        frozen_edges: list[dict[str, str]],
    ) -> None:
        broadcast_topology_changed(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            run_id=run_id,
            run=run,
            affected_equivalents=affected_equivalents,
            new_participants_scenario=new_participants_scenario,
            new_trustlines_scenario=new_trustlines_scenario,
            frozen_pids=frozen_pids,
            frozen_edges=frozen_edges,
        )
