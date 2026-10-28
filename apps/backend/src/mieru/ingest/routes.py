from fastapi import APIRouter, Request, Response

from mieru.core import health, redis

router = APIRouter()


@router.get("/health")
async def liveness() -> dict[str, str]:
    """The process is up and serving"""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, health.Status]:
    """Redis answers"""
    state = request.app.state
    results = await health.check({"redis": lambda: redis.ping(state.redis)})
    if "error" in results.values():
        response.status_code = 503
    return results


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    """The git commit this process was built from"""
    return {"version": request.app.state.settings.git_sha}
