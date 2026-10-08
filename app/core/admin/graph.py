"""The admin graph read, in one place (032 / S2: B-1, B-2, B-3, B-4, B-8, B-10).

``GET /admin/graph/snapshot`` and ``GET /admin/graph/ego`` used to be two copies of the same ~300 lines in
``app/api/v1/admin.py``.  They are one read now, and the ego route is the general case: **the snapshot is the
ego read with the whole network as its scope and no filters.**  The two handlers stay thin - parse, validate,
call ``load_graph``, wrap the dict in their response model.

Three rules live here and nowhere else:

* **One projection of a trustline** (``trustline_select``, ``trustline_schema``): the line, the debt it
  supports (``used``) and what is left of it (``available = limit - used``), for the snapshot, the ego read and
  ``GET /admin/trustlines``.  A **closed** line is history: since migration 019 a closed incarnation may share
  (from, to, equivalent) with the live one, and the debt of the pair belongs to the live line, so a closed
  line has ``used = 0`` and therefore ``available = limit`` everywhere (before this module the graph attached
  the live line's debt to the closed one and the list did not).
* **Node colour and size** come from ``app.core.simulator.viz_rules`` - the rules the simulator already
  uses - not from a private copy.
* **Optional collections** (``include=``) are fetched, cut to their limit and reported (``included`` /
  ``truncated``) by ``fetch_optional_collections``; see its docstring for what an empty list can and cannot say.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.core.simulator import viz_rules
from app.core.simulator.net_balance_utils import net_decimal_to_atoms
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent as EquivalentModel
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.admin import AdminAuditLogItem
from app.schemas.equivalents import StoredEquivalent
from app.schemas.graph import AdminGraphDebt, AdminGraphParticipant
from app.schemas.trustline import TrustLine as TrustLineSchema

# Live rows first, then a stable tie-break.  Since migration 019 a closed incarnation may share
# (from, to, equivalent) with the live one, so a query that does not order deterministically returns them in
# planner order, and a query that does not de-duplicate emits BOTH as graph edges.
_TRUSTLINE_LIVE_FIRST = case((TrustLine.status == "closed", 1), else_=0)

# Positions of the columns `_dedupe_trustline_rows` reads in a row of `trustline_select`.
_EQUIVALENT_INDEX, _FROM_INDEX, _TO_INDEX = 6, 7, 9


def _dedupe_trustline_rows(rows, *, equivalent_index: int, from_index: int, to_index: int):
    """Keep one row per (equivalent, from, to) -- the live one when it exists.

    Restores the "one edge per triple" shape the graph had while the unique constraint was
    unconditional, without hiding pairs whose only incarnation is closed.
    """
    seen: set[tuple] = set()
    out = []
    for row in rows:
        key = (row[equivalent_index], row[from_index], row[to_index])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# --- the one projection of a trustline ---------------------------------------------------------------------


def trustline_select() -> tuple[Select, Any, Any]:
    """``(statement, from_participant_alias, to_participant_alias)``: every trustline with the names of its two
    ends, its equivalent's code and ``used`` - the debt on the pair (debtor = ``to``, creditor = ``from``) for a
    line that is not closed, ``0`` for a closed one.

    The debt is joined with an OUTER join that a closed line never satisfies, so one statement carries the
    whole page - no per-row reads.  The pair's debt is unique (``uq_debts_debtor_creditor_equivalent``), so the
    join does not multiply rows.  Column order is part of the contract with ``trustline_schema`` and
    ``_dedupe_trustline_rows``; callers add ``where`` / ``order_by`` / paging only.
    """
    p_from = aliased(Participant)
    p_to = aliased(Participant)
    stmt = (
        select(
            TrustLine.id,
            TrustLine.limit,
            TrustLine.status,
            TrustLine.created_at,
            TrustLine.updated_at,
            TrustLine.policy,
            EquivalentModel.code.label("equivalent"),
            p_from.pid.label("from_pid"),
            p_from.display_name.label("from_display_name"),
            p_to.pid.label("to_pid"),
            p_to.display_name.label("to_display_name"),
            func.coalesce(Debt.amount, 0).label("used"),
            TrustLine.close_requested_at,
        )
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .join(p_from, TrustLine.from_participant_id == p_from.id)
        .join(p_to, TrustLine.to_participant_id == p_to.id)
        .outerjoin(
            Debt,
            and_(
                TrustLine.status != "closed",
                Debt.debtor_id == TrustLine.to_participant_id,
                Debt.creditor_id == TrustLine.from_participant_id,
                Debt.equivalent_id == TrustLine.equivalent_id,
            ),
        )
    )
    return stmt, p_from, p_to


def trustline_schema(row) -> TrustLineSchema:
    """One row of ``trustline_select`` as the wire model.  ``available = limit - used`` is the formula and the
    only place it is written."""
    (
        tl_id,
        limit,
        status,
        created_at,
        updated_at,
        policy,
        equivalent_code,
        from_pid,
        from_display_name,
        to_pid,
        to_display_name,
        used,
        close_requested_at,
    ) = row
    return TrustLineSchema.model_validate(
        {
            "id": tl_id,
            "from_pid": from_pid,
            "to_pid": to_pid,
            "from_display_name": from_display_name,
            "to_display_name": to_display_name,
            "equivalent_code": equivalent_code,
            "limit": limit,
            "used": used,
            "available": limit - used,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
            "close_requested_at": close_requested_at,
            "policy": policy,
        }
    )


def trustline_page_statements(
    *,
    equivalent: str | None,
    creditor: str | None,
    debtor: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[Select, Select]:
    """``(page, count)`` for ``GET /admin/trustlines``: the same joins and the same predicates in both, so a page
    and its ``total`` cannot disagree.  An empty filter value is no filter; a value that names nothing (an unknown
    equivalent code or pid) matches no row, so the page is empty and the total is zero.  Order: ``created_at desc,
    id asc`` - ``created_at`` is not unique (fixtures write identical values in bulk, and a triple can hold several
    rows), so without the tie-break offset/limit pages could repeat or skip rows.
    """
    page, p_from, p_to = trustline_select()
    predicates = []
    if status:
        predicates.append(TrustLine.status == status)
    if creditor:
        predicates.append(p_from.pid == creditor)
    if debtor:
        predicates.append(p_to.pid == debtor)
    if equivalent:
        predicates.append(EquivalentModel.code == equivalent)

    count = (
        select(func.count())
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .join(p_from, TrustLine.from_participant_id == p_from.id)
        .join(p_to, TrustLine.to_participant_id == p_to.id)
        .where(*predicates)
    )
    page = page.where(*predicates).order_by(TrustLine.created_at.desc(), TrustLine.id.asc()).offset(offset).limit(limit)
    return page, count


# --- optional collections (`include=`) ---------------------------------------------------------------------


def _parse_include_csv(value: str | None) -> set[str]:
    if value is None:
        return set()
    raw = str(value).strip()
    if not raw:
        return set()
    out: set[str] = set()
    for part in raw.split(","):
        p = part.strip().lower()
        if p:
            out.add(p)
    return out


async def fetch_optional_collections(
    db: AsyncSession, include: str | None
) -> tuple[list[Any], list[Any], list[str], list[str]]:
    """The optional collections of a graph read, plus what the wire must say about them.

    F-013-1 / T1302, 2026-09-10. Two things are returned beyond the data, and both exist because
    an empty list is ambiguous:

      * ``included`` - which collections this response actually carries. Without it, "you did not
        ask" and "you asked and there are none" are byte-identical, and the consumer counting
        payments reported zero for a period it was never told about.
      * ``truncated`` - which of them hit the include limit. A count over a cut list is a lower
        bound presented as a total, and nothing on the wire admitted the cut.

    Truncation is detected by asking for one row more than the limit and trimming: the fetch
    helpers order deterministically, so the extra row is proof of "there is more" and never
    reaches the client.
    """

    include_set = _parse_include_csv(include)
    audit_log: list[Any] = []
    transactions: list[Any] = []
    included: list[str] = []
    truncated: list[str] = []

    async def _take(name: str, fetch, limit: int) -> list[Any]:
        rows = await fetch(db, limit=limit + 1)
        included.append(name)
        if len(rows) > limit:
            truncated.append(name)
            return list(rows[:limit])
        return list(rows)

    if "audit_log" in include_set:
        audit_log = await _take(
            "audit_log",
            fetch_graph_audit_log,
            int(settings.ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS or 50),
        )
    if "transactions" in include_set:
        transactions = await _take(
            "transactions",
            fetch_graph_transactions,
            int(settings.ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS or 50),
        )

    return audit_log, transactions, included, truncated


async def fetch_graph_audit_log(db: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
    items = (await db.execute(stmt)).scalars().all()
    return [AdminAuditLogItem.model_validate(x).model_dump() for x in items]


async def fetch_graph_transactions(db: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    stmt = (
        select(Transaction, Participant.pid)
        .outerjoin(Participant, Transaction.initiator_id == Participant.id)  # a CLEARING has none (028 F-028-45)
        .order_by(desc(Transaction.updated_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[dict[str, Any]] = []
    for tx, initiator_pid in rows:
        payload = tx.payload or {}
        item: dict[str, Any] = {
            "tx_id": tx.tx_id,
            "type": tx.type,
            "state": tx.state,
            "initiator_pid": None if initiator_pid is None else str(initiator_pid),
            "created_at": tx.created_at,
            "updated_at": tx.updated_at,
            "equivalent": payload.get("equivalent"),
            "error": tx.error,
        }

        # WHO THIS TRANSACTION IS ABOUT (F-013-1 / T1302, 2026-09-10).
        #
        # `initiator_pid` alone cannot answer it. A consumer asking "was this participant party to
        # this payment" gets "the initiator was someone else", which is not an answer, and a screen
        # that turns that into a count reports zero for people who were paid. The spec prescribes
        # exactly this projection: `from`/`to` for a payment, minimal `edges` for a clearing.
        #
        # THE FULL `payload` IS DELIBERATELY NOT PUBLISHED. It is internal and versionless
        # (`Transaction.payload`), so only the named keys cross the wire, and the clearing edges are
        # cut down to the two pids - no amounts, no `debt_id`, which belong to the audit surface and
        # not to a graph read.
        if tx.type == "PAYMENT":
            sender = payload.get("from")
            recipient = payload.get("to")
            if isinstance(sender, str) and sender:
                item["from"] = sender
            if isinstance(recipient, str) and recipient:
                item["to"] = recipient
        elif tx.type == "CLEARING":
            raw_edges = payload.get("edges")
            if isinstance(raw_edges, list):
                edges: list[dict[str, str]] = []
                for edge in raw_edges:
                    if not isinstance(edge, dict):
                        continue
                    debtor = edge.get("debtor")
                    creditor = edge.get("creditor")
                    if isinstance(debtor, str) and isinstance(creditor, str) and debtor and creditor:
                        edges.append({"debtor": debtor, "creditor": creditor})
                if edges:
                    item["edges"] = edges

        out.append(item)
    return out


# --- the graph read -----------------------------------------------------------------------------------------


async def ego_participant_ids(
    db: AsyncSession,
    root_id: Any,
    *,
    depth: int,
    equivalent: str | None,
    statuses: list[str] | None,
) -> set[Any]:
    """The participants within ``depth`` hops of ``root_id`` on the trustline graph taken as undirected, over the
    lines that match ``equivalent`` / ``statuses`` (no filter = every line)."""
    visited_ids: set[Any] = {root_id}
    frontier_ids: set[Any] = {root_id}

    for _ in range(int(depth)):
        if not frontier_ids:
            break

        stmt = (
            select(TrustLine.from_participant_id, TrustLine.to_participant_id)
            .select_from(TrustLine)
            .where(
                (TrustLine.from_participant_id.in_(list(frontier_ids)))
                | (TrustLine.to_participant_id.in_(list(frontier_ids)))
            )
        )
        if equivalent:
            stmt = stmt.join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id).where(
                EquivalentModel.code == equivalent
            )
        if statuses:
            stmt = stmt.where(TrustLine.status.in_(list(statuses)))

        next_frontier: set[Any] = set()
        for from_id, to_id in (await db.execute(stmt)).all():
            if from_id in frontier_ids and to_id not in visited_ids:
                next_frontier.add(to_id)
            if to_id in frontier_ids and from_id not in visited_ids:
                next_frontier.add(from_id)

        visited_ids |= next_frontier
        frontier_ids = next_frontier

    return visited_ids


async def _attach_net_viz(
    db: AsyncSession,
    participants: list[AdminGraphParticipant],
    id_by_pid: dict[str, Any],
    equivalent: EquivalentModel,
    scope_ids: set[Any] | None,
) -> None:
    """Net balance, colour and size of each node in ONE equivalent (nothing is summed across equivalents).

    The sums run over every debt of the equivalent that touches a node of the scope - a node's net counts its
    debts to nodes outside the scope too.  ``scope_ids=None`` is the whole network, where no filter is needed.
    """
    precision = int(equivalent.precision)

    def _totals(column) -> Select:
        stmt = select(column, func.coalesce(func.sum(Debt.amount), 0)).where(
            Debt.equivalent_id == equivalent.id, Debt.amount > 0
        )
        if scope_ids is not None:
            stmt = stmt.where(column.in_(list(scope_ids)))
        return stmt.group_by(column)

    debt_by_id = {i: s for i, s in (await db.execute(_totals(Debt.debtor_id))).all()}
    credit_by_id = {i: s for i, s in (await db.execute(_totals(Debt.creditor_id))).all()}

    atoms_by_pid: dict[str, int] = {}
    for p in participants:
        pid_id = id_by_pid[p.pid]
        # 028 F-028-39: the shared rule keeps the sign of a sub-quantum net (T1210).
        net = credit_by_id.get(pid_id, Decimal(0)) - debt_by_id.get(pid_id, Decimal(0))
        atoms_by_pid[p.pid] = net_decimal_to_atoms(net, precision=precision)

    mags_sorted, debt_mags_sorted = viz_rules.collect_magnitudes(atoms_by_pid.values())

    for p in participants:
        atoms = atoms_by_pid[p.pid]
        p.net_balance_atoms = str(atoms)
        p.net_sign = viz_rules.net_sign_from_atoms(atoms)
        p.viz_color_key = viz_rules.node_color_key(
            atoms=atoms, status_key=p.status, type_key=p.type, debt_mags_sorted=debt_mags_sorted
        )
        w, h = viz_rules.node_size_wh(atoms_abs=abs(atoms), mags_sorted=mags_sorted, type_key=p.type)
        p.viz_size = {"w": w, "h": h}


async def load_graph(
    db: AsyncSession,
    *,
    net_equivalent: str | None,
    include: str | None,
    scope_ids: set[Any] | None = None,
    line_equivalent: str | None = None,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    """The body of a graph read, as the keyword arguments of ``AdminGraphSnapshotResponse``.

    ``scope_ids`` is the participant set the read is about (``None`` = every participant, the snapshot).  Inside a
    scope, lines and debts are restricted to those with BOTH ends in it.  ``line_equivalent`` and ``statuses``
    narrow the lines (and, for the equivalent, the debts); the snapshot passes neither.  ``net_equivalent`` only
    selects the equivalent whose net balance colours and sizes the nodes - without one, the viz fields stay null.

    Guardrail: a trustline's direction in the output is ``from -> to`` = creditor -> debtor.
    """
    participant_stmt = select(Participant.id, Participant.pid, Participant.display_name, Participant.type, Participant.status)
    if scope_ids is not None:
        participant_stmt = participant_stmt.where(Participant.id.in_(list(scope_ids)))
    participant_rows = (await db.execute(participant_stmt.order_by(Participant.pid.asc()))).all()
    id_by_pid: dict[str, Any] = {}
    participants: list[AdminGraphParticipant] = []
    for id_, pid, display_name, type_, status in participant_rows:
        id_by_pid[pid] = id_
        participants.append(
            AdminGraphParticipant(
                pid=pid,
                display_name=display_name,
                type=type_,
                status=str(status or "").strip().lower(),
                net_balance_atoms=None,
                net_sign=None,
                viz_color_key=None,
                viz_size=None,
            )
        )

    # Equivalents: the full list, for the UI's dropdown.
    eq_models = (await db.execute(select(EquivalentModel).order_by(EquivalentModel.code.asc()))).scalars().all()
    equivalents = [StoredEquivalent.model_validate(e) for e in eq_models]

    net_code = net_equivalent  # already the canonical code: the route reads it through `canonical_code`
    if net_code and participants:
        net_model = next((e for e in eq_models if e.code == net_code), None)
        if net_model is not None:
            await _attach_net_viz(db, participants, id_by_pid, net_model, scope_ids)

    # Trustlines + used/available (one statement), one edge per (equivalent, from, to), the live line first.
    tl_stmt, p_from, p_to = trustline_select()
    if scope_ids is not None:
        tl_stmt = tl_stmt.where(
            TrustLine.from_participant_id.in_(list(scope_ids)),
            TrustLine.to_participant_id.in_(list(scope_ids)),
        )
    if line_equivalent:
        tl_stmt = tl_stmt.where(EquivalentModel.code == line_equivalent)
    if statuses:
        tl_stmt = tl_stmt.where(TrustLine.status.in_(list(statuses)))
    tl_stmt = tl_stmt.order_by(
        EquivalentModel.code.asc(),
        p_from.pid.asc(),
        p_to.pid.asc(),
        _TRUSTLINE_LIVE_FIRST.asc(),
        TrustLine.id.asc(),
    )
    tl_rows = _dedupe_trustline_rows(
        (await db.execute(tl_stmt)).all(),
        equivalent_index=_EQUIVALENT_INDEX,
        from_index=_FROM_INDEX,
        to_index=_TO_INDEX,
    )
    trustlines = [trustline_schema(row) for row in tl_rows]

    # Debts
    p_debtor = aliased(Participant)
    p_creditor = aliased(Participant)
    debt_stmt = (
        select(
            EquivalentModel.code.label("equivalent"),
            p_debtor.pid.label("debtor"),
            p_creditor.pid.label("creditor"),
            Debt.amount,
        )
        .select_from(Debt)
        .join(EquivalentModel, Debt.equivalent_id == EquivalentModel.id)
        .join(p_debtor, Debt.debtor_id == p_debtor.id)
        .join(p_creditor, Debt.creditor_id == p_creditor.id)
        .where(Debt.amount > 0)
        .order_by(EquivalentModel.code.asc(), p_debtor.pid.asc(), p_creditor.pid.asc())
    )
    if scope_ids is not None:
        debt_stmt = debt_stmt.where(
            Debt.debtor_id.in_(list(scope_ids)),
            Debt.creditor_id.in_(list(scope_ids)),
        )
    if line_equivalent:
        debt_stmt = debt_stmt.where(EquivalentModel.code == line_equivalent)
    debts = [
        AdminGraphDebt(equivalent=eq, debtor=debtor, creditor=creditor, amount=amount)
        for eq, debtor, creditor, amount in (await db.execute(debt_stmt)).all()
    ]

    audit_log, transactions, included, truncated = await fetch_optional_collections(db, include)

    return {
        "participants": participants,
        "trustlines": trustlines,
        "equivalents": equivalents,
        "debts": debts,
        "audit_log": audit_log,
        "transactions": transactions,
        "included": included,
        "truncated": truncated,
    }
