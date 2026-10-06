import asyncio

import httpx

from qarz.interface.http import HealthCheck, create_app


def _get_healthz(check: HealthCheck) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(check))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/healthz")

    return asyncio.run(call())


def test_healthz_is_ok_when_the_database_is_reachable() -> None:
    async def reachable() -> bool:
        return True

    response = _get_healthz(reachable)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_down_when_the_database_is_not_reachable() -> None:
    async def unreachable() -> bool:
        return False

    response = _get_healthz(unreachable)
    assert response.status_code == 503
    assert response.json() == {"status": "down"}


def test_healthz_reveals_nothing_when_the_check_raises() -> None:
    async def broken() -> bool:
        raise RuntimeError("password authentication failed for user qd_app at 10.0.0.5")

    response = _get_healthz(broken)
    assert response.status_code == 503
    assert response.json() == {"status": "down"}
    assert "password" not in response.text


def test_no_api_description_is_exposed_yet() -> None:
    async def reachable() -> bool:
        return True

    async def call() -> list[int]:
        transport = httpx.ASGITransport(app=create_app(reachable))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return [(await client.get(path)).status_code for path in ("/docs", "/redoc", "/openapi.json")]

    assert asyncio.run(call()) == [404, 404, 404]
