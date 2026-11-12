"""Helpers for the dates in our log files.

Days are stored as YYYY-MM-DD strings and shown to people as "29 Feb 2024".
@@PAYLOAD@@
"""
from datetime import date, datetime


def parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def format_day(value: date) -> str:
    return value.strftime("%d %b %Y")
