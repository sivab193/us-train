from ingest_gtfs import _date, _time_to_seconds, _text, _int, build_copy_buffer


def test_gtfs_time_conversion_allows_next_day() -> None:
    assert _time_to_seconds("25:30:00") == 91_800
    assert _time_to_seconds("") is None


def test_gtfs_date_conversion() -> None:
    assert _date("20261223") == "2026-12-23"
    assert _date("") is None


def test_copy_buffer_deduplicates_primary_keys() -> None:
    raw = (
        b"trip_id,stop_sequence,arrival_time\n"
        b"trip-1,1,25:30:00\n"
        b"trip-1,1,25:30:00\n"
    )
    definitions = [
        ("trip_id", "trip_id", _text),
        ("stop_sequence", "stop_sequence", _int),
        ("arrival_time", "arrival_time", _time_to_seconds),
    ]
    buffer, rows = build_copy_buffer(raw, "stop_times", definitions)
    assert rows == 1
    assert buffer.read() == "trip-1\t1\t91800\n"
