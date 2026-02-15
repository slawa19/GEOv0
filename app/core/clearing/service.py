import logging
import uuid
from decimal import Decimal
from typing import Dict, List, Set

from sqlalchemy import select, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.audit_log import IntegrityAuditLog
from app.utils.exceptions import GeoException
from app.utils.metrics import CLEARING_EVENTS_TOTAL
from app.core.payments.router import PaymentRouter
from app.core.invariants import InvariantChecker
from app.core.integrity import compute_integrity_checkpoint_for_equivalent

logger = logging.getLogger(__name__)


class ClearingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _dialect_name(self) -> str | None:
        try:
            return self.session.get_bind().dialect.name
        except Exception:
            return None

    def _is_sqlite(self) -> bool:
        return self._dialect_name() == "sqlite"

    def _bind_uuid(self, uid: uuid.UUID) -> object:
        """Return UUID in a format supported by the current DBAPI for raw SQL binds."""
        if self._is_sqlite():
            # SQLAlchemy stores UUIDs in SQLite as CHAR(32) hex (no dashes).
            return uid.hex
        return uid

    def _bind_decimal(self, val: Decimal) -> object:
        """Return Decimal in a format supported by the current DBAPI for raw SQL binds."""
        if self._is_sqlite():
            return float(val)
        return val

    def _sql_auto_clearing_ok(self, alias: str) -> str:
        """Dialect-aware SQL predicate: trustline policy permits auto-clearing.

        Must match `_policy_flag(..., default=True)` semantics as closely as possible:
        - NULL policy -> allow
        - missing key -> allow
        - explicit false-ish -> reject
        """
        dialect = self._dialect_name()
        if dialect == "sqlite":
            # json_extract returns 0/1 for JSON booleans; can also surface strings.
            return (
                "("
                f"{alias}.policy IS NULL OR "
                f"json_extract({alias}.policy, '$.auto_clearing') IS NULL OR "
                f"json_extract({alias}.policy, '$.auto_clearing') NOT IN (0, 'false', '0', 'no', 'off')"
                ")"
            )

        # Postgres (json/jsonb): policy->>'auto_clearing' yields text.
        return (
            "("
            f"{alias}.policy IS NULL OR "
            f"({alias}.policy->>'auto_clearing') IS NULL OR "
            f"lower({alias}.policy->>'auto_clearing') NOT IN ('false', '0', 'no', 'off')"
            ")"
        )

    @staticmethod
    def _deduplicate_cycles(cycles: List[List[Dict]]) -> List[List[Dict]]:
        """Stable dedupe by unordered set of debt ids.

        SQL cycle queries can emit the same logical cycle multiple times (different rotation).
        Keep first occurrence to preserve ordering heuristics (e.g. clear_amount DESC).
        """
        if not cycles:
            return []

        seen: set[tuple[str, ...]] = set()
        out: List[List[Dict]] = []
        for cycle in cycles:
            try:
                key = tuple(sorted(str(e.get("debt_id", "")) for e in cycle))
            except Exception:
                key = tuple()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(cycle)
        return out

    async def find_triangles_sql(self, equivalent_id: uuid.UUID) -> List[List[Dict]]:
        """Find 3-node debt cycles using a SQL JOIN."""

        dialect = self._dialect_name()

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount)"
        if dialect == "sqlite":
            # SQLite supports scalar min(x, y, z) as a LEAST replacement.
            least_expr = "min(d1.amount, d2.amount, d3.amount)"

        # NOTE: When executing raw SQL (text()), sqlite3 DBAPI does not accept uuid.UUID/Decimal
        # as bound parameters. Normalize binds for SQLite only.
        equivalent_id_param = self._bind_uuid(equivalent_id)
        min_amount_param = self._bind_decimal(Decimal("0.01"))

        query = text(
            f"""
            SELECT DISTINCT
                d1.id as debt1_id,
                d1.debtor_id as a,
                d1.creditor_id as b,
                d1.amount as amount1,
                d2.id as debt2_id,
                d2.creditor_id as c,
                d2.amount as amount2,
                d3.id as debt3_id,
                d3.amount as amount3,
                {least_expr} as clear_amount
            FROM debts d1
            JOIN debts d2 ON d1.creditor_id = d2.debtor_id
                         AND d1.equivalent_id = d2.equivalent_id
            JOIN debts d3 ON d2.creditor_id = d3.debtor_id
                         AND d3.creditor_id = d1.debtor_id
                         AND d2.equivalent_id = d3.equivalent_id
                        JOIN trust_lines t1 ON t1.from_participant_id = d1.creditor_id
                                                            AND t1.to_participant_id = d1.debtor_id
                                                            AND t1.equivalent_id = d1.equivalent_id
                                                            AND t1.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t3')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0
              AND {least_expr} > :min_amount
            ORDER BY clear_amount DESC
            LIMIT 100
            """
        )

        result = await self.session.execute(
            query,
            {
                "equivalent_id": equivalent_id_param,
                "min_amount": min_amount_param,
            },
        )

        cycles: List[List[Dict]] = []
        for row in result:
            cycles.append(
                [
                    {
                        "debt_id": str(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": str(row.amount1),
                    },
                    {
                        "debt_id": str(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": str(row.amount2),
                    },
                    {
                        "debt_id": str(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.a),
                        "amount": str(row.amount3),
                    },
                ]
            )

        return cycles

    async def find_quadrangles_sql(self, equivalent_id: uuid.UUID) -> List[List[Dict]]:
        """Find 4-node debt cycles using a SQL JOIN."""

        dialect = self._dialect_name()

        least_expr = "LEAST(d1.amount, d2.amount, d3.amount, d4.amount)"
        if dialect == "sqlite":
            least_expr = "min(d1.amount, d2.amount, d3.amount, d4.amount)"

        equivalent_id_param = self._bind_uuid(equivalent_id)
        min_amount_param = self._bind_decimal(Decimal("0.01"))

        query = text(
            f"""
            SELECT DISTINCT
                d1.id as debt1_id, d1.debtor_id as a, d1.creditor_id as b, d1.amount as amt1,
                d2.id as debt2_id, d2.creditor_id as c, d2.amount as amt2,
                d3.id as debt3_id, d3.creditor_id as d, d3.amount as amt3,
                d4.id as debt4_id, d4.amount as amt4,
                {least_expr} as clear_amount
            FROM debts d1
            JOIN debts d2 ON d1.creditor_id = d2.debtor_id AND d1.equivalent_id = d2.equivalent_id
            JOIN debts d3 ON d2.creditor_id = d3.debtor_id AND d2.equivalent_id = d3.equivalent_id
            JOIN debts d4 ON d3.creditor_id = d4.debtor_id AND d4.creditor_id = d1.debtor_id
                         AND d3.equivalent_id = d4.equivalent_id
                        JOIN trust_lines t1 ON t1.from_participant_id = d1.creditor_id
                                                            AND t1.to_participant_id = d1.debtor_id
                                                            AND t1.equivalent_id = d1.equivalent_id
                                                            AND t1.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t1')}
                        JOIN trust_lines t2 ON t2.from_participant_id = d2.creditor_id
                                                            AND t2.to_participant_id = d2.debtor_id
                                                            AND t2.equivalent_id = d2.equivalent_id
                                                            AND t2.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t2')}
                        JOIN trust_lines t3 ON t3.from_participant_id = d3.creditor_id
                                                            AND t3.to_participant_id = d3.debtor_id
                                                            AND t3.equivalent_id = d3.equivalent_id
                                                            AND t3.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t3')}
                        JOIN trust_lines t4 ON t4.from_participant_id = d4.creditor_id
                                                            AND t4.to_participant_id = d4.debtor_id
                                                            AND t4.equivalent_id = d4.equivalent_id
                                                            AND t4.status = 'active'
                                                            AND {self._sql_auto_clearing_ok('t4')}
            WHERE d1.equivalent_id = :equivalent_id
              AND d1.amount > 0 AND d2.amount > 0 AND d3.amount > 0 AND d4.amount > 0
              AND d1.debtor_id != d2.creditor_id
              AND d1.debtor_id != d3.creditor_id
              AND d1.creditor_id != d3.creditor_id
              AND {least_expr} > :min_amount
            ORDER BY clear_amount DESC
            LIMIT 50
            """
        )

        result = await self.session.execute(
            query,
            {
                "equivalent_id": equivalent_id_param,
                "min_amount": min_amount_param,
            },
        )

        cycles: List[List[Dict]] = []
        for row in result:
            cycles.append(
                [
                    {
                        "debt_id": str(row.debt1_id),
                        "debtor": str(row.a),
                        "creditor": str(row.b),
                        "amount": str(row.amt1),
                    },
                    {
                        "debt_id": str(row.debt2_id),
                        "debtor": str(row.b),
                        "creditor": str(row.c),
                        "amount": str(row.amt2),
                    },
                    {
                        "debt_id": str(row.debt3_id),
                        "debtor": str(row.c),
                        "creditor": str(row.d),
                        "amount": str(row.amt3),
                    },
                    {
                        "debt_id": str(row.debt4_id),
                        "debtor": str(row.d),
                        "creditor": str(row.a),
                        "amount": str(row.amt4),
                    },
                ]
            )

        return cycles

    async def _cycle_respects_auto_clearing(self, debts: List[Debt]) -> bool:
        """Return True if every cycle edge has consent for auto clearing.

        For each debt edge debtor->creditor, the controlling trustline is creditor->debtor
        (i.e. the creditor's line of trust/limit towards the debtor).
        """
        if not debts:
            return False

        equivalent_id = debts[0].equivalent_id
        required_pairs: set[tuple[uuid.UUID, uuid.UUID]] = {
            (d.creditor_id, d.debtor_id) for d in debts
        }

        from_ids = {p[0] for p in required_pairs}
        to_ids = {p[1] for p in required_pairs}

        trustlines = (
            (
                await self.session.execute(
                    select(TrustLine).where(
                        and_(
                            TrustLine.equivalent_id == equivalent_id,
                            TrustLine.status == "active",
                            TrustLine.from_participant_id.in_(list(from_ids)),
                            TrustLine.to_participant_id.in_(list(to_ids)),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], TrustLine] = {
            (tl.from_participant_id, tl.to_participant_id): tl for tl in trustlines
        }

        for from_id, to_id in required_pairs:
            tl = tl_by_pair.get((from_id, to_id))
            if tl is None:
                return False
            if not self._policy_flag(tl.policy, "auto_clearing", default=True):
                return False

        return True

    @staticmethod
    def _policy_flag(policy: dict | None, key: str, *, default: bool) -> bool:
        """Parse a boolean flag from a policy JSON blob.

        SQLite JSON handling can surface values as strings in some flows.
        We treat common falsy string forms as False.
        """
        if policy is None:
            return default

        value = policy.get(key, default)
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"false", "0", "no", "off"}:
                return False
            if v in {"true", "1", "yes", "on"}:
                return True
            return default

        return bool(value)

    async def _filter_cycles_by_auto_clearing_policy_sql(
        self, cycles: List[List[Dict]], *, equivalent_id: uuid.UUID
    ) -> List[List[Dict]]:
        """Filter SQL-produced candidate cycles by auto-clearing consent.

        Important: SQL cycle detectors don't apply policy constraints. If we return
        cycles that will be skipped at execution time, the clearing loop may stop
        early and never try alternative depths (e.g., quadrangles).
        """

        if not cycles:
            return []

        debt_ids: set[uuid.UUID] = set()
        for cycle in cycles:
            for edge in cycle:
                try:
                    debt_ids.add(uuid.UUID(str(edge.get("debt_id"))))
                except Exception:
                    continue

        if not debt_ids:
            return []

        debts = (
            (
                await self.session.execute(
                    select(Debt).where(Debt.id.in_(list(debt_ids)))
                )
            )
            .scalars()
            .all()
        )
        debts_by_id: dict[uuid.UUID, Debt] = {d.id: d for d in debts}

        # Use the same policy evaluation path as execution time.
        # This is intentionally less optimized than the bulk trustline fetch, but
        # keeps behavior consistent and avoids subtle SQLite/JSON edge cases.
        filtered: List[List[Dict]] = []
        for cycle in cycles:
            cycle_debts: List[Debt] = []
            ok = True
            for edge in cycle:
                try:
                    debt_id = uuid.UUID(str(edge.get("debt_id")))
                except Exception:
                    ok = False
                    break
                debt = debts_by_id.get(debt_id)
                if debt is None:
                    ok = False
                    break
                cycle_debts.append(debt)

            if not ok or not cycle_debts:
                continue
            if cycle_debts[0].equivalent_id != equivalent_id:
                continue
            if await self._cycle_respects_auto_clearing(cycle_debts):
                filtered.append(cycle)

        return filtered

    async def _locked_pairs_for_equivalent(
        self, equivalent_id: uuid.UUID
    ) -> Set[frozenset[uuid.UUID]]:
        """Return participant pairs that must not be touched by clearing.

        For MVP safety we treat any active prepared payment flow `from->to` as a lock on the unordered
        participant pair {from, to}. Clearing must not modify debts between these participants.
        """
        stmt = select(PrepareLock).where(PrepareLock.expires_at > func.now())
        locks = (await self.session.execute(stmt)).scalars().all()

        locked: Set[frozenset[uuid.UUID]] = set()
        for lock in locks:
            for flow in (lock.effects or {}).get("flows", []):
                try:
                    eq_id = uuid.UUID(str(flow.get("equivalent")))
                    if eq_id != equivalent_id:
                        continue
                    from_id = uuid.UUID(str(flow.get("from")))
                    to_id = uuid.UUID(str(flow.get("to")))
                except Exception:
                    continue

                locked.add(frozenset({from_id, to_id}))

        return locked

    async def find_cycles(
        self, equivalent_code: str, max_depth: int = 6
    ) -> List[List[Dict]]:
        """
        Find closed cycles of debts for a given equivalent.
        Returns list of cycles, where each cycle is a list of Debt objects (or dicts representing edges).

        Algorithm:
        1. Load all debts for this equivalent into memory (Graph).
           For MVP (small scale), this is feasible. For production, we need more optimized graph DB or targeted search.
        2. Perform DFS/BFS to find cycles.
        """
        logger.info(
            "event=clearing.find_cycles equivalent=%s max_depth=%s",
            equivalent_code,
            max_depth,
        )
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="start").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.start",
                exc_info=True,
            )

        equivalent = (
            await self.session.execute(
                select(Equivalent).where(Equivalent.code == equivalent_code)
            )
        ).scalar_one_or_none()
        if not equivalent:
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="find_cycles", result="not_found"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.not_found",
                    exc_info=True,
                )
            raise GeoException(f"Equivalent {equivalent_code} not found")

        # FIX-012: Prefer SQL JOIN based search for short cycles (3–4) when running with a real AsyncSession.
        use_sql = isinstance(self.session, AsyncSession)
        if use_sql and max_depth >= 3:
            locked_pairs = await self._locked_pairs_for_equivalent(equivalent.id)
            cycles: List[List[Dict]] = []
            try:
                cycles = await self.find_triangles_sql(equivalent.id)
                if cycles and locked_pairs:
                    filtered: List[List[Dict]] = []
                    for cycle in cycles:
                        skip = False
                        for edge in cycle:
                            try:
                                debtor_id = uuid.UUID(str(edge.get("debtor")))
                                creditor_id = uuid.UUID(str(edge.get("creditor")))
                            except Exception:
                                continue
                            if frozenset({debtor_id, creditor_id}) in locked_pairs:
                                skip = True
                                break
                        if not skip:
                            filtered.append(cycle)
                    cycles = filtered

                cycles = self._deduplicate_cycles(cycles)

                if cycles:
                    cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                        cycles, equivalent_id=equivalent.id
                    )

                # If triangles exist but are all filtered out (policy/locks), try quadrangles.
                if max_depth >= 4 and not cycles:
                    cycles = await self.find_quadrangles_sql(equivalent.id)
                    if cycles and locked_pairs:
                        filtered = []
                        for cycle in cycles:
                            skip = False
                            for edge in cycle:
                                try:
                                    debtor_id = uuid.UUID(str(edge.get("debtor")))
                                    creditor_id = uuid.UUID(str(edge.get("creditor")))
                                except Exception:
                                    continue
                                if frozenset({debtor_id, creditor_id}) in locked_pairs:
                                    skip = True
                                    break
                            if not skip:
                                filtered.append(cycle)
                        cycles = filtered

                    cycles = self._deduplicate_cycles(cycles)

                    if cycles:
                        cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                            cycles, equivalent_id=equivalent.id
                        )
            except Exception:
                logger.warning(
                    "event=clearing.find_cycles_sql_failed equivalent=%s",
                    equivalent_code,
                    exc_info=True,
                )
                cycles = []

            if cycles:
                # Replace UUIDs with PIDs for consistency with existing output.
                participant_ids: Set[uuid.UUID] = set()
                for cycle in cycles:
                    for edge in cycle:
                        try:
                            participant_ids.add(uuid.UUID(str(edge["debtor"])))
                            participant_ids.add(uuid.UUID(str(edge["creditor"])))
                        except Exception:
                            pass

                pid_by_id: Dict[uuid.UUID, str] = {}
                if participant_ids:
                    participants = (
                        (
                            await self.session.execute(
                                select(Participant).where(
                                    Participant.id.in_(list(participant_ids))
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    pid_by_id = {p.id: p.pid for p in participants}

                for cycle in cycles:
                    for edge in cycle:
                        try:
                            debtor_uuid = uuid.UUID(str(edge["debtor"]))
                            creditor_uuid = uuid.UUID(str(edge["creditor"]))
                            edge["debtor"] = str(
                                pid_by_id.get(debtor_uuid, debtor_uuid)
                            )
                            edge["creditor"] = str(
                                pid_by_id.get(creditor_uuid, creditor_uuid)
                            )
                        except Exception:
                            pass

                return cycles

        # 1. Load Graph
        # Node: Participant ID
        # Edge: Debt (debtor -> creditor, amount)
        stmt = select(Debt).where(
            and_(Debt.equivalent_id == equivalent.id, Debt.amount > 0)
        )
        all_debts = (await self.session.execute(stmt)).scalars().all()

        # Exclude edges that are involved in active prepared payments.
        locked_pairs = await self._locked_pairs_for_equivalent(equivalent.id)
        if locked_pairs:
            all_debts = [
                d
                for d in all_debts
                if frozenset({d.debtor_id, d.creditor_id}) not in locked_pairs
            ]

        adjacency: Dict[uuid.UUID, List[Debt]] = {}
        for d in all_debts:
            if d.debtor_id not in adjacency:
                adjacency[d.debtor_id] = []
            adjacency[d.debtor_id].append(d)

        # Build UUID -> PID mapping for participants in this graph.
        participant_ids: Set[uuid.UUID] = set()
        for d in all_debts:
            participant_ids.add(d.debtor_id)
            participant_ids.add(d.creditor_id)

        pid_by_id: Dict[uuid.UUID, str] = {}
        if participant_ids:
            participants = (
                (
                    await self.session.execute(
                        select(Participant).where(
                            Participant.id.in_(list(participant_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            pid_by_id = {p.id: p.pid for p in participants}

        # 2. Find Cycles
        # We look for simple cycles.
        cycles = []

        # To avoid duplicates (e.g. A->B->C->A vs B->C->A->B), we can enforce ordering or use set of sets.
        # Simple DFS with path tracking.

        def dfs(
            start_node: uuid.UUID,
            current_node: uuid.UUID,
            path: List[Debt],
            visited_in_path: Set[uuid.UUID],
        ):
            # `max_depth` is the maximum number of edges in the resulting cycle.
            # If we already have `max_depth` edges in the path, we can't extend it.
            if len(path) >= max_depth:
                return

            if current_node not in adjacency:
                return

            for edge in adjacency[current_node]:
                neighbor = edge.creditor_id

                if neighbor == start_node:
                    # Cycle found!
                    cycles.append(path + [edge])
                    return

                if neighbor not in visited_in_path:
                    dfs(
                        start_node,
                        neighbor,
                        path + [edge],
                        visited_in_path | {neighbor},
                    )

        # Run DFS from each node.
        # Optimization: Remove nodes that cannot be part of a cycle (in-degree=0 or out-degree=0).
        # Optimization: Once a cycle is found, we might want to "consume" it?
        # But here we just LIST them.

        # We need to avoid finding same cycle multiple times starting from different nodes.
        # Canonization: Cycle is represented by min(node_id) as start?

        nodes = list(adjacency.keys())
        # Sort for determinism
        # nodes.sort()

        # We need a robust cycle finder.
        # NetworkX is good but adding dependency? Let's keep it simple custom DFS.
        # Since we want to find *any* cycle to clear, we don't need *all* cycles.

        unique_cycles_hashes = set()

        # Let's retry simple approach:
        # Iterate all nodes. If node not visited globally (optional optimization?), start DFS.
        # Actually finding ALL cycles in a graph is NP-hard (or exponential).
        # We usually want "Shortest Cycle" or "Any Cycle".

        # Let's implement finding ONE cycle per run? Or a few.
        # Clearing usually iterates: Find Cycle -> Clear -> Repeat.

        # Heuristic: Start from nodes with Debts.
        for start_node in nodes:
            # Limit search
            if len(cycles) > 50:
                break

            dfs(start_node, start_node, [], {start_node})

        # Filter duplicates
        final_cycles = []
        for cycle in cycles:
            # cycle is list of Debt objects
            # Signature: sorted list of debt IDs?
            ids = sorted([d.id for d in cycle])
            h = tuple(ids)
            if h not in unique_cycles_hashes:
                unique_cycles_hashes.add(h)

                # Format for output
                cycle_data = []
                for edge in cycle:
                    cycle_data.append(
                        {
                            "debt_id": str(edge.id),
                            "debtor": str(
                                pid_by_id.get(edge.debtor_id, edge.debtor_id)
                            ),
                            "creditor": str(
                                pid_by_id.get(edge.creditor_id, edge.creditor_id)
                            ),
                            "amount": str(edge.amount),
                        }
                    )
                final_cycles.append(cycle_data)

        # Prefer shorter cycles first for auto_clear().
        final_cycles.sort(key=len)

        logger.info(
            "event=clearing.find_cycles_done equivalent=%s cycles=%s",
            equivalent_code,
            len(final_cycles),
        )
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="success").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=find_cycles.success",
                exc_info=True,
            )
        if final_cycles:
            final_cycles = await self._filter_cycles_by_auto_clearing_policy_sql(
                final_cycles, equivalent_id=equivalent.id
            )
        return final_cycles

    async def execute_clearing(self, cycle: List[Dict]) -> bool:
        """Backward-compatible API: execute clearing and return success flag."""
        return (await self.execute_clearing_with_amount(cycle)) is not None

    async def execute_clearing_with_amount(self, cycle: List[Dict]) -> Decimal | None:
        """Execute clearing for a specific cycle and return the *actual* cleared amount.

        Returns:
        - Decimal amount on success (the min debt amount among the locked cycle edges at execution time)
        - None on failure / skipped.
        """
        if not cycle:
            return None

        logger.info("event=clearing.execute cycle_len=%s", len(cycle))
        try:
            CLEARING_EVENTS_TOTAL.labels(event="execute", result="start").inc()
        except Exception:
            logger.debug(
                "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.start",
                exc_info=True,
            )

        # Load debts for this cycle to avoid relying on debtor/creditor fields in the API output.
        try:
            debt_ids = [uuid.UUID(str(edge["debt_id"])) for edge in cycle]
        except Exception:
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="execute", result="bad_request"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.bad_request",
                    exc_info=True,
                )
            return None

        debts = (
            (
                await self.session.execute(
                    select(Debt).where(Debt.id.in_(debt_ids)).with_for_update()
                )
            )
            .scalars()
            .all()
        )

        if len(debts) != len(debt_ids):
            return None

        # 1. Determine clearing amount (min amount in cycle)
        clear_amount = min([d.amount for d in debts])

        if clear_amount <= 0:
            return None

        logger.info(
            "event=clearing.execute_ready cycle_len=%s amount=%s",
            len(cycle),
            clear_amount,
        )

        # Reject cycles that touch any edge reserved by active PrepareLocks.
        locked_pairs = await self._locked_pairs_for_equivalent(debts[0].equivalent_id)
        if locked_pairs:
            for d in debts:
                pair = frozenset({d.debtor_id, d.creditor_id})
                if pair in locked_pairs:
                    logger.info("event=clearing.skip_locked cycle_len=%s", len(cycle))
                    try:
                        CLEARING_EVENTS_TOTAL.labels(
                            event="execute", result="skip_locked"
                        ).inc()
                    except Exception:
                        logger.debug(
                            "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.skip_locked",
                            exc_info=True,
                        )
                    return None

        # FIX-017: enforce auto_clearing policy on every edge in the cycle.
        if not await self._cycle_respects_auto_clearing(debts):
            logger.info("event=clearing.skip_policy cycle_len=%s", len(cycle))
            try:
                CLEARING_EVENTS_TOTAL.labels(
                    event="execute", result="skip_policy"
                ).inc()
            except Exception:
                logger.debug(
                    "event=clearing.metrics_inc_failed metric=CLEARING_EVENTS_TOTAL label=execute.skip_policy",
                    exc_info=True,
                )
            return None

        # FIX-011: capture net positions BEFORE clearing (clearing neutrality invariant).
        checker = InvariantChecker(self.session)
        participant_ids: Set[uuid.UUID] = set()
        for d in debts:
            participant_ids.add(d.debtor_id)
            participant_ids.add(d.creditor_id)

        # FIX-025: enrich CLEARING transaction payload for traceability.
        equivalent = (
            await self.session.execute(
                select(Equivalent).where(Equivalent.id == debts[0].equivalent_id)
            )
        ).scalar_one_or_none()

        pid_by_id: Dict[uuid.UUID, str] = {}
        if participant_ids:
            participants = (
                (
                    await self.session.execute(
                        select(Participant).where(
                            Participant.id.in_(list(participant_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            pid_by_id = {p.id: p.pid for p in participants}

        debts_by_id: Dict[uuid.UUID, Debt] = {d.id: d for d in debts}
        edges_payload: List[Dict[str, str]] = []
        for edge in cycle:
            try:
                edge_debt_id = uuid.UUID(str(edge.get("debt_id")))
            except Exception:
                continue

            debt = debts_by_id.get(edge_debt_id)
            if debt is None:
                continue

            edges_payload.append(
                {
                    "debt_id": str(debt.id),
                    "debtor": str(pid_by_id.get(debt.debtor_id, debt.debtor_id)),
                    "creditor": str(pid_by_id.get(debt.creditor_id, debt.creditor_id)),
                    "amount": str(clear_amount),
                }
            )

        positions_before: Dict[uuid.UUID, Decimal] = {}
        for pid in participant_ids:
            positions_before[pid] = await checker._calculate_net_position(
                pid, debts[0].equivalent_id
            )

        checkpoint_before = None
        try:
            checkpoint_before = await compute_integrity_checkpoint_for_equivalent(
                self.session,
                equivalent_id=debts[0].equivalent_id,
            )
        except Exception:
            checkpoint_before = None

        # 2. Create Transaction (CLEARING)
        # We need an initiator? System or one of participants.
        # Let's pick the first debtor.
        initiator_id = debts[0].debtor_id

        tx_uuid = uuid.uuid4()
        tx_id_str = str(tx_uuid)

        new_tx = Transaction(
            id=tx_uuid,
            tx_id=tx_id_str,
            type="CLEARING",
            initiator_id=initiator_id,
            payload={
                # Backward-compatible fields.
                "cycle": [str(e["debt_id"]) for e in cycle],
                "amount": str(clear_amount),
                # Enriched fields for audit/debugging.
                "equivalent": str(
                    equivalent.code if equivalent else debts[0].equivalent_id
                ),
                "edges": edges_payload,
            },
            state="NEW",
        )
        self.session.add(new_tx)

        try:
            # 3. Apply changes (Decrease debts)
            # We must lock rows? Or just update.
            # Since we are in a transaction, we should select for update ideally.
            # For MVP, we just update.

            for debt in debts:
                if debt.amount < clear_amount:
                    raise GeoException(f"Debt {debt.id} amount changed during clearing")

                debt.amount -= clear_amount
                if debt.amount == 0:
                    await self.session.delete(debt)
                else:
                    self.session.add(debt)

            await self.session.flush()

            checkpoint_after = None
            try:
                checkpoint_after = await compute_integrity_checkpoint_for_equivalent(
                    self.session,
                    equivalent_id=debts[0].equivalent_id,
                )
            except Exception:
                checkpoint_after = None

            try:
                before_sum = checkpoint_before.checksum if checkpoint_before else ""
                after_sum = (
                    checkpoint_after.checksum if checkpoint_after else before_sum
                )
                invariants_status = (
                    (checkpoint_after.invariants_status or {})
                    if checkpoint_after
                    else {}
                )
                passed = bool(invariants_status.get("passed", True))

                self.session.add(
                    IntegrityAuditLog(
                        operation_type="CLEARING",
                        tx_id=tx_id_str,
                        equivalent_code=str(
                            equivalent.code if equivalent else debts[0].equivalent_id
                        ),
                        state_checksum_before=before_sum,
                        state_checksum_after=after_sum,
                        affected_participants={
                            "participants": [
                                str(pid_by_id.get(p, p)) for p in participant_ids
                            ],
                            "edges": edges_payload,
                        },
                        invariants_checked=invariants_status.get("checks")
                        or invariants_status,
                        verification_passed=passed,
                        error_details=None if passed else invariants_status,
                    )
                )
            except Exception:
                # Best-effort; clearing must not fail due to audit logging.
                pass

            # Verify neutrality AFTER applying changes (must be within the same DB transaction).
            await checker.verify_clearing_neutrality(
                list(participant_ids),
                debts[0].equivalent_id,
                positions_before,
            )

            # 4. Commit
            new_tx.state = "COMMITTED"
            self.session.add(new_tx)
            await self.session.commit()

            # Debts changed: invalidate any TTL routing graph cache.
            try:
                eq_code = str(equivalent.code if equivalent else "")
                if eq_code:
                    PaymentRouter.invalidate_cache(eq_code)
            except Exception:
                pass

            logger.info("event=clearing.committed tx_id=%s", tx_id_str)
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="success").inc()
            except Exception:
                pass
            return clear_amount

        except Exception as e:
            logger.error("event=clearing.failed error=%s", str(e))
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="error").inc()
            except Exception:
                pass
            await self.session.rollback()
            # Log failed tx?
            return None

    async def auto_clear(self, equivalent_code: str, *, max_depth: int = 6) -> int:
        """
        Run clearing loop.
        Returns number of cleared cycles.
        """
        count = 0
        while True:
            cycles = await self.find_cycles(equivalent_code, max_depth=max_depth)
            if not cycles:
                break

            # Try cycles until one succeeds. If all candidates fail (e.g. due to locks/concurrency), stop.
            executed = False
            for cycle in cycles:
                success = await self.execute_clearing(cycle)
                if success:
                    count += 1
                    executed = True
                    break

            if not executed:
                break

            if count > 100:  # Safety break
                break

        return count
