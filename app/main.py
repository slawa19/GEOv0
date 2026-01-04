from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.config import settings
from app.api.router import api_router
from app.utils.exceptions import GeoException

app = FastAPI(title="GEO Hub Backend", debug=settings.DEBUG)

@app.exception_handler(GeoException)
async def geo_exception_handler(request: Request, exc: GeoException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )

app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "ok"}