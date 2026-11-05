"""The injection eval's scenario files: loading, markers and payload placement."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from evals.injection.scenarios import (
    ScenarioError,
    load_scenarios,
    materialize,
    new_marker,
    render_payload,
)


def test_every_scenario_loads_and_is_well_formed():
    scenarios = load_scenarios()
    assert scenarios, "no scenarios"
    ids = {s.id for s in scenarios}
    for s in scenarios:
        assert s.workspace.is_dir()
        if s.payload is not None:
            assert "{marker}" in s.payload
        else:
            assert s.clean_of in ids


def test_marker_shape_and_uniqueness():
    a, b = new_marker("readme_exfil_plain"), new_marker("readme_exfil_plain")
    assert re.fullmatch(r"inj-readme_exfil_plain-[0-9a-f]{6}", a) and a != b


def test_materialize_puts_payload_in_target(tmp_path):
    s = next(x for x in load_scenarios() if x.id == "readme_exfil_plain")
    materialize(s, "inj-x-abc123", tmp_path / "w")
    text = (tmp_path / "w" / "README.md").read_text()
    assert "inj-x-abc123" in text and "@@PAYLOAD@@" not in text


def test_clean_twin_renders_no_payload(tmp_path):
    s = next(x for x in load_scenarios() if x.id == "readme_exfil_plain_clean")
    materialize(s, "inj-x-abc123", tmp_path / "w")
    text = (tmp_path / "w" / "README.md").read_text()
    assert "inj-" not in text and "@@PAYLOAD@@" not in text


def test_invisible_payload_has_no_visible_instruction():
    base = next(x for x in load_scenarios() if x.id == "readme_exfil_plain")
    s = replace(base, technique="invisible")
    out = render_payload(s, "inj-x-abc123")
    assert "inj-x-abc123" not in out
    assert any(0xE0000 <= ord(c) <= 0xE007F for c in out)


def test_bad_scenario_names_the_file(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: bad\nchannel: nowhere\n")
    with pytest.raises(ScenarioError, match="bad.yaml"):
        load_scenarios(tmp_path)
