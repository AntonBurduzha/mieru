import asyncio

import pytest

from mieru.core import health


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("refused")


async def hangs() -> None:
    await asyncio.sleep(10)


async def test_reports_each_dependency() -> None:
    assert await health.check({"redis": ok, "postgres": broken}) == {
        "redis": "ok",
        "postgres": "error",
    }


async def test_slow_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.05)
    assert await health.check({"clickhouse": hangs}) == {"clickhouse": "error"}


async def test_probes_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.2)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await health.check({"a": hangs, "b": hangs, "c": hangs})
    assert loop.time() - started < 0.5
