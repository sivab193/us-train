//! All GTFS SQL lives here. Handlers never build SQL; MCP and CLI never touch
//! this at all (they go through the REST API).
//!
//! sqlx runtime-query mode is used throughout (`query` + `Row` accessors), so
//! the crate compiles WITHOUT a live database. No compile-time query checking.

use crate::gtfs_time::{is_next_day, to_readable};
use crate::models::*;
use chrono::NaiveDate;
use sqlx::{postgres::PgRow, PgPool, Row};

/// Reusable predicate: is a trip's service active on `:date`, honouring BOTH
/// the weekday pattern in `calendar` AND the exceptions in `calendar_dates`.
///
/// GTFS correctness rule: never filter on calendar alone. A service runs on
/// date D iff calendar covers D with the right weekday flag and no removal
/// exception, OR there is an explicit addition exception for D.
///
/// Assumes an outer alias `t` for trips so `t.service_id` is in scope, and a
/// bind parameter `$N` for the date (passed via `date_bind`).
fn service_active_sql(date_bind: &str) -> String {
    format!(
        r#"(
            (
                EXISTS (
                    SELECT 1 FROM calendar c
                    WHERE c.service_id = t.service_id
                      AND {d} BETWEEN c.start_date AND c.end_date
                      AND CASE EXTRACT(DOW FROM CAST({d} AS date))
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
                      AND cd.date = {d} AND cd.exception_type = 2
                )
            )
            OR EXISTS (
                SELECT 1 FROM calendar_dates cd
                WHERE cd.service_id = t.service_id
                  AND cd.date = {d} AND cd.exception_type = 1
            )
        )"#,
        d = date_bind
    )
}

// ---------------------------------------------------------------------------
// Stations
// ---------------------------------------------------------------------------

pub async fn count_stations(pool: &PgPool, q: Option<&str>) -> Result<i64, sqlx::Error> {
    match q {
        Some(q) if !q.is_empty() => {
            let sql = "SELECT count(*) AS n FROM stops s \
                       WHERE (s.location_type = 1 OR s.parent_station IS NULL) \
                       AND (lower(s.stop_name) LIKE $1 OR upper(s.stop_code) = $2)";
            let row = sqlx::query(sql)
                .bind(format!("%{}%", q.to_lowercase()))
                .bind(q.to_uppercase())
                .fetch_one(pool)
                .await?;
            Ok(row.get::<i64, _>("n"))
        }
        _ => {
            let sql = "SELECT count(*) AS n FROM stops s \
                       WHERE (s.location_type = 1 OR s.parent_station IS NULL)";
            let row = sqlx::query(sql).fetch_one(pool).await?;
            Ok(row.get::<i64, _>("n"))
        }
    }
}

pub async fn search_stations(
    pool: &PgPool,
    q: Option<&str>,
    page: i64,
    page_size: i64,
) -> Result<Vec<StationSummary>, sqlx::Error> {
    let offset = page * page_size;
    let rows = match q {
        Some(q) if !q.is_empty() => {
            let sql = "SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_lat, s.stop_lon \
                       FROM stops s \
                       WHERE (s.location_type = 1 OR s.parent_station IS NULL) \
                       AND (lower(s.stop_name) LIKE $1 OR upper(s.stop_code) = $2) \
                       ORDER BY s.stop_name LIMIT $3 OFFSET $4";
            sqlx::query(sql)
                .bind(format!("%{}%", q.to_lowercase()))
                .bind(q.to_uppercase())
                .bind(page_size)
                .bind(offset)
                .fetch_all(pool)
                .await?
        }
        _ => {
            let sql = "SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_lat, s.stop_lon \
                       FROM stops s \
                       WHERE (s.location_type = 1 OR s.parent_station IS NULL) \
                       ORDER BY s.stop_name LIMIT $1 OFFSET $2";
            sqlx::query(sql)
                .bind(page_size)
                .bind(offset)
                .fetch_all(pool)
                .await?
        }
    };
    Ok(rows.iter().map(map_station_summary).collect())
}

fn map_station_summary(r: &PgRow) -> StationSummary {
    StationSummary {
        id: r.get("stop_id"),
        code: r.get("stop_code"),
        name: r.get("stop_name"),
        lat: r.get("stop_lat"),
        lon: r.get("stop_lon"),
    }
}

pub async fn get_station(pool: &PgPool, id: &str) -> Result<Option<StationDetail>, sqlx::Error> {
    let sql = "SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_desc, \
               s.stop_lat, s.stop_lon, s.stop_timezone \
               FROM stops s WHERE s.stop_id = $1";
    let row = sqlx::query(sql).bind(id).fetch_optional(pool).await?;
    let Some(r) = row else { return Ok(None) };

    let routes = routes_serving_station(pool, id).await?;
    Ok(Some(StationDetail {
        id: r.get("stop_id"),
        code: r.get("stop_code"),
        name: r.get("stop_name"),
        description: r.get("stop_desc"),
        lat: r.get("stop_lat"),
        lon: r.get("stop_lon"),
        timezone: r.get("stop_timezone"),
        routes,
    }))
}

async fn routes_serving_station(
    pool: &PgPool,
    station_id: &str,
) -> Result<Vec<RouteSummary>, sqlx::Error> {
    let sql = "SELECT DISTINCT r.route_id, r.route_short_name, r.route_long_name, r.route_type \
               FROM routes r \
               JOIN trips t ON t.route_id = r.route_id \
               JOIN stop_times st ON st.trip_id = t.trip_id \
               JOIN stops stp ON stp.stop_id = st.stop_id \
               WHERE stp.stop_id = $1 OR stp.parent_station = $1 \
               ORDER BY r.route_long_name";
    let rows = sqlx::query(sql).bind(station_id).fetch_all(pool).await?;
    Ok(rows.iter().map(map_route_summary).collect())
}

fn map_route_summary(r: &PgRow) -> RouteSummary {
    let rtype: Option<i32> = r.get("route_type");
    RouteSummary {
        id: r.get("route_id"),
        short_name: r.get("route_short_name"),
        long_name: r.get("route_long_name"),
        r#type: rtype,
        kind: kind_of(rtype),
    }
}

// ---------------------------------------------------------------------------
// Departures
// ---------------------------------------------------------------------------

pub async fn departures(
    pool: &PgPool,
    station_id: &str,
    date: NaiveDate,
    after_seconds: Option<i32>,
    page: i64,
    page_size: i64,
) -> Result<Vec<Departure>, sqlx::Error> {
    // binds: $1 station, $2 date (used by service predicate), $3 after,
    //        $4 limit, $5 offset
    let predicate = service_active_sql("$2");
    let sql = format!(
        "SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name, \
                COALESCE(st.stop_headsign, t.trip_headsign) AS headsign, \
                st.departure_time, st.arrival_time, st.stop_sequence \
         FROM stop_times st \
         JOIN stops stp ON stp.stop_id = st.stop_id \
         JOIN trips t ON t.trip_id = st.trip_id \
         JOIN routes r ON r.route_id = t.route_id \
         WHERE (stp.stop_id = $1 OR stp.parent_station = $1) \
           AND st.departure_time IS NOT NULL \
           AND ($3::int IS NULL OR st.departure_time >= $3) \
           AND {predicate} \
         ORDER BY st.departure_time LIMIT $4 OFFSET $5"
    );
    let rows = sqlx::query(&sql)
        .bind(station_id)
        .bind(date)
        .bind(after_seconds)
        .bind(page_size)
        .bind(page * page_size)
        .fetch_all(pool)
        .await?;
    Ok(rows
        .iter()
        .map(|r| {
            let dep: Option<i32> = r.get("departure_time");
            let arr: Option<i32> = r.get("arrival_time");
            Departure {
                trip_id: r.get("trip_id"),
                route_id: r.get("route_id"),
                route_name: r.get("route_long_name"),
                trip_short_name: r.get("trip_short_name"),
                headsign: r.get("headsign"),
                departure_time: to_readable(dep),
                arrival_time: to_readable(arr),
                departs_next_day: is_next_day(dep),
                stop_sequence: r.get("stop_sequence"),
            }
        })
        .collect())
}

pub async fn count_departures(
    pool: &PgPool,
    station_id: &str,
    date: NaiveDate,
    after_seconds: Option<i32>,
) -> Result<i64, sqlx::Error> {
    let predicate = service_active_sql("$2");
    let sql = format!(
        "SELECT count(*) AS n \
         FROM stop_times st \
         JOIN stops stp ON stp.stop_id = st.stop_id \
         JOIN trips t ON t.trip_id = st.trip_id \
         WHERE (stp.stop_id = $1 OR stp.parent_station = $1) \
           AND st.departure_time IS NOT NULL \
           AND ($3::int IS NULL OR st.departure_time >= $3) \
           AND {predicate}"
    );
    let row = sqlx::query(&sql)
        .bind(station_id)
        .bind(date)
        .bind(after_seconds)
        .fetch_one(pool)
        .await?;
    Ok(row.get::<i64, _>("n"))
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

pub async fn list_routes(pool: &PgPool, kind: Option<&str>) -> Result<Vec<RouteSummary>, sqlx::Error> {
    let rows = match kind {
        Some("rail") => {
            sqlx::query(
                "SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_type \
                 FROM routes r WHERE r.route_type = 2 ORDER BY r.route_long_name",
            )
            .fetch_all(pool)
            .await?
        }
        Some("bus") => {
            sqlx::query(
                "SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_type \
                 FROM routes r WHERE r.route_type = 3 ORDER BY r.route_long_name",
            )
            .fetch_all(pool)
            .await?
        }
        _ => {
            sqlx::query(
                "SELECT r.route_id, r.route_short_name, r.route_long_name, r.route_type \
                 FROM routes r ORDER BY r.route_long_name",
            )
            .fetch_all(pool)
            .await?
        }
    };
    Ok(rows.iter().map(map_route_summary).collect())
}

pub async fn get_route(pool: &PgPool, id: &str) -> Result<Option<RouteDetail>, sqlx::Error> {
    let sql = "SELECT r.route_id, r.route_short_name, r.route_long_name, \
                      r.route_type, r.route_desc \
               FROM routes r WHERE r.route_id = $1";
    let row = sqlx::query(sql).bind(id).fetch_optional(pool).await?;
    let Some(r) = row else { return Ok(None) };

    let rtype: Option<i32> = r.get("route_type");
    let stations = route_stations_in_order(pool, id).await?;
    Ok(Some(RouteDetail {
        id: r.get("route_id"),
        short_name: r.get("route_short_name"),
        long_name: r.get("route_long_name"),
        r#type: rtype,
        kind: kind_of(rtype),
        description: r.get("route_desc"),
        stations,
    }))
}

/// Stations of a route in travel order, taken from the representative trip with
/// the most stops (covers the full calling pattern).
async fn route_stations_in_order(
    pool: &PgPool,
    route_id: &str,
) -> Result<Vec<StationSummary>, sqlx::Error> {
    let sql = "WITH rep AS ( \
                 SELECT t.trip_id FROM trips t \
                 JOIN stop_times st ON st.trip_id = t.trip_id \
                 WHERE t.route_id = $1 \
                 GROUP BY t.trip_id ORDER BY count(*) DESC LIMIT 1 \
               ) \
               SELECT s.stop_id, s.stop_code, s.stop_name, s.stop_lat, s.stop_lon \
               FROM stop_times st \
               JOIN rep ON rep.trip_id = st.trip_id \
               JOIN stops s ON s.stop_id = st.stop_id \
               ORDER BY st.stop_sequence";
    let rows = sqlx::query(sql).bind(route_id).fetch_all(pool).await?;
    Ok(rows.iter().map(map_station_summary).collect())
}

// ---------------------------------------------------------------------------
// Between (direct trains only — Phase 1)
// ---------------------------------------------------------------------------

pub async fn between(
    pool: &PgPool,
    from_id: &str,
    to_id: &str,
    date: NaiveDate,
) -> Result<Vec<BetweenTrain>, sqlx::Error> {
    // binds: $1 from, $2 to, $3 date
    let predicate = service_active_sql("$3");
    let sql = format!(
        "SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name, \
                t.trip_headsign, \
                o.stop_id AS from_id, os.stop_name AS from_name, \
                d.stop_id AS to_id, ds.stop_name AS to_name, \
                o.departure_time AS dep, d.arrival_time AS arr \
         FROM trips t \
         JOIN routes r ON r.route_id = t.route_id \
         JOIN stop_times o ON o.trip_id = t.trip_id \
         JOIN stops os ON os.stop_id = o.stop_id \
         JOIN stop_times d ON d.trip_id = t.trip_id \
         JOIN stops ds ON ds.stop_id = d.stop_id \
         WHERE (o.stop_id = $1 OR os.parent_station = $1) \
           AND (d.stop_id = $2 OR ds.parent_station = $2) \
           AND d.stop_sequence > o.stop_sequence \
           AND o.departure_time IS NOT NULL \
           AND d.arrival_time IS NOT NULL \
           AND {predicate} \
         ORDER BY o.departure_time"
    );
    let rows = sqlx::query(&sql)
        .bind(from_id)
        .bind(to_id)
        .bind(date)
        .fetch_all(pool)
        .await?;
    Ok(rows
        .iter()
        .map(|r| {
            let dep: Option<i32> = r.get("dep");
            let arr: Option<i32> = r.get("arr");
            let duration = match (arr, dep) {
                (Some(a), Some(d)) => (a - d) / 60,
                _ => 0,
            };
            BetweenTrain {
                trip_id: r.get("trip_id"),
                route_id: r.get("route_id"),
                route_name: r.get("route_long_name"),
                trip_short_name: r.get("trip_short_name"),
                headsign: r.get("trip_headsign"),
                from_station_id: r.get("from_id"),
                from_station_name: r.get("from_name"),
                to_station_id: r.get("to_id"),
                to_station_name: r.get("to_name"),
                departure_time: to_readable(dep),
                arrival_time: to_readable(arr),
                duration_minutes: duration,
                overnight: is_next_day(arr),
            }
        })
        .collect())
}

/// Resolve an Amtrak 3-letter code (or pass-through a stop_id) to a stop_id.
pub async fn resolve_station_id(pool: &PgPool, code_or_id: &str) -> Result<Option<String>, sqlx::Error> {
    let sql = "SELECT stop_id FROM stops \
               WHERE stop_id = $1 OR upper(stop_code) = upper($1) \
               ORDER BY CASE WHEN stop_id = $1 THEN 0 ELSE 1 END, \
                        CASE WHEN location_type = 1 OR parent_station IS NULL THEN 0 ELSE 1 END \
               LIMIT 1";
    let row = sqlx::query(sql).bind(code_or_id).fetch_optional(pool).await?;
    Ok(row.map(|r| r.get::<String, _>("stop_id")))
}

// ---------------------------------------------------------------------------
// Trips
// ---------------------------------------------------------------------------

pub async fn get_trip(pool: &PgPool, trip_id: &str) -> Result<Option<TripDetail>, sqlx::Error> {
    let head_sql = "SELECT t.trip_id, t.route_id, r.route_long_name, t.trip_short_name, \
                           t.trip_headsign, t.service_id \
                    FROM trips t JOIN routes r ON r.route_id = t.route_id \
                    WHERE t.trip_id = $1";
    let head = sqlx::query(head_sql).bind(trip_id).fetch_optional(pool).await?;
    let Some(h) = head else { return Ok(None) };

    let stops_sql = "SELECT st.stop_sequence, st.stop_id, s.stop_code, s.stop_name, \
                            st.arrival_time, st.departure_time \
                     FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id \
                     WHERE st.trip_id = $1 ORDER BY st.stop_sequence";
    let stop_rows = sqlx::query(stops_sql).bind(trip_id).fetch_all(pool).await?;
    let stops = stop_rows
        .iter()
        .map(|r| {
            let arr: Option<i32> = r.get("arrival_time");
            let dep: Option<i32> = r.get("departure_time");
            TripStop {
                stop_sequence: r.get("stop_sequence"),
                stop_id: r.get("stop_id"),
                stop_code: r.get("stop_code"),
                stop_name: r.get("stop_name"),
                arrival_time: to_readable(arr),
                departure_time: to_readable(dep),
                arrives_next_day: is_next_day(arr),
            }
        })
        .collect();

    Ok(Some(TripDetail {
        trip_id: h.get("trip_id"),
        route_id: h.get("route_id"),
        route_name: h.get("route_long_name"),
        trip_short_name: h.get("trip_short_name"),
        headsign: h.get("trip_headsign"),
        service_id: h.get("service_id"),
        stops,
    }))
}

// ---------------------------------------------------------------------------
// Meta
// ---------------------------------------------------------------------------

pub async fn latest_meta(pool: &PgPool, timezone: &str) -> Result<Option<FeedMeta>, sqlx::Error> {
    let sql = "SELECT feed, data_as_of::text AS data_as_of, ingested_at::text AS ingested_at, \
                      feed_start_date::text AS feed_start_date, \
                      feed_end_date::text AS feed_end_date, \
                      route_count, station_count, source_url \
               FROM meta ORDER BY ingested_at DESC LIMIT 1";
    let row = sqlx::query(sql).fetch_optional(pool).await?;
    Ok(row.map(|r| FeedMeta {
        feed: r.get("feed"),
        data_as_of: r.get("data_as_of"),
        ingested_at: r.get("ingested_at"),
        feed_start_date: r.get("feed_start_date"),
        feed_end_date: r.get("feed_end_date"),
        route_count: r.get("route_count"),
        station_count: r.get("station_count"),
        timezone: timezone.to_string(),
        source_url: r.get("source_url"),
    }))
}

/// The data_as_of value used to stamp every response envelope. Returns None if
/// no feed has been ingested yet (table empty).
pub async fn current_data_as_of(pool: &PgPool) -> Option<String> {
    let sql = "SELECT data_as_of::text AS d FROM meta ORDER BY ingested_at DESC LIMIT 1";
    match sqlx::query(sql).fetch_optional(pool).await {
        Ok(Some(r)) => r.get("d"),
        _ => None,
    }
}
