"""FastAPI application preserving the Phase 1 REST contract."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError
from psycopg_pool import AsyncConnectionPool

from .config import Settings
from .gtfs_time import parse_time
from .repository import Repository

logger = logging.getLogger("train_api")


def _paged(items: list[dict[str, Any]], page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size + len(items) < total,
    }


def create_app(repository: Any | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if repository is not None:
            app.state.repository = repository
            yield
            return
        pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"autocommit": True},
        )
        await pool.open(wait=True, timeout=15)
        app.state.repository = Repository(pool)
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(
        title="train.us.siv19.dev",
        version="1.0.0",
        description="Amtrak GTFS schedule-search API",
        lifespan=lifespan,
    )
    if repository is not None:
        app.state.repository = repository
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.exception_handler(PsycopgError)
    async def database_error(_request: Request, exc: PsycopgError) -> JSONResponse:
        logger.exception("database error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": "db_error", "message": "database error"},
        )

    def repo(request: Request) -> Any:
        return request.app.state.repository

    async def envelope(request: Request, data: Any) -> dict[str, Any]:
        return {
            "data": data,
            "data_as_of": await repo(request).current_data_as_of(),
            "feed": settings.feed_id,
        }

    def service_date(value: str | None) -> date:
        if not value:
            return datetime.now(ZoneInfo(settings.feed_timezone)).date()
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "message": f"invalid date '{value}', expected YYYY-MM-DD"},
            ) from exc

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, str]:
        await repo(request).ping()
        return {"status": "ready"}

    @app.get("/api/v1/stations")
    async def search_stations(
        request: Request,
        q: str | None = None,
        page: int = Query(0, ge=0),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        total = await repo(request).count_stations(q)
        items = await repo(request).search_stations(q, page, page_size)
        return await envelope(request, _paged(items, page, page_size, total))

    @app.get("/api/v1/stations/{station_id}")
    async def get_station(request: Request, station_id: str) -> dict[str, Any]:
        resolved = await repo(request).resolve_station_id(station_id)
        if not resolved:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"station {station_id}"})
        station = await repo(request).get_station(resolved)
        if not station:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"station {station_id}"})
        return await envelope(request, station)

    @app.get("/api/v1/stations/{station_id}/departures")
    async def departures(
        request: Request,
        station_id: str,
        date_value: str | None = Query(None, alias="date"),
        time_value: str | None = Query(None, alias="time"),
        page: int = Query(0, ge=0),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        resolved = await repo(request).resolve_station_id(station_id)
        if not resolved:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"station {station_id}"})
        try:
            after = parse_time(time_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "bad_request", "message": str(exc)}) from exc
        day = service_date(date_value)
        total = await repo(request).count_departures(resolved, day, after)
        items = await repo(request).departures(resolved, day, after, page, page_size)
        return await envelope(request, _paged(items, page, page_size, total))

    @app.get("/api/v1/routes")
    async def routes(request: Request, route_type: str | None = Query(None, alias="type")) -> dict[str, Any]:
        if route_type not in (None, "rail", "bus"):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "message": f"type must be 'rail' or 'bus', got '{route_type}'"},
            )
        return await envelope(request, await repo(request).list_routes(route_type))

    @app.get("/api/v1/routes/{route_id}")
    async def get_route(request: Request, route_id: str) -> dict[str, Any]:
        route = await repo(request).get_route(route_id)
        if not route:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"route {route_id}"})
        return await envelope(request, route)

    @app.get("/api/v1/between")
    async def between(
        request: Request,
        from_station: str = Query(..., alias="from"),
        to_station: str = Query(..., alias="to"),
        date_value: str | None = Query(None, alias="date"),
    ) -> dict[str, Any]:
        origin = await repo(request).resolve_station_id(from_station)
        destination = await repo(request).resolve_station_id(to_station)
        if not origin:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"station {from_station}"})
        if not destination:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"station {to_station}"})
        trains = await repo(request).between(origin, destination, service_date(date_value))
        return await envelope(request, trains)

    @app.get("/api/v1/trips/{trip_id}")
    async def get_trip(request: Request, trip_id: str) -> dict[str, Any]:
        trip = await repo(request).get_trip(trip_id)
        if not trip:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"trip {trip_id}"})
        return await envelope(request, trip)

    @app.get("/api/v1/meta")
    async def meta(request: Request) -> dict[str, Any]:
        data = await repo(request).latest_meta(settings.feed_timezone)
        if not data:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "no feed ingested yet"})
        return await envelope(request, data)

    return app


app = create_app()
