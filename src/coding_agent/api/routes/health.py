import asyncio

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from coding_agent.config import settings
from coding_agent.models.database import get_db
from coding_agent.models.schemas import HealthResponse

router = APIRouter()

REDIS_PROBE_TIMEOUT = 1.0  # seconds; a missing Redis must not stall /health for ~4s


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"

    redis_status = "disabled"
    if settings.RATE_LIMIT_ENABLED:
        try:
            r = redis.from_url(settings.REDIS_URL)
            try:
                await asyncio.wait_for(r.ping(), timeout=REDIS_PROBE_TIMEOUT)
                redis_status = "healthy"
            finally:
                await r.aclose()
        except Exception:
            redis_status = "unhealthy"

    healthy = db_status == "healthy" and redis_status in ("healthy", "disabled")
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version="0.1.0",
        database=db_status,
        redis=redis_status,
    )
