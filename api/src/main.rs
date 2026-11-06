//! train.us.siv19.dev — Amtrak GTFS schedule-search API (Phase 1).
//!
//! axum + sqlx(Postgres). The MCP server and CLI are thin clients over this
//! API; neither touches the database. One query implementation, three reaches.

mod config;
mod db;
mod error;
mod gtfs_time;
mod handlers;
mod models;

use axum::routing::get;
use axum::Router;
use config::Config;
use handlers::AppState;
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use std::time::Duration;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,train_api=info".into()),
        )
        .init();

    let config = Config::from_env();
    tracing::info!("connecting to database");

    let pool = PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(10))
        .connect(&config.database_url)
        .await?;

    let state = AppState {
        pool,
        config: Arc::new(config.clone()),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/api/v1/stations", get(handlers::search_stations))
        .route("/api/v1/stations/:id", get(handlers::get_station))
        .route(
            "/api/v1/stations/:id/departures",
            get(handlers::get_departures),
        )
        .route("/api/v1/routes", get(handlers::list_routes))
        .route("/api/v1/routes/:id", get(handlers::get_route))
        .route("/api/v1/between", get(handlers::between))
        .route("/api/v1/trips/:id", get(handlers::get_trip))
        .route("/api/v1/meta", get(handlers::get_meta))
        .layer(TraceLayer::new_for_http())
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = format!("0.0.0.0:{}", config.port);
    tracing::info!("listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn health() -> &'static str {
    "ok"
}
