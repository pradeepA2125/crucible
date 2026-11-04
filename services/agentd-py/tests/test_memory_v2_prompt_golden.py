import json
from pathlib import Path

import pytest

from tests.memory_v2_prompt_fixture import collect


@pytest.fixture(autouse=True)
def _injection_defense_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The goldens pin the shipped prompt, and the prompt-injection defense ships on.
    monkeypatch.setenv("CRUCIBLE_INJECTION_DEFENSE", "1")


def test_v2_teaching_schema_and_private_history_projection_are_stable():
    path = Path(__file__).parent / "goldens/controller_prompt_memory_v2.json"
    assert collect() == json.loads(path.read_text())
