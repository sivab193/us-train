"""Conversions for GTFS clock values, including times after 24:00."""

from __future__ import annotations


def to_readable(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def parse_time(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid time '{value}', expected HH:MM or HH:MM:SS")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"invalid time '{value}', expected HH:MM or HH:MM:SS") from exc
    if hours < 0 or minutes not in range(60) or seconds not in range(60):
        raise ValueError(f"invalid time '{value}', expected HH:MM or HH:MM:SS")
    return hours * 3600 + minutes * 60 + seconds


def is_next_day(seconds: int | None) -> bool:
    return seconds is not None and seconds >= 86_400
