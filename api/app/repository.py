"""PostgreSQL query layer for the GTFS API."""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .gtfs_time import is_next_day, to_readable

SERVICE_ACTIVE_SQL = """
(
    (
        EXISTS (
            SELECT 1 FROM calendar c
            WHERE c.service_id = t.service_id
              AND %(service_date)s BETWEEN c.start_date AND c.end_date
              AND CASE EXTRACT(DOW FROM CAST(%(service_date)s AS date))
                    WHEN 0 THEN c.sunday
                    WHEN 1 THEN c.monday
                    WHEN 2 THEN c.tuesday
                    WHEN 3 THEN c.wednesday
                    WHEN 4 THEN c.thursday
                    WHEN 5 THEN c.friday
                    WHEN 6 THEN c.saturday
                  END = 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM calendar_dates cd
            WHERE cd.service_id = t.service_id
              AND cd.date = %(service_date)s AND cd.exception_type = 2
        )
    )
    OR EXISTS (
        SELECT 1 FROM calendar_dates cd
        WHERE cd.service_id = t.service_id
          AND cd.date = %(service_date)s AND cd.exception_type = 1
    )
)
"""


def _station(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["stop_id"],
        "code": row["stop_code"],
        "name": row["stop_name"],
        "lat": row["stop_lat"],
        "lon": row["stop_lon"],
    }


def _route(row: dict[str, Any]) -> dict[str, Any]:
    route_type = row["route_type"]
    kind = "rail" if route_type == 2 else "bus" if route_type == 3 else "other"
    return {
        "id": row["route_id"],
        "short_name": row["route_short_name"],
        "long_name": row["route_long_name"],
        "type": route_type,
        "kind": kind,
    }


class Repository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def _fetchall(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                return await cursor.fetchall()

    async def _fetchone(self, query: str, params: Any = None) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                return await cursor.fetchone()

    async def ping(self) -> None:
        await self._fetchone("SELECT 1 AS ok")

    async def current_data_as_of(self) -> str | None:
        row = await self._fetchone(
            "SELECT data_as_of::text AS value FROM meta ORDER BY ingested_at DESC LIMIT 1"
        )
        return row["value"] if row else None

    async def count_stations(self, query: str | None) -> int:
        params: dict[str, Any] = {}
        where = ""
        if query:
            where = "AND (lower(s.stop_name) LIKE %(name)s OR upper(s.stop_code) = %(code)s)"
            params = {"name": f"%{query.lower()}%", "code": query.upper()}
        row = await self._fetchone(
            f"""SELECT count(*) AS total FROM stops s
                WHERE (s.location_type = 1 OR s.parent_station IS NULL) {where}""",
            params,
        )
        return int(row["total"])

    async def search_stations(
        self, query: str | None, page: int, page_size: int
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": page_size, "offset": page * page_size}
        where = ""
        if query:
            where = "AND (lower(s.stop_name) LIKE %(name)s OR upper(s.stop_code) = %(code)s)"
            params.update(name=f"%{query.lower()}%", code=query.upper())
        rows = await self._fetchall(
            f"""SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_lat, s.stop_lon
                FROM stops s
                WHERE (s.location_type = 1 OR s.parent_station IS NULL) {where}
                ORDER BY s.stop_name LIMIT %(limit)s OFFSET %(offset)s""",
            params,
        )
        return [_station(row) for row in rows]

    async def resolve_station_id(self, code_or_id: str) -> str | None:
        row = await self._fetchone(
            """SELECT stop_id FROM stops
               WHERE stop_id = %(value)s OR upper(stop_code) = upper(%(value)s)
               ORDER BY CASE WHEN stop_id = %(value)s THEN 0 ELSE 1 END,
                        CASE WHEN location_type = 1 OR parent_station IS NULL THEN 0 ELSE 1 END
               LIMIT 1""",
            {"value": code_or_id},
        )
        return row["stop_id"] if row else None

    async def get_station(self, station_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            """SELECT stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon,
                      stop_timezone FROM stops WHERE stop_id = %(station_id)s""",
            {"station_id": station_id},
        )
        if not row:
            return None
        routes = await self._fetchall(
            """SELECT DISTINCT r.route_id, r.route_short_name, r.route_long_name, r.route_type
               FROM routes r
               JOIN trips t ON t.route_id = r.route_id
               JOIN stop_times st ON st.trip_id = t.trip_id
               JOIN stops s ON s.stop_id = st.stop_id
               WHERE s.stop_id = %(station_id)s OR s.parent_station = %(station_id)s
               ORDER BY r.route_long_name""",
            {"station_id": station_id},
        )
        return {
            "id": row["stop_id"],
            "code": row["stop_code"],
            "name": row["stop_name"],
            "description": row["stop_desc"],
            "lat": row["stop_lat"],
            "lon": row["stop_lon"],
            "timezone": row["stop_timezone"],
            "routes": [_route(item) for item in routes],
        }

    async def count_departures(
        self, station_id: str, service_date: date, after: int | None
    ) -> int:
        row = await self._fetchone(
            f"""SELECT count(*) AS total
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                JOIN trips t ON t.trip_id = st.trip_id
                WHERE (s.stop_id = %(station_id)s OR s.parent_station = %(station_id)s)
                  AND st.departure_time IS NOT NULL
                  AND (%(after)s::int IS NULL OR st.departure_time >= %(after)s)
                  AND {SERVICE_ACTIVE_SQL}""",
            {"station_id": station_id, "service_date": service_date, "after": after},
        )
        return int(row["total"])

    async def departures(
        self,
        station_id: str,
        service_date: date,
        after: int | None,
        page: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            f"""SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name,
                       COALESCE(st.stop_headsign, t.trip_headsign) AS headsign,
                       st.departure_time, st.arrival_time, st.stop_sequence
                FROM stop_times st
                JOIN stops s ON s.stop_id = st.stop_id
                JOIN trips t ON t.trip_id = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                WHERE (s.stop_id = %(station_id)s OR s.parent_station = %(station_id)s)
                  AND st.departure_time IS NOT NULL
                  AND (%(after)s::int IS NULL OR st.departure_time >= %(after)s)
                  AND {SERVICE_ACTIVE_SQL}
                ORDER BY st.departure_time LIMIT %(limit)s OFFSET %(offset)s""",
            {
                "station_id": station_id,
                "service_date": service_date,
                "after": after,
                "limit": page_size,
                "offset": page * page_size,
            },
        )
        return [
            {
                "trip_id": row["trip_id"],
                "route_id": row["route_id"],
                "route_name": row["route_long_name"],
                "trip_short_name": row["trip_short_name"],
                "headsign": row["headsign"],
                "departure_time": to_readable(row["departure_time"]),
                "arrival_time": to_readable(row["arrival_time"]),
                "departs_next_day": is_next_day(row["departure_time"]),
                "stop_sequence": row["stop_sequence"],
            }
            for row in rows
        ]

    async def list_routes(self, kind: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        where = ""
        if kind:
            where = "WHERE r.route_type = %(route_type)s"
            params["route_type"] = 2 if kind == "rail" else 3
        rows = await self._fetchall(
            f"""SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_type
                FROM routes r {where} ORDER BY r.route_long_name""",
            params,
        )
        return [_route(row) for row in rows]

    async def get_route(self, route_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            """SELECT route_id, route_short_name, route_long_name, route_type, route_desc
               FROM routes WHERE route_id = %(route_id)s""",
            {"route_id": route_id},
        )
        if not row:
            return None
        stations = await self._fetchall(
            """WITH representative AS (
                   SELECT t.trip_id FROM trips t
                   JOIN stop_times st ON st.trip_id = t.trip_id
                   WHERE t.route_id = %(route_id)s
                   GROUP BY t.trip_id ORDER BY count(*) DESC LIMIT 1
               )
               SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_lat, s.stop_lon
               FROM stop_times st
               JOIN representative rep ON rep.trip_id = st.trip_id
               JOIN stops s ON s.stop_id = st.stop_id
               ORDER BY st.stop_sequence""",
            {"route_id": route_id},
        )
        result = _route(row)
        result.update(description=row["route_desc"], stations=[_station(item) for item in stations])
        return result

    async def between(
        self, from_id: str, to_id: str, service_date: date
    ) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            f"""SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name,
                       t.trip_headsign, origin.stop_id AS from_id,
                       origin_stop.stop_name AS from_name,
                       destination.stop_id AS to_id, destination_stop.stop_name AS to_name,
                       origin.departure_time AS departure, destination.arrival_time AS arrival
                FROM trips t
                JOIN routes r ON r.route_id = t.route_id
                JOIN stop_times origin ON origin.trip_id = t.trip_id
                JOIN stops origin_stop ON origin_stop.stop_id = origin.stop_id
                JOIN stop_times destination ON destination.trip_id = t.trip_id
                JOIN stops destination_stop ON destination_stop.stop_id = destination.stop_id
                WHERE (origin.stop_id = %(from_id)s OR origin_stop.parent_station = %(from_id)s)
                  AND (destination.stop_id = %(to_id)s OR destination_stop.parent_station = %(to_id)s)
                  AND destination.stop_sequence > origin.stop_sequence
                  AND origin.departure_time IS NOT NULL
                  AND destination.arrival_time IS NOT NULL
                  AND {SERVICE_ACTIVE_SQL}
                ORDER BY origin.departure_time""",
            {"from_id": from_id, "to_id": to_id, "service_date": service_date},
        )
        return [
            {
                "trip_id": row["trip_id"],
                "route_id": row["route_id"],
                "route_name": row["route_long_name"],
                "trip_short_name": row["trip_short_name"],
                "headsign": row["trip_headsign"],
                "from_station_id": row["from_id"],
                "from_station_name": row["from_name"],
                "to_station_id": row["to_id"],
                "to_station_name": row["to_name"],
                "departure_time": to_readable(row["departure"]),
                "arrival_time": to_readable(row["arrival"]),
                "duration_minutes": (row["arrival"] - row["departure"]) // 60,
                "overnight": is_next_day(row["arrival"]),
            }
            for row in rows
        ]

    async def get_trip(self, trip_id: str) -> dict[str, Any] | None:
        trip = await self._fetchone(
            """SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name,
                      t.trip_headsign, t.service_id
               FROM trips t JOIN routes r ON r.route_id = t.route_id
               WHERE t.trip_id = %(trip_id)s""",
            {"trip_id": trip_id},
        )
        if not trip:
            return None
        rows = await self._fetchall(
            """SELECT st.stop_sequence, st.stop_id, s.stop_code, s.stop_name,
                      st.arrival_time, st.departure_time
               FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id
               WHERE st.trip_id = %(trip_id)s ORDER BY st.stop_sequence""",
            {"trip_id": trip_id},
        )
        return {
            "trip_id": trip["trip_id"],
            "route_id": trip["route_id"],
            "route_name": trip["route_long_name"],
            "trip_short_name": trip["trip_short_name"],
            "headsign": trip["trip_headsign"],
            "service_id": trip["service_id"],
            "stops": [
                {
                    "stop_sequence": row["stop_sequence"],
                    "stop_id": row["stop_id"],
                    "stop_code": row["stop_code"],
                    "stop_name": row["stop_name"],
                    "arrival_time": to_readable(row["arrival_time"]),
                    "departure_time": to_readable(row["departure_time"]),
                    "arrives_next_day": is_next_day(row["arrival_time"]),
                }
                for row in rows
            ],
        }

    async def latest_meta(self, timezone: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            """SELECT feed, data_as_of::text AS data_as_of,
                      ingested_at::text AS ingested_at,
                      feed_start_date::text AS feed_start_date,
                      feed_end_date::text AS feed_end_date,
                      route_count, station_count, source_url
               FROM meta ORDER BY ingested_at DESC LIMIT 1"""
        )
        if row:
            row["timezone"] = timezone
        return row
