-- train.us.siv19.dev — GTFS schema (Amtrak, single agency)
-- Reuses the 9-table GTFS shape from sivab193/india-public-transport.
--
-- Key design decisions carried over:
--   * arrival_time / departure_time stored as INTEGER seconds-from-midnight.
--     GTFS times legitimately exceed 24:00:00 (e.g. 25:30:00 == 01:30 next day).
--     Integer seconds handle that without timezone or wrap-around bugs.
--   * composite index stop_times(trip_id, stop_sequence)  -> ordered calling pattern
--   * index       stop_times(stop_id, arrival_time)       -> departures-from-station
--
-- Single agency (Amtrak), so no cities / users / subscriptions tables.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. agency
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agency (
    agency_id        TEXT PRIMARY KEY,
    agency_name      TEXT NOT NULL,
    agency_url       TEXT,
    agency_timezone  TEXT NOT NULL,
    agency_lang      TEXT,
    agency_phone     TEXT
);

-- ---------------------------------------------------------------------------
-- 2. stops  (stations and platforms; location_type 1 == parent station)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stops (
    stop_id          TEXT PRIMARY KEY,
    stop_code        TEXT,            -- Amtrak 3-letter code often lives here
    stop_name        TEXT NOT NULL,
    stop_desc        TEXT,
    stop_lat         DOUBLE PRECISION,
    stop_lon         DOUBLE PRECISION,
    zone_id          TEXT,
    stop_url         TEXT,
    location_type    INTEGER DEFAULT 0,   -- 0 stop/platform, 1 station
    parent_station   TEXT,
    stop_timezone    TEXT,
    wheelchair_boarding INTEGER
);

-- ---------------------------------------------------------------------------
-- 3. routes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routes (
    route_id         TEXT PRIMARY KEY,
    agency_id        TEXT,
    route_short_name TEXT,
    route_long_name  TEXT,
    route_desc       TEXT,
    route_type       INTEGER,          -- 2 = rail, 3 = bus (Thruway)
    route_url        TEXT,
    route_color      TEXT,
    route_text_color TEXT
);

-- ---------------------------------------------------------------------------
-- 4. calendar  (weekday service pattern)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar (
    service_id       TEXT PRIMARY KEY,
    monday           INTEGER NOT NULL,
    tuesday          INTEGER NOT NULL,
    wednesday        INTEGER NOT NULL,
    thursday         INTEGER NOT NULL,
    friday           INTEGER NOT NULL,
    saturday         INTEGER NOT NULL,
    sunday           INTEGER NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL
);

-- ---------------------------------------------------------------------------
-- 5. calendar_dates  (service exceptions: 1 = added, 2 = removed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_dates (
    service_id       TEXT NOT NULL,
    date             DATE NOT NULL,
    exception_type   INTEGER NOT NULL,
    PRIMARY KEY (service_id, date)
);

-- ---------------------------------------------------------------------------
-- 6. trips
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trips (
    trip_id          TEXT PRIMARY KEY,
    route_id         TEXT NOT NULL,
    service_id       TEXT NOT NULL,
    trip_headsign    TEXT,
    trip_short_name  TEXT,
    direction_id     INTEGER,
    block_id         TEXT,
    shape_id         TEXT,
    wheelchair_accessible INTEGER,
    bikes_allowed    INTEGER
);

-- ---------------------------------------------------------------------------
-- 7. stop_times  (the big table; times as INTEGER seconds from midnight)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stop_times (
    trip_id          TEXT NOT NULL,
    arrival_time     INTEGER,          -- seconds from midnight (may exceed 86400)
    departure_time   INTEGER,          -- seconds from midnight (may exceed 86400)
    stop_id          TEXT NOT NULL,
    stop_sequence    INTEGER NOT NULL,
    stop_headsign    TEXT,
    pickup_type      INTEGER,
    drop_off_type    INTEGER,
    shape_dist_traveled DOUBLE PRECISION,
    timepoint        INTEGER,
    PRIMARY KEY (trip_id, stop_sequence)
);

-- ---------------------------------------------------------------------------
-- 8. shapes  (route geometry; used by Phase 2 live map, kept now)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shapes (
    shape_id            TEXT NOT NULL,
    shape_pt_lat        DOUBLE PRECISION NOT NULL,
    shape_pt_lon        DOUBLE PRECISION NOT NULL,
    shape_pt_sequence   INTEGER NOT NULL,
    shape_dist_traveled DOUBLE PRECISION,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

-- ---------------------------------------------------------------------------
-- 9. meta  (feed vintage; one row per ingestion, latest wins)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    id               SERIAL PRIMARY KEY,
    feed             TEXT NOT NULL,          -- e.g. 'mdb-11'
    data_as_of       DATE NOT NULL,          -- dataset date
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    feed_start_date  DATE,                   -- min(calendar.start_date)
    feed_end_date    DATE,                   -- max(calendar.end_date)
    route_count      INTEGER,
    station_count    INTEGER,
    source_url       TEXT
);

-- ===========================================================================
-- Indexes — the whole performance story.
-- ===========================================================================

-- ordered calling-pattern lookups (trip -> its stops in sequence)
CREATE INDEX IF NOT EXISTS idx_stop_times_trip_seq
    ON stop_times (trip_id, stop_sequence);

-- departures-from-a-station queries (station -> trains by time)
CREATE INDEX IF NOT EXISTS idx_stop_times_stop_arr
    ON stop_times (stop_id, arrival_time);

-- departures by departure_time as well (after-time filter on departures)
CREATE INDEX IF NOT EXISTS idx_stop_times_stop_dep
    ON stop_times (stop_id, departure_time);

-- trip -> route and trip -> service joins
CREATE INDEX IF NOT EXISTS idx_trips_route    ON trips (route_id);
CREATE INDEX IF NOT EXISTS idx_trips_service  ON trips (service_id);

-- station search (name ILIKE and code lookups)
CREATE INDEX IF NOT EXISTS idx_stops_name     ON stops (lower(stop_name));
CREATE INDEX IF NOT EXISTS idx_stops_code     ON stops (upper(stop_code));
CREATE INDEX IF NOT EXISTS idx_stops_parent   ON stops (parent_station);

-- calendar_dates lookups by date
CREATE INDEX IF NOT EXISTS idx_caldates_date  ON calendar_dates (date);

COMMIT;
