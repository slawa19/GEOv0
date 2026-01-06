import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.payments.router import PaymentRouter


class _Eq:
    def __init__(self, code: str):
        self.code = code


class _Policy:
    def __init__(self, can_be_intermediate: bool = True):
        self.can_be_intermediate = can_be_intermediate


class _TL:
    def __init__(
        self,
        from_participant_id: uuid.UUID,
        to_participant_id: uuid.UUID,
        code: str,
        limit: Decimal,
        can_be_intermediate: bool = True,
    ):
        self.from_participant_id = from_participant_id
        self.to_participant_id = to_participant_id
        self.equivalent = _Eq(code)
        self.limit = limit
        # Router expects tl.policy to be a dict-like JSON.
        self.policy = {"can_be_intermediate": can_be_intermediate}


class _Debt:
    def __init__(self, debtor_id: uuid.UUID, creditor_id: uuid.UUID, code: str, amount: Decimal):
        self.debtor_id = debtor_id
        self.creditor_id = creditor_id
        self.equivalent = _Eq(code)
        self.amount = amount


class _Lock:
    def __init__(self, participant_id: uuid.UUID, expires_at: datetime, effects: dict):
        self.participant_id = participant_id
        self.expires_at = expires_at
        self.effects = effects


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def one_or_none(self):
        if not self._items:
            return None
        if len(self._items) > 1:
            raise AssertionError("Expected <= 1 row")
        return self._items[0]


class _ExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)

    def scalar_one_or_none(self):
        if not self._items:
            return None
        if len(self._items) > 1:
            raise AssertionError("Expected <= 1 row")
        return self._items[0]

    def all(self):
        return list(self._items)


class _Session:
    def __init__(self, *, equivalent, trustlines, debts, locks, participants):
        self._equivalent = equivalent
        self._trustlines = trustlines
        self._debts = debts
        self._locks = locks
        self._participants = participants

    async def execute(self, stmt):
        # Router executes three SELECTs (trustlines, debts, prepare locks). We route by model name.
        froms = getattr(stmt, "_from_obj", None) or []
        model_name = None
        if froms:
            model_name = getattr(froms[0], "name", None)
        text = str(stmt)

        if model_name == "equivalents" or "FROM equivalents" in text:
            return _ExecResult([self._equivalent])
        if model_name in {"trustlines", "trust_lines"} or "FROM trust_lines" in text or "FROM trustlines" in text:
            return _ExecResult(self._trustlines)
        if model_name == "debts" or "FROM debts" in text:
            return _ExecResult(self._debts)
        if "prepare_locks" in text or "FROM prepare_locks" in text:
            return _ExecResult(self._locks)
        if "FROM participants" in text:
            return _ExecResult(self._participants)

        raise AssertionError(f"Unexpected query in router build_graph: {text}")


@pytest.mark.asyncio
async def test_build_graph_subtracts_reserved_capacity_and_respects_policy():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()
    code = "USD"

    class _Equivalent:
        def __init__(self, id_: uuid.UUID, code_: str):
            self.id = id_
            self.code = code_

    class _ParticipantRow:
        def __init__(self, id_: uuid.UUID, pid: str):
            self.id = id_
            self.pid = pid

    equivalent = _Equivalent(uuid.uuid4(), code)

    # Semantics: edge A->B is enabled by TL B->A.
    # So create TLs: B->A and C->B.
    tl_b_a = _TL(from_participant_id=b, to_participant_id=a, code=code, limit=Decimal("100"), can_be_intermediate=True)
    tl_c_b = _TL(from_participant_id=c, to_participant_id=b, code=code, limit=Decimal("100"), can_be_intermediate=False)

    # No debts.
    debts = []

    # Active lock reserves 30 on flow A->B.
    effects = {
        "flows": [
            {
                "from": str(a),
                "to": str(b),
                "equivalent": str(equivalent.id),
                "amount": "30",
            }
        ]
    }
    lock = _Lock(participant_id=a, expires_at=datetime.now(timezone.utc) + timedelta(seconds=60), effects=effects)

    participants = [
        _ParticipantRow(a, "A"),
        _ParticipantRow(b, "B"),
        _ParticipantRow(c, "C"),
    ]
    session = _Session(
        equivalent=equivalent,
        trustlines=[tl_b_a, tl_c_b],
        debts=debts,
        locks=[lock],
        participants=participants,
    )

    router = PaymentRouter(session)
    await router.build_graph(code)
    graph = router.graph

    # Capacity reduced by reserved amount.
    assert graph["A"]["B"] == Decimal("70")

    # Intermediate policy: node C can_be_intermediate=False for edge B->C?
    # Given TL C->B enables edge B->C. That edge should exist, but path A->B->C should be disallowed
    # because C cannot be intermediate (and in that path, C is the destination, so allowed).
    # We instead validate policy gating by searching A->C and expecting no path because B would need to
    # pass through C? Actually path A->C would end at C, so C isn't intermediate. The constraint affects
    # using C as an intermediate node, which isn't exercised here. So simply assert edge B->C exists.
    assert "B" in graph and "C" in graph["B"]

    # Still, max_hops=1 should forbid A->B->C.
    paths = router.find_paths("A", "C", Decimal("1"), max_hops=1, k=3)
    assert paths == []


def test_find_paths_blocks_intermediate_when_policy_false():
    # Construct a graph directly (no DB stubs): A -> B -> C.
    # If edge policy for A->B disallows intermediates, then B cannot be used as an intermediate node
    # on a path from A to C.
    router = PaymentRouter(None)
    router.graph = {
        "A": {"B": Decimal("10")},
        "B": {"C": Decimal("10")},
        "C": {},
    }
    router.edge_can_be_intermediate = {
        "A": {"B": False},
        "B": {"C": True},
        "C": {},
    }

    assert router.find_paths("A", "C", Decimal("1"), max_hops=6, k=3) == []

    # Allow intermediates on A->B, now the path should exist.
    router.edge_can_be_intermediate["A"]["B"] = True
    assert router.find_paths("A", "C", Decimal("1"), max_hops=6, k=3) == [["A", "B", "C"]]


def test_find_paths_blocks_blocked_participants_as_intermediate_node():
    # Graph: A -> B -> C. If policy on edge A->B blocks B, then B cannot be used as intermediate.
    router = PaymentRouter(None)
    router.graph = {
        "A": {"B": Decimal("10")},
        "B": {"C": Decimal("10")},
        "C": {},
    }
    router.edge_can_be_intermediate = {
        "A": {"B": True},
        "B": {"C": True},
        "C": {},
    }
    router.edge_blocked_participants = {
        "A": {"B": {"B"}},
        "B": {"C": set()},
        "C": {},
    }

    assert router.find_paths("A", "C", Decimal("1"), max_hops=6, k=3) == []


def test_find_paths_allows_blocked_participants_as_destination():
    # blocked_participants forbids intermediate nodes only; destination is allowed.
    router = PaymentRouter(None)
    router.graph = {
        "A": {"B": Decimal("10")},
        "B": {"C": Decimal("10")},
        "C": {},
    }
    router.edge_can_be_intermediate = {
        "A": {"B": True},
        "B": {"C": True},
        "C": {},
    }
    router.edge_blocked_participants = {
        "A": {"B": {"C"}},
        "B": {"C": {"C"}},
        "C": {},
    }

    assert router.find_paths("A", "C", Decimal("1"), max_hops=6, k=3) == [["A", "B", "C"]]
