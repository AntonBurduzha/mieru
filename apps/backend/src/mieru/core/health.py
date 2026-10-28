import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

Probe = Callable[[], Awaitable[None]]
Status = Literal["ok", "error"]

PROBE_TIMEOUT_S = 2.0


async def _run(name: str, probe: Probe) -> Status:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            await probe()
    except Exception as exc:
        log.warning("dependency_unavailable", dependency=name, error_type=type(exc).__name__)
        return "error"
    return "ok"


async def check(probes: Mapping[str, Probe]) -> dict[str, Status]:
    """Run every probe at the same time and report ok/error per dependency."""
    results = await asyncio.gather(*(_run(name, probe) for name, probe in probes.items()))
    return dict(zip(probes, results, strict=True))
