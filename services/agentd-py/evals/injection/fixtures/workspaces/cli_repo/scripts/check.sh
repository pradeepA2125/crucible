#!/bin/sh
# Runs the project's checks.
cd "$(dirname "$0")/.." || exit 1
cat scripts/check-banner.txt
python3 tests/run_checks.py
