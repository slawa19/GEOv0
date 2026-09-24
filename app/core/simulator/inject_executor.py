from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Awaitable, Callable

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.simulator.cache_invalidator import (
    invalidate_caches_after_inject as _invalidate_caches_after_inject,
)
from app.core.ledger.book import APPLIED, REFUSED_OPPOSING_DEBT, Book, InjectIncrease
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import InjectResult, RunRecord
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.simulator import (
    TopologyChangedEdgeRef,
    TopologyChangedNodeRef,
    TopologyChangedPayload,
)
from app.core.simulator.scenario_equivalent import effective_equivalent
from app.utils.validation import is_storable_money

# How many effects of one inject event are processed. Shared by staging and by the lock-set
# helper below, so the helper never names fewer equivalents than staging can reach.
_MAX_INJECT_EFFECTS = 500


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
    inject_debt_equivalents: set[str] = field(default_factory=set)
    inject_debt_edges_by_eq: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    pid_additions: dict[str, uuid.UUID] = field(default_factory=dict)
    applied: int = 0
    skipped: int = 0
    total_applied: Decimal = Decimal("0")


def inject_event_equivalent_codes(
    *, scenario: Mapping[str, Any], event: Mapping[str, Any] | None
) -> set[str]:
    """Equivalent codes an inject event names by itself, before touching the database.

    Programme 015, phase B step 3: the owner locks these together with the run's equivalents
    before staging. `inject_debt` and `create_trustline` name one equivalent each; the initial
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
        if op in {"inject_debt", "create_trustline"}:
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
    """Participants whose incident trustlines a `freeze_participant` effect of the event will freeze.

    Programme 015, phase B step 3, external review of the step. The equivalents of those trustlines
    are not named by the event, so the owner reads them before locking. Without this read, staging
    stopped at the FIRST equivalent it lacked: an event freezing two participants whose trustlines
    live in two equivalents outside the run needed two expansions, exhausted the one allowed, and -
    since every tick starts again from the run's set - never completed on a topology nobody was
    changing. The bounded expansion is left as what it is meant to be: a backstop for a trustline
    created between the owner's read and the staging.

    Mirrors staging's own parsing: an empty pid is skipped there, and `freeze_trustlines=False`
    freezes no trustline, so neither needs a lock.
    """

    pids: set[str] = set()
    effects = (event or {}).get("effects")
    if not isinstance(effects, list):
        return pids
    for eff in effects[:_MAX_INJECT_EFFECTS]:
        if not isinstance(eff, dict):
            continue
        if str(eff.get("op") or "").strip() != "freeze_participant":
            continue
        if not bool(eff.get("freeze_trustlines", True)):
            continue
        pid = str(eff.get("participant_id") or "").strip()
        if pid:
            pids.add(pid)
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
        max_total_amount: Decimal | None = None
        try:
            md = (event or {}).get("metadata")
            if isinstance(md, dict) and md.get("max_total_amount") is not None:
                max_total_amount = Decimal(str(md.get("max_total_amount")))
        except Exception:
            max_total_amount = None
        if max_total_amount is not None and max_total_amount <= 0:
            max_total_amount = None

        applied = 0
        skipped = 0
        total_applied = Decimal("0")

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
        inject_debt_equivalents: set[str] = set()
        inject_debt_edges_by_eq: dict[str, set[tuple[str, str]]] = {}

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
                eq_precision_by_code[eq_upper] = int(row[1] or 2)
                return row[0]
            return None

        def eq_precision(eq_code: str) -> int:
            return int(eq_precision_by_code.get(eq_code.strip().upper(), 2))

        default_tl_policy = {
            "auto_clearing": True,
            "can_be_intermediate": True,
            "max_hop_usage": None,
            "daily_limit": None,
            "blocked_participants": [],
        }

        async def op_inject_debt(eff: dict[str, Any]) -> bool:
            nonlocal applied, skipped, total_applied

            eq = effective_equivalent(scenario=scenario, payload=(eff or {}))
            # Contract: prefer from/to (creditor->debtor), but keep
            # backward-compatible debtor/creditor keys.
            creditor_pid = str(eff.get("creditor") or eff.get("from") or "").strip()
            debtor_pid = str(eff.get("debtor") or eff.get("to") or "").strip()
            if not eq or not debtor_pid or not creditor_pid:
                skipped += 1
                return False

            try:
                amount = Decimal(str(eff.get("amount")))
                amount = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            except Exception:
                skipped += 1
                return False
            if amount <= 0:
                skipped += 1
                return False
            # Storage-capacity door (012 / F-012-1).  The quantize above already bounds the
            # fraction, but nothing bounded the magnitude: a `Debt.amount` of 1e12 or more
            # does not fit Numeric(20, 8) and aborts the whole inject transaction with
            # `numeric field overflow`.  An inject entry that cannot be applied is skipped,
            # which is this executor's declared behaviour for every other unusable field.
            if not is_storable_money(amount):
                self._logger.warning(
                    "simulator.real.inject.inject_debt.amount_unstorable amount=%s",
                    amount,
                )
                skipped += 1
                return False

            if max_total_amount is not None and total_applied + amount > max_total_amount:
                skipped += 1
                return False

            debtor_id = pids.get(debtor_pid)
            creditor_id = pids.get(creditor_pid)
            if debtor_id is None or creditor_id is None:
                skipped += 1
                return False

            eq_id = await resolve_eq_id(eq)
            if eq_id is None:
                skipped += 1
                return False
            # Programme 015, phase B step 3: the debt below belongs to this equivalent.
            require_owner_locks({eq_id})

            tl = (
                await session.execute(
                    # Live row only (migration 019): a closed incarnation may coexist.
                    select(TrustLine.limit, TrustLine.status).where(
                        TrustLine.from_participant_id == creditor_id,
                        TrustLine.to_participant_id == debtor_id,
                        TrustLine.equivalent_id == eq_id,
                        TrustLine.status != "closed",
                    )
                )
            ).one_or_none()
            if tl is None:
                skipped += 1
                return False
            tl_limit, tl_status = tl
            if str(tl_status or "").strip().lower() != "active":
                skipped += 1
                return False
            try:
                tl_limit_amt = Decimal(str(tl_limit))
            except Exception:
                skipped += 1
                return False
            if tl_limit_amt <= 0:
                skipped += 1
                return False

            # THE WRITE IS THE BOOK'S (018 stage A): increase or refuse. An effect opposite to an
            # existing debt (F-015-12, protocol §11.2.4) and an effect whose result would exceed the
            # trust limit are both REFUSED and counted as skipped; neither nets. The book flushes
            # before its reads, so an effect staged earlier in this event is seen - the rule and its
            # concurrency argument are documented at `_apply_inject_increase`.
            outcome = await Book.current(session).apply(
                InjectIncrease(
                    debtor_id=debtor_id,
                    creditor_id=creditor_id,
                    equivalent_id=eq_id,
                    amount=amount,
                    ceiling=tl_limit_amt,
                )
            )
            if outcome == REFUSED_OPPOSING_DEBT:
                self._logger.warning(
                    "simulator.real.inject.inject_debt.opposing_debt_exists equivalent=%s",
                    eq,
                )
                skipped += 1
                return False
            if outcome != APPLIED:
                skipped += 1
                return False

            applied += 1
            total_applied += amount
            affected_equivalents.add(eq)
            inject_debt_equivalents.add(eq)
            inject_debt_edges_by_eq.setdefault(eq, set()).add((creditor_pid, debtor_pid))
            return True

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

                # Idempotency: skip if participant already exists.
                existing_p = (
                    await session.execute(select(Participant.id).where(Participant.pid == pid))
                ).scalar_one_or_none()
                if existing_p is not None:
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
                status = str(p_data.get("status") or "active").strip().lower()
                if status not in {"active", "suspended", "left", "deleted"}:
                    status = "active"
                public_key = hashlib.sha256(pid.encode("utf-8")).hexdigest()

                new_p = Participant(
                    pid=pid,
                    display_name=name,
                    public_key=public_key,
                    type=p_type,
                    status=status,
                    profile={},
                )
                session.add(new_p)
                await session.flush()  # materialise new_p.id

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
                        if tl_limit_val <= 0:
                            continue
                        # Storage-capacity door (012 / F-012-1) - see op_inject_debt.
                        if not is_storable_money(tl_limit_val):
                            self._logger.warning(
                                "simulator.real.inject.add_participant.limit_unstorable "
                                "sponsor=%s limit=%s",
                                sponsor_pid,
                                tl_limit_val,
                            )
                            continue

                        # Resolve sponsor participant ID.
                        sponsor_id = pids.get(sponsor_pid)
                        if sponsor_id is None:
                            sponsor_row = (
                                await session.execute(
                                    select(Participant.id).where(Participant.pid == sponsor_pid)
                                )
                            ).scalar_one_or_none()
                            if sponsor_row is None:
                                self._logger.warning(
                                    "simulator.real.inject.add_participant.sponsor_not_found sponsor=%s",
                                    sponsor_pid,
                                )
                                continue
                            sponsor_id = sponsor_row
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

                        session.add(
                            TrustLine(
                                from_participant_id=from_id,
                                to_participant_id=to_id,
                                equivalent_id=eq_id,
                                limit=tl_limit_val,
                                status="active",
                                policy=dict(default_tl_policy),
                            )
                        )
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

                applied += 1
                return True
            except (InjectOwnerLockSetTooNarrow, SQLAlchemyError):
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
                if tl_limit_val <= 0:
                    skipped += 1
                    return False
                # Storage-capacity door (012 / F-012-1) - see op_inject_debt.
                if not is_storable_money(tl_limit_val):
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
                            select(Participant.id).where(Participant.pid == from_pid_val)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.from_not_found pid=%s",
                            from_pid_val,
                        )
                        skipped += 1
                        return False
                    from_id = row
                    remember_pid(from_pid_val, from_id)

                to_id = pids.get(to_pid_val)
                if to_id is None:
                    row = (
                        await session.execute(
                            select(Participant.id).where(Participant.pid == to_pid_val)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        self._logger.warning(
                            "simulator.real.inject.create_trustline.to_not_found pid=%s",
                            to_pid_val,
                        )
                        skipped += 1
                        return False
                    to_id = row
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

                session.add(
                    TrustLine(
                        from_participant_id=from_id,
                        to_participant_id=to_id,
                        equivalent_id=eq_id,
                        limit=tl_limit_val,
                        status="active",
                        policy=dict(default_tl_policy),
                    )
                )
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
            except (InjectOwnerLockSetTooNarrow, SQLAlchemyError):
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
                freeze_tls = bool(eff.get("freeze_trustlines", True))

                if not freeze_pid:
                    skipped += 1
                    return False

                # Update participant status → suspended.
                p_row = (
                    await session.execute(select(Participant).where(Participant.pid == freeze_pid))
                ).scalar_one_or_none()
                if p_row is None:
                    self._logger.warning(
                        "simulator.real.inject.freeze_participant.not_found pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False

                if p_row.status == "suspended":
                    self._logger.info(
                        "simulator.real.inject.freeze_participant.already_suspended pid=%s",
                        freeze_pid,
                    )
                    skipped += 1
                    return False

                incident_tls: list[TrustLine] = []
                if freeze_tls:
                    incident_tls = list(
                        (
                            await session.execute(
                                select(TrustLine).where(
                                    or_(
                                        TrustLine.from_participant_id == p_row.id,
                                        TrustLine.to_participant_id == p_row.id,
                                    ),
                                    TrustLine.status == "active",
                                )
                            )
                        ).scalars().all()
                    )
                    # Programme 015, phase B step 3. This freezes EVERY active incident trustline,
                    # whatever its equivalent, so the set of owner locks it needs is only known
                    # here. Checked before anything of this effect is staged - the participant's
                    # own status included - so a too-narrow set leaves nothing behind to roll back.
                    require_owner_locks({tl.equivalent_id for tl in incident_tls})

                p_row.status = "suspended"
                for frozen_tl in incident_tls:
                    frozen_tl.status = "frozen"

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
            except (InjectOwnerLockSetTooNarrow, SQLAlchemyError):
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

            # Unknown inject op — skip silently.
            if op == "inject_debt":
                await op_inject_debt(eff)
                continue
            if op == "add_participant":
                await op_add_participant(eff)
                continue
            if op == "create_trustline":
                await op_create_trustline(eff)
                continue
            if op == "freeze_participant":
                await op_freeze_participant(eff)
                continue

        # No commit here: the owner commits (programme 015, phase B step 3).
        return StagedInjectEvent(
            affected_equivalents=set(affected_equivalents),
            new_participants=list(new_participants_for_cache),
            new_participants_scenario=list(new_participants_for_scenario),
            new_trustlines_scenario=list(new_trustlines_for_scenario),
            frozen_participant_pids=list(frozen_participant_pids),
            inject_debt_equivalents=set(inject_debt_equivalents),
            inject_debt_edges_by_eq={k: set(v) for k, v in inject_debt_edges_by_eq.items()},
            pid_additions=dict(pid_additions),
            applied=int(applied),
            skipped=int(skipped),
            total_applied=total_applied,
        )

    async def publish_committed_inject(
        self,
        session,
        *,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        event_index: int,
        event_time_ms: int,
        staged: StagedInjectEvent,
        build_edge_patch_for_equivalent: Callable[
            ..., Awaitable[list[dict[str, Any]]]
        ],
        broadcast_topology_edge_patch: Callable[
            ..., None
        ],
    ) -> InjectResult:
        """Publish an inject event whose staged effects the owner has committed.

        Programme 015, phase B step 3. Called only after a confirmed commit: it mutates the
        run's caches and the in-memory scenario, emits `topology.changed` and the inject edge
        patch, and records the "inject applied" note. It reads through `session` for the edge
        patch and leaves that read transaction for the owner to end. It does not mark the event
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
            inject_debt_equivalents=set(staged.inject_debt_equivalents),
            inject_debt_edges_by_eq={
                k: set(v) for k, v in staged.inject_debt_edges_by_eq.items()
            },
            applied=int(staged.applied),
            skipped=int(staged.skipped),
            total_applied=staged.total_applied,
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

        # inject_debt affects DB debts and therefore routing capacity; emit an
        # edge_patch so the frontend updates used/available/viz without refresh.
        if result.inject_debt_equivalents:
            try:
                for eq in result.inject_debt_equivalents:
                    edges = result.inject_debt_edges_by_eq.get(eq) or set()
                    edge_patch = await build_edge_patch_for_equivalent(
                        session=session,
                        run=run,
                        equivalent_code=str(eq),
                        only_edges=edges,
                        include_width_keys=False,
                    )
                    broadcast_topology_edge_patch(
                        run_id=run_id,
                        run=run,
                        equivalent=str(eq),
                        edge_patch=edge_patch,
                        reason="inject_debt",
                    )
            except Exception:
                self._logger.warning(
                    "simulator.real.inject.inject_debt_edge_patch_failed",
                    exc_info=True,
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
                "total_amount": format(result.total_applied, "f"),
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
