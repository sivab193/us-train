# train.us.siv19.dev

A schedule-search service for Amtrak GTFS data, queryable by humans (CLI),
agents (MCP), and any REST client. **Phase 1 is schedule search only** — it
answers which trains run, from where, to where, on which dates, and what time
they call at each station. No fares, no seats, no booking, no live positions
(none of that is in GTFS).

## What's in the box

```
db/           GTFS schema.sql (9 tables + meta, integer-seconds times, indexes)
ingestion/    Python COPY-based GTFS loader for Amtrak (mdb-11)
api/          Python REST API (FastAPI + psycopg + Postgres) — the one query layer
mcp/          FastMCP server (5 tools, thin client over the API)
cli/          Typer CLI `train` (thin client over the API)
docker-compose.yml   postgres + api + mcp + one-shot ingest
DEPLOYMENT.md        hosting, daily refresh, Phase 2–4 seams
```

One rule keeps it simple: **the MCP server and the CLI never touch the
database.** They are thin clients over the REST API. One query implementation,
three ways to reach it.

## Architecture

```
   REST clients ─┐
   MCP agents  ──┼──▶  Python API (FastAPI) ──▶ PostgreSQL (GTFS + indexes)
   CLI `train` ──┘                               ▲
                                                 │ daily
                                       Python ingestion (mdb-11 zip)
```

The entire application stack is Python. The API uses FastAPI with an async
psycopg connection pool; ingestion uses psycopg COPY; MCP uses FastMCP; and
the CLI uses Typer. The GTFS schema, COPY-based ingestion, and index strategy
follow the reference repo [sivab193/india-public-transport](https://github.com/sivab193/india-public-transport).

## Quick start (Docker)

```bash
cp .env.example .env            # edit POSTGRES_PASSWORD at least
docker compose up -d postgres api mcp
docker compose run --rm ingest  # load the Amtrak feed (first time)
```

Then:

```bash
curl 'http://localhost:8080/api/v1/meta'
curl 'http://localhost:8080/api/v1/stations?q=chicago'
curl 'http://localhost:8080/api/v1/between?from=CHI&to=SEA&date=2026-12-23'
```

MCP is served at `http://localhost:8000/mcp` (Streamable HTTP). Point any MCP
client at that URL.

## API — Phase 1 endpoints

All under `/api/v1`. Times are in the feed timezone (America/New_York), and
GTFS times past 24:00 are rendered as e.g. `25:30` with a `+1 day` flag.

| Method | Endpoint | Answers |
| ------ | -------- | ------- |
| GET | `/api/v1/stations?q=chicago` | Station search by name or 3-letter code |
| GET | `/api/v1/stations/{id}` | One station + routes that serve it |
| GET | `/api/v1/stations/{id}/departures?date=&time=` | Departures on a date, optionally after a time |
| GET | `/api/v1/routes?type=rail\|bus` | All routes, filterable (excludes Thruway buses) |
| GET | `/api/v1/routes/{id}` | One route with its stations in order |
| GET | `/api/v1/between?from=CHI&to=SEA&date=` | Direct trains between two stations (Phase 1: direct only) |
| GET | `/api/v1/trips/{id}` | Full calling pattern of one trip |
| GET | `/api/v1/meta` | Feed version, date range, refresh time, counts |

`{id}` accepts a GTFS stop_id or an Amtrak 3-letter code (CHI, SEA). The API
resolves codes to ids and matches parent stations (not individual platforms).

Every response uses the envelope:

```json
{ "data": { "...": "..." }, "data_as_of": "2026-11-04", "feed": "mdb-11" }
```

List endpoints wrap `data` in `{ items, page, page_size, total, has_more }`.

## Data correctness notes

- **Times as integer seconds from midnight.** GTFS times legitimately exceed
  24:00:00 (25:30 = 1:30 AM the next service day). Integer seconds handle this
  with no timezone or wrap-around bugs; the API renders them for display.
- **Service days join `calendar` + `calendar_dates`.** A train runs on a date
  only if the weekday pattern says so *and* no removal exception applies, or if
  an explicit addition exception applies. The `/departures` and `/between`
  queries never filter on `calendar` alone.
- **Feed vintage on every response** via `data_as_of`, recorded by the
  ingestion job in the `meta` table.

## MCP tools

| Tool | Maps to |
| ---- | ------- |
| `search_stations` | `GET /api/v1/stations` |
| `get_departures` | `GET /api/v1/stations/{id}/departures` |
| `get_trains_between` | `GET /api/v1/between` |
| `get_trip_stops` | `GET /api/v1/trips/{id}` |
| `get_feed_status` | `GET /api/v1/meta` |

No auth in Phase 1 — add a token later if abuse appears, not before.

## CLI

```bash
pipx install ./cli
export TRAIN_API_BASE_URL=https://train.us.siv19.dev   # defaults to localhost:8080
train stations chicago
train departures CHI --date 2026-12-23 --after 08:00
train between CHI SEA --date 2026-12-23
train trip <trip-id>
train status
```

## Running and testing the API locally (without Docker)

```bash
cd api
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt  # Windows
.venv/Scripts/pytest
DATABASE_URL=postgresql://train:train@localhost:5432/train .venv/Scripts/python -m app
```

On macOS/Linux, use `.venv/bin/pip`, `.venv/bin/pytest`, and
`.venv/bin/python` instead. OpenAPI documentation is available at `/docs`.

## Hosting & refresh

See [DEPLOYMENT.md](DEPLOYMENT.md) for VPS and GCP options, the daily-refresh
cron, reverse-proxy TLS, and the Phase 2–4 seams (live map, tour planner,
Bivia comparison).

## What Phase 1 deliberately does not do

Fares, seat availability, booking (not in GTFS), live positions (needs a Phase
2 source validation), connections in `/between` (direct only until the Phase 3
planner), and non-Amtrak feeds. Amtrak only, so Phase 1 stays honest.
