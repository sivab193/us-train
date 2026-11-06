//! Response models. Field names are already snake_case, matching the
//! architecture doc's JSON contract (data_as_of, stop_id, ...).

use serde::Serialize;

/// Standard envelope wrapping every endpoint payload.
#[derive(Serialize)]
pub struct Envelope<T: Serialize> {
    pub data: T,
    pub data_as_of: Option<String>,
    pub feed: String,
}

/// Pagination wrapper, used as the `data` of list endpoints.
#[derive(Serialize)]
pub struct Paged<T: Serialize> {
    pub items: Vec<T>,
    pub page: i64,
    pub page_size: i64,
    pub total: i64,
    pub has_more: bool,
}

impl<T: Serialize> Paged<T> {
    pub fn new(items: Vec<T>, page: i64, page_size: i64, total: i64) -> Self {
        let has_more = page * page_size + items.len() as i64 < total;
        Paged { items, page, page_size, total, has_more }
    }
}

#[derive(Serialize)]
pub struct StationSummary {
    pub id: String,
    pub code: Option<String>,
    pub name: String,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
}

#[derive(Serialize)]
pub struct RouteSummary {
    pub id: String,
    pub short_name: Option<String>,
    pub long_name: Option<String>,
    pub r#type: Option<i32>,
    pub kind: String,
}

#[derive(Serialize)]
pub struct StationDetail {
    pub id: String,
    pub code: Option<String>,
    pub name: String,
    pub description: Option<String>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub timezone: Option<String>,
    pub routes: Vec<RouteSummary>,
}

#[derive(Serialize)]
pub struct RouteDetail {
    pub id: String,
    pub short_name: Option<String>,
    pub long_name: Option<String>,
    pub r#type: Option<i32>,
    pub kind: String,
    pub description: Option<String>,
    pub stations: Vec<StationSummary>,
}

#[derive(Serialize)]
pub struct Departure {
    pub trip_id: String,
    pub route_id: String,
    pub route_name: Option<String>,
    pub trip_short_name: Option<String>,
    pub headsign: Option<String>,
    pub departure_time: Option<String>,
    pub arrival_time: Option<String>,
    pub departs_next_day: bool,
    pub stop_sequence: i32,
}

#[derive(Serialize)]
pub struct BetweenTrain {
    pub trip_id: String,
    pub route_id: String,
    pub route_name: Option<String>,
    pub trip_short_name: Option<String>,
    pub headsign: Option<String>,
    pub from_station_id: String,
    pub from_station_name: String,
    pub to_station_id: String,
    pub to_station_name: String,
    pub departure_time: Option<String>,
    pub arrival_time: Option<String>,
    pub duration_minutes: i32,
    pub overnight: bool,
}

#[derive(Serialize)]
pub struct TripStop {
    pub stop_sequence: i32,
    pub stop_id: String,
    pub stop_code: Option<String>,
    pub stop_name: String,
    pub arrival_time: Option<String>,
    pub departure_time: Option<String>,
    pub arrives_next_day: bool,
}

#[derive(Serialize)]
pub struct TripDetail {
    pub trip_id: String,
    pub route_id: String,
    pub route_name: Option<String>,
    pub trip_short_name: Option<String>,
    pub headsign: Option<String>,
    pub service_id: String,
    pub stops: Vec<TripStop>,
}

#[derive(Serialize)]
pub struct FeedMeta {
    pub feed: Option<String>,
    pub data_as_of: Option<String>,
    pub ingested_at: Option<String>,
    pub feed_start_date: Option<String>,
    pub feed_end_date: Option<String>,
    pub route_count: Option<i32>,
    pub station_count: Option<i32>,
    pub timezone: String,
    pub source_url: Option<String>,
}

/// Friendly classification from GTFS route_type (2 = rail, 3 = bus).
pub fn kind_of(route_type: Option<i32>) -> String {
    match route_type {
        Some(2) => "rail".to_string(),
        Some(3) => "bus".to_string(),
        _ => "other".to_string(),
    }
}
