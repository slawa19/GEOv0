from typing import List
from pydantic import BaseModel

class BalanceEquivalent(BaseModel):
    code: str
    total_debt: str
    total_credit: str
    net_balance: str
    available_to_spend: str
    available_to_receive: str

class BalanceSummary(BaseModel):
    equivalents: List[BalanceEquivalent]

class OutgoingDebt(BaseModel):
    creditor: str
    creditor_name: str
    equivalent: str
    amount: str

class IncomingDebt(BaseModel):
    debtor: str
    debtor_name: str
    equivalent: str
    amount: str

class DebtsDetails(BaseModel):
    outgoing: List[OutgoingDebt]
    incoming: List[IncomingDebt]