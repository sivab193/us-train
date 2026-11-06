from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app


class FakeRepository:
    async def ping(self) -> None:
        return None

    async def current_data_as_of(self) -> str:
        return "2026-11-05"

    async def count_stations(self, query: str | None) -> int:
        return 1

    async def search_stations(self, query: str | None, page: int, page_size: int) -> list[dict[str, Any]]:
        return [{"id": "CHI", "code": "CHI", "name": "Chicago", "lat": 41.88, "lon": -87.64}]

    async def resolve_station_id(self, value: str) -> str | None:
        return "CHI" if value.upper() == "CHI" else None

    async def get_station(self, station_id: str) -> dict[str, Any] | None:
        return {"id": "CHI", "code": "CHI", "name": "Chicago", "routes": []}

    async def list_routes(self, kind: str | None) -> list[dict[str, Any]]:
        return []

    async def latest_meta(self, timezone: str) -> dict[str, Any]:
        return {"feed": "mdb-11", "data_as_of": "2026-11-05", "timezone": timezone}


SETTINGS = Settings(
    database_url="postgresql://unused",
    port=8080,
    feed_id="mdb-11",
    feed_timezone="America/New_York",
    cors_origins=("*",),
)
APP = create_app(FakeRepository(), SETTINGS)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def get(path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


@pytest.mark.anyio
async def test_health_and_readiness() -> None:
    assert (await get("/health")).json() == {"status": "ok"}
    assert (await get("/ready")).json() == {"status": "ready"}


@pytest.mark.anyio
async def test_station_search_preserves_envelope_and_pagination() -> None:
    response = await get("/api/v1/stations", params={"q": "chicago"})
    assert response.status_code == 200
    body = response.json()
    assert body["feed"] == "mdb-11"
    assert body["data_as_of"] == "2026-11-05"
    assert body["data"]["items"][0]["code"] == "CHI"
    assert body["data"]["has_more"] is False


@pytest.mark.anyio
async def test_station_codes_work_on_detail_endpoint() -> None:
    response = await get("/api/v1/stations/chi")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "CHI"


@pytest.mark.anyio
async def test_unknown_station_returns_not_found() -> None:
    response = await get("/api/v1/stations/unknown")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_route_type_is_validated() -> None:
    response = await get("/api/v1/routes", params={"type": "ferry"})
    assert response.status_code == 400


@pytest.mark.anyio
async def test_meta_uses_feed_timezone() -> None:
    response = await get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json()["data"]["timezone"] == "America/New_York"
