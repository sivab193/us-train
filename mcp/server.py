#!/usr/bin/env python3
"""
FastMCP server for train.us.siv19.dev.

Exposes the Phase 1 schedule queries as MCP tools, served as Streamable HTTP
at /mcp so any MCP client (Claude, Jarvis, other agents) can connect with just
the URL. It is a THIN CLIENT over the REST API: no SQL, no GTFS logic here.
One query implementation (the API); this is one of three ways to reach it.

Tools (architecture §5):
    search_stations     -> GET /api/v1/stations
    get_departures      -> GET /api/v1/stations/{id}/departures
    get_trains_between  -> GET /api/v1/between
    get_trip_stops      -> GET /api/v1/trips/{id}
    get_feed_status     -> GET /api/v1/meta
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

API_BASE_URL = os.environ.get("MCP_API_BASE_URL", "http://localhost:8080").rstrip("/")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
HTTP_TIMEOUT = float(os.environ.get("MCP_HTTP_TIMEOUT", "15"))

mcp = FastMCP("train.us.siv19.dev")


async def _get(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """GET helper against the REST API. Drops None-valued params."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    url = f"{API_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=clean)
        if resp.status_code == 404:
            return {"error": "not_found", "detail": resp.json()}
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def search_stations(query: str, page: int = 0, page_size: int = 20) -> dict:
    """Search Amtrak stations by name or 3-letter code (e.g. 'chicago', 'CHI').

    Returns parent stations (not individual platforms). Use the returned
    station `id` or `code` with the other tools.
    """
    return await _get(
        "/api/v1/stations",
        {"q": query, "page": page, "page_size": page_size},
    )


@mcp.tool()
async def get_departures(
    station: str,
    date: Optional[str] = None,
    after_time: Optional[str] = None,
    page: int = 0,
    page_size: int = 50,
) -> dict:
    """All departures from a station on a date.

    Args:
        station: station id or Amtrak 3-letter code (e.g. 'CHI').
        date: ISO date 'YYYY-MM-DD'. Defaults to today in the feed timezone.
        after_time: optional 'HH:MM' filter; only departures at/after this time.
    """
    return await _get(
        f"/api/v1/stations/{station}/departures",
        {"date": date, "time": after_time, "page": page, "page_size": page_size},
    )


@mcp.tool()
async def get_trains_between(
    from_station: str,
    to_station: str,
    date: Optional[str] = None,
) -> dict:
    """Direct trains between two stations on a date (Phase 1: direct only).

    Args:
        from_station: origin id or code (e.g. 'CHI').
        to_station: destination id or code (e.g. 'SEA').
        date: ISO date 'YYYY-MM-DD'. Defaults to today.
    """
    return await _get(
        "/api/v1/between",
        {"from": from_station, "to": to_station, "date": date},
    )


@mcp.tool()
async def get_trip_stops(trip_id: str) -> dict:
    """Full calling pattern of one trip: every stop with arrival/departure in order."""
    return await _get(f"/api/v1/trips/{trip_id}")


@mcp.tool()
async def get_feed_status() -> dict:
    """Feed vintage and counts: version, service date range, refresh time,
    route/station counts. Use this to report how current the timetable is."""
    return await _get("/api/v1/meta")


if __name__ == "__main__":
    # Streamable HTTP transport so the server is reachable at
    # https://train.us.siv19.dev/mcp behind the reverse proxy.
    mcp.run(transport="http", host="0.0.0.0", port=MCP_PORT, path="/mcp")
