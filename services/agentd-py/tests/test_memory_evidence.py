import copy
import json
from datetime import UTC, datetime

import pytest

from agentd.memory.evidence import capped, evidence_text, evidence_turns, render_evidence
from agentd.memory.models import CompactionSegment
from agentd.subagents.framing import frame


def action(tool="read_file", args=None):
    return {"role": "assistant", "mseq": 1, "content": json.dumps({
        "type": "tool_call", "tool": tool, "args": args or {"path": "src/module.py"},
    }), "_evidence": {"thought": "private intent"}}


def result(tool="read_file", content="observation", **metadata):
    return {"role": "tool_result", "mseq": 2, "tool": tool, "content": content, **metadata}


def test_history_and_segments_render_identically_without_mutating_sources():
    messages = [action(), result(content="1: value = 1\n2: done", delivered_read={
        "path": "src/module.py", "delivered_start": 1, "delivered_end": 2, "total_lines": 20,
    }, status="success", is_error=False, exit_code=0)]
    before = copy.deepcopy(messages)
    segments = [
        CompactionSegment(
            id=str(i), run_id="thread", seq=i, created_at=datetime.now(UTC),
            role=m["role"], tool=m.get("tool"), content=m["content"], mseq=m["mseq"],
            metadata={k: v for k, v in m.items() if k not in {"role", "tool", "content", "mseq"}},
        ) for i, m in enumerate(messages)
    ]
    rows = render_evidence(messages)
    assert rows == render_evidence(segments)
    text = evidence_text(rows)
    assert "path=\"src/module.py\"" in text
    assert "delivered=1–2 file_lines=20 delivered_lines=2" in text
    assert "status=success" in text and "is_error=False" in text and "exit_code=0" in text
    assert "private intent" not in text
    with_thoughts = evidence_text(render_evidence(messages, include_thoughts=True))
    assert with_thoughts.count("private intent") == 1
    assert len(evidence_turns(rows)) == 1  # action and result stay together
    assert messages == before


@pytest.mark.parametrize("tool,head,tail", [
    ("read_file", 2000, 1000), ("run_command", 1600, 3200), ("write_stdin", 1600, 3200),
    ("web_fetch", 1000, 1000), ("search_code", 1000, 1000), ("edit", 4000, 1000),
])
def test_typed_caps_and_exact_omission_counts(tool, head, tail):
    content = "HEAD" + "x" * 10000 + "TAIL"
    body = render_evidence([result(tool, content)])[0].text.split("\n", 1)[1]
    assert body.startswith(content[:head])
    assert f"{len(content) - head - tail} chars omitted" in body
    if tail:
        assert body.endswith(content[-tail:])
    else:
        assert "TAIL" not in body
    assert capped("short", head, tail) == "short"


def test_batch_read_identity_prevents_instruction_misclassification():
    response = {"role": "assistant", "content": json.dumps({
        "type": "tool_calls", "calls": [
            {"id": "a", "tool": "read_file", "args": {"path": "AGENTS.md"}},
            {"id": "b", "tool": "read_file", "args": {"path": "src/module.py"}},
        ],
    })}
    rows = render_evidence([
        response, result(id="b", content="CODE mentioning AGENTS.md"),
        result(id="a", content="SECRET INSTRUCTIONS"),
    ])
    assert "CODE mentioning AGENTS.md" in rows[1].text
    assert 'path="src/module.py"' in rows[1].text
    assert "SECRET INSTRUCTIONS" not in rows[2].text
    assert "reload" in rows[2].text
    ambiguous = render_evidence([response, result(content="UNLABELLED observation")])[-1]
    assert "UNLABELLED observation" in ambiguous.text and "path=unavailable" in ambiguous.text


def test_exclusions_are_narrow_and_errors_remain_evidence():
    rows = render_evidence([
        {"role": "user", "content":
            "[MEMORY] Summary of earlier conversation that was compacted:\nX"},
        result("recall", "OLD MEMORY"),
        {"role": "user", "content": "Possibly relevant memories are causing this bug"},
        result("read_skill", "SKILL BODY"),
        action(args={"path": "docs/AGENTS.md"}),
        result(content="Error: cannot read AGENTS.md", is_error=True),
        action(args={"path": ".agents/x/SKILL.md"}),
        result(content="SKILL FILE"),
    ])
    text = evidence_text(rows)
    assert "OLD MEMORY" not in text and "SKILL BODY" not in text and "SKILL FILE" not in text
    assert "Possibly relevant memories are causing this bug" in text
    assert "Error: cannot read AGENTS.md" in text
    assert text.count("instruction body omitted") == 2
    assert not render_evidence([{"role": "user", "content": "x", "_evidence": {
        "kind": "injected_memory",
    }}])
    tagged = evidence_text(render_evidence([{
        "role": "user", "content": "TAGGED BODY", "_evidence": {"kind": "agents_body"},
    }]))
    assert "TAGGED BODY" not in tagged and "instruction body omitted" in tagged
    failed_command = render_evidence([result(
        "run_command", "x"*3000 + "FAILED TEST TAIL", is_error=True, status="failed",
    )])[0].text
    assert failed_command.endswith("x"*1184 + "FAILED TEST TAIL")
    assert "status=failed" in failed_command and "is_error=True" in failed_command


def test_edits_retain_each_file_operation_and_optional_thought():
    response = {"role": "assistant", "content": json.dumps({
        "type": "edit", "thought": "intent", "patch_ops": [
            {"file": "src/one.py", "op": "create", "content": "FIRST" + "x" * 5000},
            {"file": "src/two.py", "op": "search_replace", "search": "old", "replace": "new"},
        ],
    })}
    text = evidence_text(render_evidence([response]))
    assert "src/one.py" in text and "src/two.py" in text and "search_replace" in text
    assert "FIRST" in text and "chars omitted" in text and "intent" not in text
    assert "intent" in evidence_text(render_evidence([response], include_thoughts=True))


def test_reports_keep_attribution_and_curator_excludes_frames():
    report = frame("bob", "report", "reported work\n" + "x" * 10000)
    text = evidence_text(render_evidence([result("team_report", report)]))
    assert 'author="bob"' in text and "reported work" in text and text.endswith("<<<end>>>")
    assert not render_evidence([result("team_report", report)], purpose="curation")


def test_legacy_unknown_metadata_and_malformed_read_metadata_are_not_invented():
    old = CompactionSegment(
        id="old", run_id="thread", seq=1, created_at=datetime.now(UTC), content="old text",
    )
    assert render_evidence([old])[0].text == "[unknown mseq=unavailable]\nold text"
    text = evidence_text(render_evidence([result(content="plain", delivered_read={
        "delivered_start": "unknown", "delivered_end": False, "total_lines": [],
    })]))
    assert "delivered=unavailable–unavailable file_lines=unavailable" in text
