"""Tool spans and the redaction of what they capture (observability spec §6.1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentd.sandbox.env import is_secret_env
from agentd.telemetry.redact import redact_args
from tests.test_telemetry_turn_spans import _controller, _scripted

ANSWER: dict[str, object] = {"type": "answer", "thought": "t", "answer": "done"}


def _read(path: str) -> dict[str, object]:
    return {"type": "tool_calls", "thought": "look",
            "calls": [{"id": "c1", "tool": "read_file", "args": {"path": path}}]}


def test_is_secret_env_is_public() -> None:
    assert is_secret_env("GITHUB_TOKEN", "x") and not is_secret_env("PATH", "/bin")


def test_redacts_secret_named_keys_and_env_assignments() -> None:
    out = redact_args({"command": "GITHUB_TOKEN=ghp_x123 gh pr list",
                       "headers": {"Authorization": "Bearer abc"}, "path": "a.py"})
    assert out["command"] == "GITHUB_TOKEN=[redacted] gh pr list"
    assert out["headers"] == "[redacted]" and out["path"] == "a.py"


def test_redacts_nested_keys_and_lists() -> None:
    out = redact_args({"args": {"api_key": "k", "items": [{"password": "p", "n": 1}]},
                       "authorization": "Bearer z", "cookie": "s=1"})
    assert out == {"args": {"api_key": "[redacted]", "items": [{"password": "[redacted]",
                                                                 "n": 1}]},
                   "authorization": "[redacted]", "cookie": "[redacted]"}


def test_redacts_url_credentials() -> None:
    assert redact_args({"url": "https://u:p@host/x"})["url"] == "https://[redacted]@host/x"
    assert redact_args({"command": "git clone https://tok@github.com/a/b"})["command"] \
        == "git clone https://[redacted]@github.com/a/b"


def test_plain_text_is_untouched() -> None:
    assert redact_args({"command": "pytest -q tests/test_a.py", "pattern": "KEY=value"}) \
        == {"command": "pytest -q tests/test_a.py", "pattern": "KEY=[redacted]"}


@pytest.mark.asyncio
async def test_tool_call_is_a_tool_span_under_the_turn(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    (tmp_path / "README.md").write_text("hello")
    ctrl, _store, tid = _controller(tmp_path, _scripted(_read("README.md"), ANSWER))
    await ctrl.handle_message(tid, "read it", channel_id="c1")
    spans = test_exporter.get_finished_spans()
    turn = next(s for s in spans if s.name == "chat.turn")
    tool = next(s for s in spans if s.name == "read_file")
    assert tool.attributes["openinference.span.kind"] == "TOOL"
    assert tool.attributes["tool.name"] == "read_file"
    assert tool.parent is not None and tool.parent.span_id == turn.context.span_id
    assert json.loads(tool.attributes["tool.parameters"]) == {"path": "README.md"}
    assert "hello" in tool.attributes["output.value"]
    assert tool.status.status_code.name == "UNSET"


@pytest.mark.asyncio
async def test_failed_tool_is_error_status(
    test_exporter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    ctrl, _store, tid = _controller(tmp_path, _scripted(_read("missing.py"), ANSWER))
    await ctrl.handle_message(tid, "read it", channel_id="c1")
    tool = next(s for s in test_exporter.get_finished_spans() if s.name == "read_file")
    assert tool.status.status_code.name == "ERROR"


@pytest.mark.asyncio
async def test_content_off_keeps_tool_name_only(
    test_exporter_no_content: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_ARTIFACTS_ROOT", raising=False)
    (tmp_path / "README.md").write_text("hello")
    ctrl, _store, tid = _controller(tmp_path, _scripted(_read("README.md"), ANSWER))
    await ctrl.handle_message(tid, "read it", channel_id="c1")
    tool = next(s for s in test_exporter_no_content.get_finished_spans()
                if s.name == "read_file")
    assert tool.attributes["tool.name"] == "read_file"
    assert "tool.parameters" not in tool.attributes and "output.value" not in tool.attributes


def test_redacts_inline_auth_headers() -> None:
    out = redact_args({"command": 'curl -H "Authorization: Bearer sk-abc123" https://x.io',
                       "args": ["-H", "Authorization: Bearer sk-abc123", "-H",
                                "X-Api-Key: k-999"]})
    text = json.dumps(out)
    assert "sk-abc123" not in text and "k-999" not in text
    assert "Authorization: [redacted]" in text


def test_tool_output_is_redacted(test_exporter: Any) -> None:
    from agentd.telemetry.spans import tool_span
    with tool_span("run_command", {"command": "cat", "args": [".env"]}) as traced:
        assert traced is not None
        traced.set_result("OPENAI_API_KEY=sk-live-XYZ\nDEBUG=1", False)
    (span,) = test_exporter.get_finished_spans()
    assert "sk-live-XYZ" not in span.attributes["output.value"]
    assert "DEBUG=1" in span.attributes["output.value"]
