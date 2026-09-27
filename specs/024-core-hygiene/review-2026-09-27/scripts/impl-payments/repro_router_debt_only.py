"""Router vs core capacity: A is owed 50 by B (A trusts B, no line B->A). Can A pay B 30?"""
import asyncio, os, sys, uuid
from decimal import Decimal
from types import SimpleNamespace as NS
sys.path.insert(0, r"<repo>")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@127.0.0.1:1/none")
from app.core.payments.router import PaymentRouter

A, B, EQ = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
line_A_trusts_B = NS(from_participant_id=A, to_participant_id=B, limit=Decimal("100"), policy=None)
debt_B_owes_A = NS(debtor_id=B, creditor_id=A, amount=Decimal("50"))

class R:
    def __init__(self, v): self.v = v
    def scalar_one_or_none(self): return self.v
    def scalars(self): return NS(all=lambda: self.v)
    def all(self): return self.v

class S:
    def __init__(self): self.n = 0
    async def execute(self, stmt):
        self.n += 1
        return {1: R(NS(id=EQ, code="UAH")), 2: R([line_A_trusts_B]), 3: R([debt_B_owes_A]),
                4: R([NS(id=A, pid="A"), NS(id=B, pid="B")])}[self.n]

async def main():
    r = PaymentRouter(S())
    await r._build_graph_impl("UAH", write_shared_cache=False)
    print("graph:", r.graph)
    print("A->B 30 routes:", r.find_flow_routes("A", "B", Decimal("30")))
    # core formula (PaymentService._segment_capacity): limit(B->A)=0 - A_owes_B(0) + B_owes_A(50)
    print("core _segment_capacity A->B would be:", Decimal("0") - 0 + Decimal("50"))
asyncio.run(main())
