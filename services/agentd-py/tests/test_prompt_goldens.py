"""Main-agent byte identity (spec §4.7.4). Only Plan 1A Task 11 may re-capture."""
import json

from tests.prompt_goldens import GOLDEN_PATH, collect


def test_main_agent_text_matches_goldens() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    live = collect()
    assert sorted(live) == sorted(golden), "golden keys changed — re-capture is Task 11 only"
    mismatched = [key for key in golden if live[key] != golden[key]]
    assert not mismatched, f"main-agent text changed for: {mismatched}"
