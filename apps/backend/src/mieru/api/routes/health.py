from fastapi import APIRouter, Request, Response

from mieru.core import clickhouse, health, redis
from mieru.db import session as db

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """The process is up and serving"""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, health.Status]:
    """Postgres, Redis and ClickHouse all answer; 503 lists the ones that do not"""
    state = request.app.state
    results = await health.check(
        {
            "postgres": lambda: db.ping(state.engine),
            "redis": lambda: redis.ping(state.redis),
            "clickhouse": lambda: clickhouse.ping(state.clickhouse),
        }
    )
    if "error" in results.values():
        response.status_code = 503
    return results


@router.get("/api/v1/version")
async def version(request: Request) -> dict[str, str]:
    """The git commit this process was built from"""
    return {"version": request.app.state.settings.git_sha}
