import json
from pathlib import Path

from tests.memory_v2_prompt_fixture import collect


def test_v2_teaching_schema_and_private_history_projection_are_stable():
    path = Path(__file__).parent / "goldens/controller_prompt_memory_v2.json"
    assert collect() == json.loads(path.read_text())
