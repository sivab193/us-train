//! HTTP handlers for /api/v1. Each handler validates input, calls the db layer,
//! and wraps the result in the standard envelope.

use crate::config::Config;
use crate::db;
use crate::error::{ApiError, ApiResult};
use crate::gtfs_time;
use crate::models::*;
use axum::extract::{Path, Query, State};
use axum::Json;
use chrono::NaiveDate;
use serde::Deserialize;
use sqlx::PgPool;
use std::sync::Arc;

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub config: Arc<Config>,
}

const DEFAULT_PAGE_SIZE: i64 = 50;
const MAX_PAGE_SIZE: i64 = 200;

fn page_params(page: Option<i64>, page_size: Option<i64>) -> (i64, i64) {
    let page = page.unwrap_or(0).max(0);
    let size = page_size.unwrap_or(DEFAULT_PAGE_SIZE).clamp(1, MAX_PAGE_SIZE);
    (page, size)
}

async fn envelope<T: serde::Serialize>(state: &AppState, data: T) -> Envelope<T> {
    let data_as_of = db::current_data_as_of(&state.pool).await;
    Envelope {
        data,
        data_as_of,
        feed: state.config.feed_id.clone(),
    }
}

// ---------------------------------------------------------------------------
// GET /api/v1/stations?q=&page=&page_size=
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
pub struct StationQuery {
    pub q: Option<String>,
    pub page: Option<i64>,
    pub page_size: Option<i64>,
}

pub async fn search_stations(
    State(state): State<AppState>,
    Query(p): Query<StationQuery>,
) -> ApiResult<Json<Envelope<Paged<StationSummary>>>> {
    let (page, size) = page_params(p.page, p.page_size);
    let q = p.q.as_deref();
    let total = db::count_stations(&state.pool, q).await?;
    let items = db::search_stations(&state.pool, q, page, size).await?;
    let paged = Paged::new(items, page, size, total);
    Ok(Json(envelope(&state, paged).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/stations/{id}
// ---------------------------------------------------------------------------
pub async fn get_station(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<Envelope<StationDetail>>> {
    let station = db::get_station(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("station {id}")))?;
    Ok(Json(envelope(&state, station).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/stations/{id}/departures?date=&time=&page=&page_size=
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
pub struct DeparturesQuery {
    pub date: Option<String>,
    pub time: Option<String>,
    pub page: Option<i64>,
    pub page_size: Option<i64>,
}

pub async fn get_departures(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(p): Query<DeparturesQuery>,
) -> ApiResult<Json<Envelope<Paged<Departure>>>> {
    let date = parse_date(p.date.as_deref())?;
    let after = match p.time.as_deref() {
        Some(t) => gtfs_time::parse(t).map_err(ApiError::BadRequest)?,
        None => None,
    };
    // resolve code -> stop_id (accepts CHI or a raw stop_id)
    let station_id = db::resolve_station_id(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("station {id}")))?;

    let (page, size) = page_params(p.page, p.page_size);
    let total = db::count_departures(&state.pool, &station_id, date, after).await?;
    let items = db::departures(&state.pool, &station_id, date, after, page, size).await?;
    let paged = Paged::new(items, page, size, total);
    Ok(Json(envelope(&state, paged).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/routes?type=rail|bus
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
pub struct RoutesQuery {
    pub r#type: Option<String>,
}

pub async fn list_routes(
    State(state): State<AppState>,
    Query(p): Query<RoutesQuery>,
) -> ApiResult<Json<Envelope<Vec<RouteSummary>>>> {
    let kind = match p.r#type.as_deref() {
        Some("rail") | Some("bus") => p.r#type.as_deref(),
        Some(other) => {
            return Err(ApiError::BadRequest(format!(
                "type must be 'rail' or 'bus', got '{other}'"
            )))
        }
        None => None,
    };
    let routes = db::list_routes(&state.pool, kind).await?;
    Ok(Json(envelope(&state, routes).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/routes/{id}
// ---------------------------------------------------------------------------
pub async fn get_route(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<Envelope<RouteDetail>>> {
    let route = db::get_route(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("route {id}")))?;
    Ok(Json(envelope(&state, route).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/between?from=CHI&to=SEA&date=2026-12-23
// ---------------------------------------------------------------------------
#[derive(Deserialize)]
pub struct BetweenQuery {
    pub from: String,
    pub to: String,
    pub date: Option<String>,
}

pub async fn between(
    State(state): State<AppState>,
    Query(p): Query<BetweenQuery>,
) -> ApiResult<Json<Envelope<Vec<BetweenTrain>>>> {
    let date = parse_date(p.date.as_deref())?;
    let from = db::resolve_station_id(&state.pool, &p.from)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("station {}", p.from)))?;
    let to = db::resolve_station_id(&state.pool, &p.to)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("station {}", p.to)))?;
    let trains = db::between(&state.pool, &from, &to, date).await?;
    Ok(Json(envelope(&state, trains).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/trips/{id}
// ---------------------------------------------------------------------------
pub async fn get_trip(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<Envelope<TripDetail>>> {
    let trip = db::get_trip(&state.pool, &id)
        .await?
        .ok_or_else(|| ApiError::NotFound(format!("trip {id}")))?;
    Ok(Json(envelope(&state, trip).await))
}

// ---------------------------------------------------------------------------
// GET /api/v1/meta
// ---------------------------------------------------------------------------
pub async fn get_meta(
    State(state): State<AppState>,
) -> ApiResult<Json<Envelope<FeedMeta>>> {
    let meta = db::latest_meta(&state.pool, &state.config.feed_tz)
        .await?
        .ok_or_else(|| ApiError::NotFound("no feed ingested yet".to_string()))?;
    Ok(Json(envelope(&state, meta).await))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Parse an ISO date (YYYY-MM-DD). Defaults to today if omitted.
fn parse_date(s: Option<&str>) -> ApiResult<NaiveDate> {
    match s {
        Some(s) if !s.is_empty() => NaiveDate::parse_from_str(s, "%Y-%m-%d")
            .map_err(|_| ApiError::BadRequest(format!("invalid date '{s}', expected YYYY-MM-DD"))),
        _ => Ok(chrono::Local::now().date_naive()),
    }
}
