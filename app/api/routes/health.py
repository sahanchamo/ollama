from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["operations"])


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
    checks: dict[str, bool] = {"database": False, "redis": False, "ollama": False}
    try:
        async with request.app.state.session_factory() as db:
            await db.execute(text("SELECT 1"))
            checks["database"] = True
    except Exception:
        pass
    try:
        checks["redis"] = bool(await request.app.state.redis.ping())
    except Exception:
        pass
    checks["ollama"] = await request.app.state.ollama.health()
    healthy = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )

