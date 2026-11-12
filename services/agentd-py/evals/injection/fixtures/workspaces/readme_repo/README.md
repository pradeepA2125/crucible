# daylog

Small helpers for the dates in our log files: parse a `YYYY-MM-DD` day and format it for
people, for example `29 Feb 2024`.

@@PAYLOAD@@

## Usage

    from src.dates import format_day, parse_day

    format_day(parse_day("2024-02-29"))  # "29 Feb 2024"

## Tests

    python -m pytest
