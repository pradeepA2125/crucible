from datetime import date

from src.dates import format_day, parse_day


def test_parse_day() -> None:
    assert parse_day("2024-02-29") == date(2024, 2, 29)


def test_format_day() -> None:
    assert format_day(date(2024, 2, 29)) == "29 Feb 2024"
