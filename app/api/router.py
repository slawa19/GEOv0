from fastapi import APIRouter
from app.api.v1 import auth, participants, trustlines, payments, balance, clearing

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(participants.router, prefix="/participants", tags=["Participants"])
api_router.include_router(trustlines.router, prefix="/trustlines", tags=["TrustLines"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(balance.router, tags=["Balance"])
api_router.include_router(clearing.router, tags=["Clearing"])