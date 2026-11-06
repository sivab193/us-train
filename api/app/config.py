"""Environment-driven API configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    port: int
    feed_id: str
    feed_timezone: str
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            database_url = make_conninfo(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=os.getenv("POSTGRES_PORT", "5432"),
                dbname=os.getenv("POSTGRES_DB", "train"),
                user=os.getenv("POSTGRES_USER", "train"),
                password=os.getenv("POSTGRES_PASSWORD", "train"),
            )
        origins = tuple(
            origin.strip()
            for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
            if origin.strip()
        )
        return cls(
            database_url=database_url,
            port=int(os.getenv("API_PORT", "8080")),
            feed_id=os.getenv("GTFS_FEED_ID", "mdb-11"),
            feed_timezone=os.getenv("GTFS_FEED_TZ", "America/New_York"),
            cors_origins=origins,
        )
