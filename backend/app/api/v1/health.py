import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/live", summary="Process liveness")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Dependency readiness")
async def readiness(request: Request) -> JSONResponse:
    engine: AsyncEngine | None = getattr(request.app.state, "db_engine", None)
    redis_client: Redis | None = getattr(request.app.state, "redis", None)
    checks = {
        "database": await _check_database(engine),
        "redis": await _check_redis(redis_client),
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": {
                name: "ok" if available else "unavailable"
                for name, available in checks.items()
            },
        },
    )


async def _check_database(engine: AsyncEngine | None) -> bool:
    if engine is None:
        return False
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Readiness check failed for database")
        return False


async def _check_redis(redis_client: Redis | None) -> bool:
    if redis_client is None:
        return False
    try:
        return bool(await redis_client.ping())
    except Exception:
        logger.warning("Readiness check failed for redis")
        return False
