import json

import pytest

from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.history_metadata import project_history
from agentd.planning.prompts import build_planning_step_payload
from agentd.providers.anthropic_transport import _anthropic_user_content
from agentd.providers.contracts import payload_without_image_attachments
from agentd.providers.gemini_transport import _gemini_contents
from agentd.providers.openai_compatible_transport import _openai_user_content
from agentd.providers.openai_transport import OpenAIJsonTransport
from agentd.reasoning.react_common import assistant_turn
from agentd.reasoning.tool_prompts import build_tool_step_payload


def evidence_history():
    history = [
        assistant_turn(
            {"type": "answer", "answer": "public", "thought": "PRIVATE_INTENT"},
            capture_evidence=True,
        )
    ]
    history[0]["mseq"] = 12345
    return history


def test_projection_does_not_mutate_history_or_ordinary_action_arguments():
    history = evidence_history()
    public = project_history(history)
    assert public == [assistant_turn({"type": "answer", "answer": "public"})]
    assert history[0]["mseq"] == 12345
    payload = {"conversation_history": history, "args": {"mseq": 7, "_evidence": "user data"}}
    payload["args"]["conversation_history"] = [{"mseq": 8, "_evidence": "a file's JSON data"}]
    clean = payload_without_image_attachments(payload)
    assert clean["conversation_history"] == public
    assert clean["args"] == payload["args"]
    assert project_history(public) is public


@pytest.mark.parametrize(
    "builder",
    [
        lambda h: build_controller_step_payload({"goal": "g"}, h, [], phase="PLAN"),
        lambda h: build_planning_step_payload({"goal": "g"}, h, []),
        lambda h: build_tool_step_payload({"goal": "g"}, h),
    ],
)
def test_every_loop_payload_projects_private_metadata(builder):
    payload = builder(evidence_history())
    assert "PRIVATE_INTENT" not in json.dumps(payload)
    assert "12345" not in json.dumps(payload)
    assert payload["conversation_history"] == [
        assistant_turn({"type": "answer", "answer": "public"})
    ]


@pytest.mark.parametrize(
    "render",
    [
        lambda p: _anthropic_user_content(p, None),
        lambda p: str(_gemini_contents(p, None)),
        lambda p: _openai_user_content(p, None, False),
        lambda p: OpenAIJsonTransport(responses_client=object())._body("model", "sys", p),
        lambda p: OpenAIJsonTransport(responses_client=object(), plan_route=True)._body(
            "model", "sys", p
        ),
    ],
)
def test_provider_inputs_cannot_expose_private_history(render):
    body = str(render({"conversation_history": evidence_history()}))
    assert "PRIVATE_INTENT" not in body and "12345" not in body
    assert "public" in body


def test_projection_preserves_append_only_prefix_and_image_batch_fields():
    first = evidence_history()
    first.append(
        {
            "role": "tool_result",
            "tool": "read_file",
            "content": "image result",
            "image_refs": [{"asset_id": "image"}],
            "batch_id": "b1",
            "tool_call_id": "c1",
            "provider_call_id": "p1",
            "status": "completed",
            "mseq": 12346,
        }
    )
    prefix = json.dumps(project_history(first))[:-1]
    first.append({"role": "user", "content": "next", "mseq": 12347})
    assert json.dumps(project_history(first)).startswith(prefix)
    tool = project_history(first)[1]
    assert tool["image_refs"] == [{"asset_id": "image"}]
    assert (tool["batch_id"], tool["tool_call_id"], tool["provider_call_id"]) == ("b1", "c1", "p1")
