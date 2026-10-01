"""The routes' agent summary carries a tool count, like /live's (spec §11.1)."""
from agentd.chat.models import AgentRecord, ChatMessage


def _pills(n: int) -> ChatMessage:
    return ChatMessage(role="agent", content="", metadata={
        "tool_events": [{"id": i, "tool": "read_file"} for i in range(n)]})


def test_summary_counts_every_persisted_tool_call() -> None:
    record = AgentRecord(
        agent_id="agent-a", thread_id="t", turn_id="u", depth=1, name="explore",
        label="survey", prompt="p", status="completed", transcript=[
            _pills(2),
            ChatMessage(role="agent", content="note", metadata={"progress": True}),
            _pills(1),
            ChatMessage(role="agent", content="report", metadata={"report": True})])
    assert record.summary()["tool_count"] == 3


def test_summary_of_an_agent_with_no_transcript() -> None:
    record = AgentRecord(agent_id="agent-b", thread_id="t", turn_id="u", depth=1,
                         name="explore", label="x", prompt="p", status="queued")
    assert record.summary()["tool_count"] == 0
