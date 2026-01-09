from fastapi import APIRouter, Depends

from app.api import deps
from app.api.v1 import auth, participants, trustlines, payments, balance, clearing, integrity, equivalents

api_router = APIRouter(dependencies=[Depends(deps.rate_limit)])

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(participants.router, prefix="/participants", tags=["Participants"])
api_router.include_router(trustlines.router, prefix="/trustlines", tags=["TrustLines"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(balance.router, prefix="/balance", tags=["Balance"])
api_router.include_router(clearing.router, prefix="/clearing", tags=["Clearing"])
api_router.include_router(integrity.router, prefix="/integrity", tags=["Integrity"])
api_router.include_router(equivalents.router, prefix="/equivalents", tags=["Equivalents"])