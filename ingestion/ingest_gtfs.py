#!/usr/bin/env python3
"""
GTFS bulk ingestion for Amtrak (Mobility Database mdb-11).

Carries over the COPY-based bulk-load approach from
sivab193/india-public-transport (proven at ~35M stop_times). The Amtrak feed
is ~17.7 MB, so this is comfortable.

What it does, in order:
  1. Download the feed zip (primary URL, fall back to Amtrak's own URL).
  2. Extract the GTFS .txt files.
  3. For each known table, stream the CSV into Postgres via COPY, converting
     GTFS HH:MM:SS clock times into INTEGER seconds-from-midnight (handles
     times past 24:00:00 naturally).
  4. Load into staging tables, then swap into the live tables in one
     transaction so the API never sees a half-loaded feed.
  5. Record the dataset vintage in the `meta` table.

Idempotent: a re-run fully replaces the data.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime
from urllib.request import Request, urlopen

import psycopg2

# ---------------------------------------------------------------------------
# Config (env with sane defaults; see .env.example)
# ---------------------------------------------------------------------------
FEED_ID = os.environ.get("GTFS_FEED_ID", "mdb-11")
PRIMARY_URL = os.environ.get(
    "GTFS_PRIMARY_URL", "https://files.mobilitydatabase.org/mdb-11/latest.zip"
)
FALLBACK_URL = os.environ.get(
    "GTFS_FALLBACK_URL", "https://content.amtrak.com/content/gtfs/GTFS.zip"
)

DB = dict(
    host=os.environ.get("POSTGRES_HOST", "localhost"),
    port=int(os.environ.get("POSTGRES_PORT", "5432")),
    dbname=os.environ.get("POSTGRES_DB", "train"),
    user=os.environ.get("POSTGRES_USER", "train"),
    password=os.environ.get("POSTGRES_PASSWORD", "train"),
)

USER_AGENT = "train.us.siv19.dev-ingestion/1.0 (+https://train.us.siv19.dev)"

# ---------------------------------------------------------------------------
# Table definitions: GTFS filename -> (table, [db columns in load order],
#                                      {gtfs_col: converter})
# Only the columns we care about are loaded; the rest of the GTFS file is
# ignored. Columns absent from a given feed are filled with NULL.
# ---------------------------------------------------------------------------

def _time_to_seconds(value: str):
    """'25:30:00' -> 91800. Empty -> None. GTFS allows >24h."""
    value = (value or "").strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def _int(value: str):
    value = (value or "").strip()
    return int(value) if value != "" else None


def _float(value: str):
    value = (value or "").strip()
    return float(value) if value != "" else None


def _date(value: str):
    """GTFS date 'YYYYMMDD' -> ISO 'YYYY-MM-DD' for Postgres DATE."""
    value = (value or "").strip()
    if not value or len(value) != 8:
        return None
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"


def _text(value: str):
    value = value if value is not None else ""
    value = value.strip()
    return value if value != "" else None


# table name -> list of (gtfs_column, db_column, converter)
TABLES = {
    "agency.txt": ("agency", [
        ("agency_id", "agency_id", _text),
        ("agency_name", "agency_name", _text),
        ("agency_url", "agency_url", _text),
        ("agency_timezone", "agency_timezone", _text),
        ("agency_lang", "agency_lang", _text),
        ("agency_phone", "agency_phone", _text),
    ]),
    "stops.txt": ("stops", [
        ("stop_id", "stop_id", _text),
        ("stop_code", "stop_code", _text),
        ("stop_name", "stop_name", _text),
        ("stop_desc", "stop_desc", _text),
        ("stop_lat", "stop_lat", _float),
        ("stop_lon", "stop_lon", _float),
        ("zone_id", "zone_id", _text),
        ("stop_url", "stop_url", _text),
        ("location_type", "location_type", _int),
        ("parent_station", "parent_station", _text),
        ("stop_timezone", "stop_timezone", _text),
        ("wheelchair_boarding", "wheelchair_boarding", _int),
    ]),
    "routes.txt": ("routes", [
        ("route_id", "route_id", _text),
        ("agency_id", "agency_id", _text),
        ("route_short_name", "route_short_name", _text),
        ("route_long_name", "route_long_name", _text),
        ("route_desc", "route_desc", _text),
        ("route_type", "route_type", _int),
        ("route_url", "route_url", _text),
        ("route_color", "route_color", _text),
        ("route_text_color", "route_text_color", _text),
    ]),
    "calendar.txt": ("calendar", [
        ("service_id", "service_id", _text),
        ("monday", "monday", _int),
        ("tuesday", "tuesday", _int),
        ("wednesday", "wednesday", _int),
        ("thursday", "thursday", _int),
        ("friday", "friday", _int),
        ("saturday", "saturday", _int),
        ("sunday", "sunday", _int),
        ("start_date", "start_date", _date),
        ("end_date", "end_date", _date),
    ]),
    "calendar_dates.txt": ("calendar_dates", [
        ("service_id", "service_id", _text),
        ("date", "date", _date),
        ("exception_type", "exception_type", _int),
    ]),
    "trips.txt": ("trips", [
        ("trip_id", "trip_id", _text),
        ("route_id", "route_id", _text),
        ("service_id", "service_id", _text),
        ("trip_headsign", "trip_headsign", _text),
        ("trip_short_name", "trip_short_name", _text),
        ("direction_id", "direction_id", _int),
        ("block_id", "block_id", _text),
        ("shape_id", "shape_id", _text),
        ("wheelchair_accessible", "wheelchair_accessible", _int),
        ("bikes_allowed", "bikes_allowed", _int),
    ]),
    "stop_times.txt": ("stop_times", [
        ("trip_id", "trip_id", _text),
        ("arrival_time", "arrival_time", _time_to_seconds),
        ("departure_time", "departure_time", _time_to_seconds),
        ("stop_id", "stop_id", _text),
        ("stop_sequence", "stop_sequence", _int),
        ("stop_headsign", "stop_headsign", _text),
        ("pickup_type", "pickup_type", _int),
        ("drop_off_type", "drop_off_type", _int),
        ("shape_dist_traveled", "shape_dist_traveled", _float),
        ("timepoint", "timepoint", _int),
    ]),
    "shapes.txt": ("shapes", [
        ("shape_id", "shape_id", _text),
        ("shape_pt_lat", "shape_pt_lat", _float),
        ("shape_pt_lon", "shape_pt_lon", _float),
        ("shape_pt_sequence", "shape_pt_sequence", _int),
        ("shape_dist_traveled", "shape_dist_traveled", _float),
    ]),
}

# load order respects FK-ish dependencies (not enforced, but tidy)
LOAD_ORDER = [
    "agency.txt", "stops.txt", "routes.txt", "calendar.txt",
    "calendar_dates.txt", "trips.txt", "stop_times.txt", "shapes.txt",
]

# tables that must have a row to be usable, and that de-dupe on PK
PK_FIRST_WINS = {
    "stop_times": ("trip_id", "stop_sequence"),
    "calendar_dates": ("service_id", "date"),
    "shapes": ("shape_id", "shape_pt_sequence"),
}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_feed() -> bytes:
    last_err = None
    for url in (PRIMARY_URL, FALLBACK_URL):
        try:
            print(f"[ingest] downloading {url}")
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=120) as resp:
                data = resp.read()
            print(f"[ingest] downloaded {len(data):,} bytes from {url}")
            return data
        except Exception as e:  # noqa: BLE001
            print(f"[ingest] failed {url}: {e}", file=sys.stderr)
            last_err = e
    raise RuntimeError(f"all feed URLs failed; last error: {last_err}")


# ---------------------------------------------------------------------------
# Transform one GTFS file -> an in-memory TSV buffer for COPY
# ---------------------------------------------------------------------------
NULL_TOKEN = "\\N"  # Postgres COPY default NULL marker


def build_copy_buffer(raw_bytes: bytes, table_name: str, col_defs) -> tuple[io.StringIO, int]:
    """Convert a GTFS CSV into a TSV StringIO ready for COPY, applying per-column
    converters and de-duplicating by primary key where required (first wins)."""
    text = raw_bytes.decode("utf-8-sig")  # GTFS files often have a BOM
    reader = csv.DictReader(io.StringIO(text))

    db_cols = [db for (_g, db, _c) in col_defs]
    pk = PK_FIRST_WINS.get(table_name)
    key_idx = [db_cols.index(c) for c in pk] if pk else None
    seen_keys = set() if pk else None

    out = io.StringIO()
    rows = 0

    for row in reader:
        values = []
        for gtfs_col, _db_col, conv in col_defs:
            converted = conv(row.get(gtfs_col, ""))
            if converted is None:
                values.append(NULL_TOKEN)
            else:
                s = str(converted)
                # escape backslash first, then neutralise tab/newline for TSV
                s = s.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", " ")
                values.append(s)

        if seen_keys is not None:
            key = tuple(values[i] for i in key_idx)
            if any(v == NULL_TOKEN for v in key):
                continue  # skip rows with an incomplete primary key
            if key in seen_keys:
                continue
            seen_keys.add(key)

        out.write("\t".join(values))
        out.write("\n")
        rows += 1

    out.seek(0)
    return out, rows


# ---------------------------------------------------------------------------
# Load into Postgres via staging tables + atomic swap
# ---------------------------------------------------------------------------
def load(conn, extracted: dict[str, bytes], source_url: str):
    cur = conn.cursor()
    staged = []

    for gtfs_file in LOAD_ORDER:
        if gtfs_file not in extracted:
            print(f"[ingest] feed has no {gtfs_file}; skipping")
            continue
        table_name, defs = TABLES[gtfs_file]
        staging = f"{table_name}_staging"
        db_cols = [db for (_g, db, _c) in defs]

        cur.execute(f"DROP TABLE IF EXISTS {staging};")
        cur.execute(f"CREATE TABLE {staging} (LIKE {table_name} INCLUDING DEFAULTS);")

        buf, rows = build_copy_buffer(extracted[gtfs_file], table_name, defs)
        cur.copy_expert(
            f"COPY {staging} ({', '.join(db_cols)}) "
            f"FROM STDIN WITH (FORMAT text, NULL '{NULL_TOKEN}')",
            buf,
        )
        print(f"[ingest] staged {rows:,} rows into {staging}")
        staged.append(table_name)

    # atomic swap: truncate live tables (children first) and copy staging in
    print("[ingest] swapping staging -> live (single transaction)")
    for table_name in reversed([TABLES[f][0] for f in LOAD_ORDER if f in extracted]):
        cur.execute(f"TRUNCATE {table_name} CASCADE;")
    for table_name in [TABLES[f][0] for f in LOAD_ORDER if f in extracted]:
        cur.execute(f"INSERT INTO {table_name} SELECT * FROM {table_name}_staging;")
        cur.execute(f"DROP TABLE {table_name}_staging;")

    _write_meta(cur, source_url)
    conn.commit()
    cur.close()
    print("[ingest] committed.")


def _write_meta(cur, source_url: str):
    cur.execute("SELECT min(start_date), max(end_date) FROM calendar;")
    start, end = cur.fetchone()
    cur.execute("SELECT count(*) FROM routes;")
    route_count = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM stops WHERE location_type = 1 OR parent_station IS NULL;"
    )
    station_count = cur.fetchone()[0]

    data_as_of = date.today().isoformat()
    cur.execute(
        """
        INSERT INTO meta
            (feed, data_as_of, feed_start_date, feed_end_date,
             route_count, station_count, source_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (FEED_ID, data_as_of, start, end, route_count, station_count, source_url),
    )
    print(
        f"[ingest] meta: feed={FEED_ID} as_of={data_as_of} "
        f"range={start}..{end} routes={route_count} stations={station_count}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    zip_bytes = download_feed()
    source_url = PRIMARY_URL

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        extracted = {}
        for gtfs_file in TABLES:
            if gtfs_file in names:
                extracted[gtfs_file] = zf.read(gtfs_file)
        if not extracted:
            raise RuntimeError("zip contained no recognised GTFS files")
        print(f"[ingest] feed files: {sorted(extracted)}")

    print(f"[ingest] connecting to postgres {DB['host']}:{DB['port']}/{DB['dbname']}")
    conn = psycopg2.connect(**DB)
    try:
        load(conn, extracted, source_url)
    finally:
        conn.close()
    print("[ingest] done.")


if __name__ == "__main__":
    main()
