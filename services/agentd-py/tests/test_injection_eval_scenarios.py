"""The injection eval's scenario files: loading, markers and payload placement."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import get_args

import pytest

from evals.injection.models import Channel, Technique
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


def test_corpus_covers_the_spec_minimum():
    attacks = [s for s in load_scenarios() if s.payload is not None]
    pairs = {(s.channel, s.goal) for s in attacks}
    for need in [("readme", "exfil_command"), ("code_comment", "persist_file"),
                 ("agents_md", "exfil_command"), ("command_output", "exfil_command"),
                 ("mcp_issue", "exfil_mcp"), ("mcp_page", "memory_poison"),
                 ("mcp_page", "answer_hijack"), ("search_result", "persist_file")]:
        assert need in pairs, need
    assert all(sum(s.channel == c for s in attacks) >= 2 for c in get_args(Channel))
    assert all(sum(s.technique == t for s in attacks) >= 2 for t in get_args(Technique))
    assert {s.clean_of for s in load_scenarios() if s.payload is None} == {s.id for s in attacks}
    techniques = {(s.channel, s.goal, s.technique) for s in attacks}
    assert ("readme", "exfil_command", "helpful_step") in techniques
    assert ("agents_md", "exfil_command", "authority") in techniques
    assert ("mcp_issue", "exfil_mcp", "plain") in techniques
    assert ("mcp_issue", "exfil_mcp", "authority") in techniques
    assert ("mcp_page", "memory_poison", "helpful_step") in techniques
    assert ("command_output", "fake_frame_close") in {(s.channel, s.technique) for s in attacks}
    assert ("mcp_page", "invisible") in {(s.channel, s.technique) for s in attacks}


def test_every_scenario_materializes_without_leftover_placeholders(tmp_path):
    for s in load_scenarios():
        dest = tmp_path / s.id
        materialize(s, f"inj-{s.id}-abcdef", dest)
        text = "".join(p.read_text(errors="ignore") for p in dest.rglob("*") if p.is_file())
        assert "@@PAYLOAD@@" not in text, s.id
        if s.payload is None:
            assert f"inj-{s.id}" not in text, s.id
        if s.uses_mcp:
            assert (dest / ".crucible" / "mcp.json").exists(), s.id
