import pytest

from app.gtfs_time import is_next_day, parse_time, to_readable


def test_renders_gtfs_times_after_midnight() -> None:
    assert to_readable(91_800) == "25:30"
    assert to_readable(28_800) == "08:00"
    assert to_readable(None) is None


def test_parses_supported_time_formats() -> None:
    assert parse_time("08:00") == 28_800
    assert parse_time("25:30:00") == 91_800
    assert parse_time("") is None


@pytest.mark.parametrize("value", ["nope", "12", "12:60", "-1:00", "12:00:99"])
def test_rejects_invalid_times(value: str) -> None:
    with pytest.raises(ValueError):
        parse_time(value)


def test_detects_next_service_day() -> None:
    assert is_next_day(86_400)
    assert not is_next_day(86_399)
    assert not is_next_day(None)
