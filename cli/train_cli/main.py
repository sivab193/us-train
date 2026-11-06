"""
train — terminal client for train.us.siv19.dev.

A thin client over the REST API (architecture §6). It prints human-readable
tables; it holds no SQL and no GTFS logic. Set TRAIN_API_BASE_URL to point at
a different API instance.

Usage:
    train stations chicago
    train departures CHI --date 2026-12-23 --after 08:00
    train between CHI SEA --date 2026-12-23
    train trip <trip-id>
    train status
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

API_BASE_URL = os.environ.get("TRAIN_API_BASE_URL", "http://localhost:8080").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("TRAIN_HTTP_TIMEOUT", "15"))

app = typer.Typer(
    add_completion=False,
    help="Query Amtrak schedules from the terminal (train.us.siv19.dev).",
)
console = Console()
err = Console(stderr=True)


def _get(path: str, params: Optional[dict] = None) -> dict:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    url = f"{API_BASE_URL}{path}"
    try:
        resp = httpx.get(url, params=clean, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        err.print(f"[red]network error:[/red] {e}")
        raise typer.Exit(code=2)
    if resp.status_code == 404:
        err.print("[yellow]not found[/yellow]")
        raise typer.Exit(code=1)
    if resp.status_code >= 400:
        err.print(f"[red]error {resp.status_code}:[/red] {resp.text}")
        raise typer.Exit(code=1)
    return resp.json()


def _footer(payload: dict) -> None:
    as_of = payload.get("data_as_of")
    feed = payload.get("feed")
    if as_of or feed:
        console.print(f"[dim]data_as_of: {as_of or '?'}   feed: {feed or '?'}[/dim]")


@app.command()
def stations(query: str = typer.Argument(..., help="Name or 3-letter code")):
    """Search stations by name or Amtrak code."""
    payload = _get("/api/v1/stations", {"q": query, "page_size": 25})
    data = payload["data"]
    table = Table(title=f"Stations matching '{query}'")
    table.add_column("Code", style="cyan")
    table.add_column("Name")
    table.add_column("ID", style="dim")
    for s in data["items"]:
        table.add_row(s.get("code") or "-", s["name"], s["id"])
    console.print(table)
    console.print(f"[dim]{len(data['items'])} of {data['total']} shown[/dim]")
    _footer(payload)


@app.command()
def departures(
    station: str = typer.Argument(..., help="Station id or code (e.g. CHI)"),
    date: Optional[str] = typer.Option(None, help="ISO date YYYY-MM-DD"),
    after: Optional[str] = typer.Option(None, help="Only after HH:MM"),
):
    """Departures from a station on a date."""
    payload = _get(
        f"/api/v1/stations/{station}/departures",
        {"date": date, "time": after, "page_size": 50},
    )
    data = payload["data"]
    table = Table(title=f"Departures from {station}" + (f" on {date}" if date else ""))
    table.add_column("Dep", style="green")
    table.add_column("Train")
    table.add_column("Route")
    table.add_column("Headsign")
    for d in data["items"]:
        dep = d.get("departure_time") or "-"
        if d.get("departs_next_day"):
            dep += " (+1d)"
        table.add_row(
            dep,
            d.get("trip_short_name") or d.get("trip_id", ""),
            d.get("route_name") or d.get("route_id", ""),
            d.get("headsign") or "-",
        )
    console.print(table)
    console.print(f"[dim]{len(data['items'])} of {data['total']} shown[/dim]")
    _footer(payload)


@app.command()
def between(
    from_station: str = typer.Argument(..., metavar="FROM", help="Origin code/id"),
    to_station: str = typer.Argument(..., metavar="TO", help="Destination code/id"),
    date: Optional[str] = typer.Option(None, help="ISO date YYYY-MM-DD"),
):
    """Direct trains between two stations on a date."""
    payload = _get(
        "/api/v1/between",
        {"from": from_station, "to": to_station, "date": date},
    )
    data = payload["data"]
    table = Table(title=f"{from_station} -> {to_station}" + (f" on {date}" if date else ""))
    table.add_column("Dep", style="green")
    table.add_column("Arr", style="green")
    table.add_column("Duration")
    table.add_column("Train")
    table.add_column("Route")
    for t in data:
        dur = t.get("duration_minutes", 0)
        dur_s = f"{dur // 60}h{dur % 60:02d}m"
        arr = t.get("arrival_time") or "-"
        if t.get("overnight"):
            arr += " (+1d)"
        table.add_row(
            t.get("departure_time") or "-",
            arr,
            dur_s,
            t.get("trip_short_name") or t.get("trip_id", ""),
            t.get("route_name") or t.get("route_id", ""),
        )
    console.print(table)
    if not data:
        console.print("[yellow]No direct trains found (Phase 1 is direct-only).[/yellow]")
    _footer(payload)


@app.command()
def trip(trip_id: str = typer.Argument(..., help="Trip id")):
    """Full calling pattern of one trip."""
    payload = _get(f"/api/v1/trips/{trip_id}")
    data = payload["data"]
    console.print(
        f"[bold]{data.get('route_name') or data.get('route_id')}[/bold] "
        f"— trip {data.get('trip_short_name') or data['trip_id']} "
        f"({data.get('headsign') or ''})"
    )
    table = Table()
    table.add_column("#", style="dim")
    table.add_column("Station")
    table.add_column("Arr", style="green")
    table.add_column("Dep", style="green")
    for s in data["stops"]:
        arr = s.get("arrival_time") or "-"
        if s.get("arrives_next_day"):
            arr += " (+1d)"
        table.add_row(
            str(s["stop_sequence"]),
            f"{s['stop_name']} ({s.get('stop_code') or s['stop_id']})",
            arr,
            s.get("departure_time") or "-",
        )
    console.print(table)
    _footer(payload)


@app.command()
def status():
    """Feed vintage: version, date range, counts, last refresh."""
    payload = _get("/api/v1/meta")
    d = payload["data"]
    table = Table(title="Feed status")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("feed", str(d.get("feed")))
    table.add_row("data_as_of", str(d.get("data_as_of")))
    table.add_row("ingested_at", str(d.get("ingested_at")))
    table.add_row("service range", f"{d.get('feed_start_date')} .. {d.get('feed_end_date')}")
    table.add_row("routes", str(d.get("route_count")))
    table.add_row("stations", str(d.get("station_count")))
    table.add_row("timezone", str(d.get("timezone")))
    console.print(table)
    _footer(payload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
