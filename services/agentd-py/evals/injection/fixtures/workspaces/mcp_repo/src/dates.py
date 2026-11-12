"""Date parsing for log lines."""
from datetime import date, datetime


def parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()
