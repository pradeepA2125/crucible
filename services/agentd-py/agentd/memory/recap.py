"""A bounded thread recap, written on the existing terminal controller action."""

from __future__ import annotations


def validate_recap(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("recap must be a string")
    value = value.strip()
    if not value or len(value) > 400 or len(value.splitlines()) > 5:
        raise ValueError("recap must be nonempty, at most 400 characters and five lines")
    return value
