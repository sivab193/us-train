//! Runtime configuration, read from the environment (see .env.example).

#[derive(Clone, Debug)]
pub struct Config {
    pub database_url: String,
    pub port: u16,
    pub feed_id: String,
    pub feed_tz: String,
}

impl Config {
    pub fn from_env() -> Self {
        // Prefer an explicit DATABASE_URL; otherwise assemble from parts so the
        // same compose env vars drive API, ingestion and MCP.
        let database_url = std::env::var("DATABASE_URL").unwrap_or_else(|_| {
            let host = env_or("POSTGRES_HOST", "localhost");
            let port = env_or("POSTGRES_PORT", "5432");
            let db = env_or("POSTGRES_DB", "train");
            let user = env_or("POSTGRES_USER", "train");
            let pass = env_or("POSTGRES_PASSWORD", "train");
            format!("postgres://{user}:{pass}@{host}:{port}/{db}")
        });

        let port = env_or("API_PORT", "8080").parse().unwrap_or(8080);
        let feed_id = env_or("GTFS_FEED_ID", "mdb-11");
        let feed_tz = env_or("GTFS_FEED_TZ", "America/New_York");

        Config { database_url, port, feed_id, feed_tz }
    }
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}
