# Deployment plan — train.us.siv19.dev

Phase 1 is a small, stateless API plus one stateful database. This document
covers two hosting options (a single VPS, and GCP), TLS, the daily GTFS
refresh, backups, and the seams left open for Phases 2–4.

---

## 1. Shape of the deployment

```
                 Internet
                     │ 443 (TLS)
            ┌────────▼─────────┐
            │  reverse proxy   │   Caddy or nginx
            │  (TLS, routing)  │
            └───┬─────────┬────┘
   /api, /    │         │  /mcp
         ┌─────▼──┐  ┌───▼─────┐
         │  api   │  │  mcp    │   both stateless; scale horizontally
         │ :8080  │  │ :8000   │
         └────┬───┘  └────┬────┘
              │           │ (mcp calls api over HTTP)
              └─────┬─────┘
             ┌──────▼──────┐
             │  postgres   │  the only stateful piece
             │   :5432     │
             └──────▲──────┘
                    │ once a day, after 00:00 UTC
             ┌──────┴───────┐
             │ ingest (cron)│  pulls mdb-11, atomic swap
             └──────────────┘
```

Only Postgres holds state. The API and MCP are stateless and can be replicated
behind the proxy once traffic warrants it.

---

## 2. Domain & DNS

- `train.us.siv19.dev` is a subdomain of `siv19.dev`.
- Point an `A`/`AAAA` record at the host's public IP.
- The reverse proxy terminates TLS and routes:
  - `/` and `/api/*` → `api:8080`
  - `/mcp` → `mcp:8000`

Keeping both behind one hostname means MCP clients connect to
`https://train.us.siv19.dev/mcp` and REST clients to
`https://train.us.siv19.dev/api/v1/...`, no extra DNS needed.

---

## 3. Option A — single VPS (recommended for Phase 1)

A 2 vCPU / 2–4 GB VPS is comfortable: the Amtrak feed is ~17.7 MB and the
whole dataset fits in memory with room to spare.

### 3.1 Install

```bash
# On the VPS (Debian/Ubuntu):
curl -fsSL https://get.docker.com | sh
git clone <this repo> train && cd train
cp .env.example .env
# edit .env: set a strong POSTGRES_PASSWORD
```

### 3.2 Bring up the stack

```bash
docker compose up -d postgres api mcp
docker compose run --rm ingest      # first-time feed load
curl http://localhost:8080/api/v1/meta   # sanity check
```

### 3.3 Reverse proxy + TLS (Caddy — simplest)

`/etc/caddy/Caddyfile`:

```
train.us.siv19.dev {
    handle /mcp* {
        reverse_proxy localhost:8000
    }
    handle {
        reverse_proxy localhost:8080
    }
}
```

Caddy obtains and renews a Let's Encrypt certificate automatically. For nginx,
use `certbot` and proxy the same two path groups.

### 3.4 Daily GTFS refresh (host cron)

MobilityData re-checks the producer URL at ~00:00 UTC and mirrors a new
version when one appears. Run the ingestion job shortly after:

```cron
# /etc/cron.d/train-gtfs-refresh
# 00:30 UTC daily — load the latest Amtrak feed
30 0 * * *  root  cd /opt/train && /usr/bin/docker compose run --rm ingest >> /var/log/train-ingest.log 2>&1
```

The job downloads into staging tables and swaps atomically, so the API never
serves a half-loaded feed, and it records the dataset date in `meta`
(surfaced as `data_as_of` on every response).

---

## 4. Option B — GCP (per the reference repo's pattern)

Mirrors the India repo's GCP deployment guide, adapted to the Rust API.

- **Database:** Cloud SQL for PostgreSQL 16 (db-g1-small is plenty). Apply
  `db/schema.sql` once via `gcloud sql` or a migration step.
- **API & MCP:** two Cloud Run services built from `api/Dockerfile` and
  `mcp/Dockerfile`. Cloud Run is a natural fit — both are stateless HTTP.
  - API env: `DATABASE_URL` (or the `POSTGRES_*` parts) pointing at Cloud SQL
    via the Cloud SQL connector / private IP.
  - MCP env: `MCP_API_BASE_URL=https://<api-run-url>`.
- **Ingestion:** Cloud Run **Job** built from `ingestion/Dockerfile`, triggered
  daily by **Cloud Scheduler** at `30 0 * * *` UTC. Same env as the VPS cron.
- **TLS & domain:** map `train.us.siv19.dev` to the API Cloud Run service;
  route `/mcp` to the MCP service (via a load balancer or a second subdomain
  `mcp.train.us.siv19.dev` if path-routing is inconvenient on Cloud Run).

GCP trades a little setup complexity for managed backups, autoscaling, and no
host to patch. Phase 1 traffic does not require it; it is the better choice if
you expect the service to outlive a single box.

---

## 5. Option C — Mac mini (home server)

Viable if the Mac mini is already serving `siv19.dev` subdomains. Run the same
`docker compose` stack; put it behind the existing reverse proxy and add the
`/mcp` and `/api` routes. Use a `launchd` plist or `cron` for the daily
`docker compose run --rm ingest`. The only caveat is residential uptime and a
stable inbound path (Cloudflare Tunnel or a static IP).

---

## 6. Backups

Only Postgres needs backing up, and the GTFS data is fully reproducible from
the feed — so backups are about convenience, not data loss risk.

- **VPS:** nightly `pg_dump` to object storage, or snapshot the `pgdata`
  volume. Because `docker compose run --rm ingest` rebuilds the dataset from
  scratch, a lost database is recovered by re-running ingestion.
- **Cloud SQL:** enable automated backups + point-in-time recovery (one click).

---

## 7. Operations checklist

- `GET /health` → `ok` (liveness; wire it to the proxy / uptime monitor).
- `GET /api/v1/meta` → confirms a feed is loaded and shows `data_as_of`.
- Watch `/var/log/train-ingest.log` for the daily refresh. A failed download
  falls back to Amtrak's own URL automatically; if both fail the previous
  dataset stays live (atomic swap only commits on success).
- Rotate `POSTGRES_PASSWORD` by updating `.env` and `docker compose up -d`.

---

## 8. Security posture (Phase 1)

- **No accounts, no payments, no tracking.** Nothing sensitive to leak.
- **MCP is open** (open decision #4). If abuse appears, add a bearer token:
  set a shared secret in the proxy and require it on `/mcp`. Deferred until
  there is a reason.
- **Rate-limit generously** at the proxy (e.g. Caddy/nginx `limit_req`), tighten
  only if needed.
- Postgres is not exposed publicly in production — drop the `5432` port mapping
  from compose on the server (it is there for local dev convenience).

---

## 9. Seams for later phases (designed now, built later)

These are deliberately cheap to reach from the Phase 1 code.

### Phase 2 — Live map
- The schedule feed has **no** live positions, and Amtrak publishes no
  GTFS-Realtime feed in the Mobility Database. **Validate a position source
  first** (leading candidate: Amtrak's own train-tracker API — public-facing
  but unofficial, can change without notice).
- The `shapes` table is already ingested, so markers can be snapped to track
  geometry for a route once positions exist.
- Clicking a train would call the existing `/api/v1/trips/{id}` for its
  schedule and overlay the validated live status.

### Phase 3 — Multi-leg tour planner
- A **new** service behind the API, not a change to Phase 1 endpoints.
- New endpoint: `POST /api/v1/plan` (dates, start, optional must-visit
  stations) → time-dependent routing (earliest-arrival / fewest-transfers with
  minimum connection times) over the schedule graph already in Postgres.
- New MCP tool `plan_trip` wrapping it. The `/between` query (direct trains) is
  the base case of this search.

### Phase 4 — Bivia integration + performance comparison
- The planner exposes **one decision function with swappable backends**
  (LLM-direct vs Bivia/Articul8-assisted). This is the measurement seam.
- Every planning request logs: backend used, latency, tokens, cost, and
  whether the returned itinerary validated against the schedule (checked
  mechanically via the Phase 1 queries, not by the model).
- Phase 4 ships a comparison page from those logs — same queries, both
  backends, latency/cost/correctness side by side. No benchmark claims without
  that table.

---

## 10. Open decisions (his call)

1. **Host:** VPS (Option A) is the least-effort path for Phase 1; GCP
   (Option B) if you want managed scaling/backups; Mac mini (Option C) if it is
   already fronting `siv19.dev`.
2. **Station codes:** confirmed at ingestion — Amtrak's 3-letter codes live in
   `stop_code`; the API resolves them via `resolve_station_id` (code → stop_id,
   preferring parent stations). No separate mapping table needed. Verify once
   against the loaded feed with `train stations chicago`.
3. **Live position source (Phase 2):** validate Amtrak's tracker API before any
   map work.
4. **MCP auth:** open in Phase 1; token later if abuse shows up (see §8).

