"""A child's durable transcript (spec §5.5) and its subtree's files (spec §6.3)."""
import pytest

from agentd.chat.models import ChatMessage
from agentd.subagents.transcript import AgentTranscript
from agentd.subagents.write_log import WorkspaceWriteLog


@pytest.mark.asyncio
async def test_pills_update_in_place_until_sealed_and_every_write_is_stamped() -> None:
    saved: list[list[ChatMessage]] = []
    seq = {"n": 0}
    transcript = AgentTranscript(saved.append, lambda: seq["n"])
    seq["n"] = 2
    await transcript.upsert_pills([{"id": 1, "tool": "read_file"}], [])
    seq["n"] = 4
    await transcript.upsert_pills([{"id": 1, "tool": "read_file"}, {"id": 2, "tool": "ls"}],
                                  ["t"])
    assert len(transcript.messages) == 1
    assert transcript.messages[0].metadata["tool_events"][1]["tool"] == "ls"
    assert transcript.messages[0].metadata["seq"] == 4
    transcript.seal_pills()
    seq["n"] = 5
    transcript.append(ChatMessage(role="agent", content="note", metadata={"progress": True}))
    seq["n"] = 7
    await transcript.upsert_pills([{"id": 3, "tool": "search_code"}], [])
    assert [m.content for m in transcript.messages] == ["", "note", ""]
    assert transcript.last_seq() == 7
    assert all(m.id for m in transcript.messages)
    assert saved[-1] == transcript.messages  # every change persisted, whole list


def test_files_changed_by_is_the_union_over_the_given_agents() -> None:
    log = WorkspaceWriteLog()
    for agent in ("a", "b", "c"):
        log.register_agent(agent)
    log.note_promote("a", "A", "g", ["x.py"])
    log.note_promote("b", "B", "g", ["y.py", "x.py"])
    log.note_promote("c", "C", "g", ["z.py"])
    assert log.files_changed_by({"a", "b"}) == ["x.py", "y.py"]
    assert log.files_changed_by({"nobody"}) == []
