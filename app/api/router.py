from fastapi import APIRouter, Depends

from app.api import deps
from app.api.v1 import auth, participants, trustlines, payments, balance, clearing, integrity, equivalents, health, admin, websocket, simulator

api_router = APIRouter()

_http_deps = [Depends(deps.rate_limit)]

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"], dependencies=_http_deps)
api_router.include_router(participants.router, prefix="/participants", tags=["Participants"], dependencies=_http_deps)
api_router.include_router(trustlines.router, prefix="/trustlines", tags=["TrustLines"], dependencies=_http_deps)
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"], dependencies=_http_deps)
api_router.include_router(balance.router, prefix="/balance", tags=["Balance"], dependencies=_http_deps)
api_router.include_router(clearing.router, prefix="/clearing", tags=["Clearing"], dependencies=_http_deps)
api_router.include_router(integrity.router, prefix="/integrity", tags=["Integrity"], dependencies=_http_deps)
api_router.include_router(equivalents.router, prefix="/equivalents", tags=["Equivalents"], dependencies=_http_deps)
api_router.include_router(health.router, tags=["Health"], dependencies=_http_deps)
api_router.include_router(admin.router, tags=["Admin"], dependencies=_http_deps)
api_router.include_router(simulator.router, tags=["Simulator"], dependencies=_http_deps)
api_router.include_router(websocket.router, tags=["WebSocket"])