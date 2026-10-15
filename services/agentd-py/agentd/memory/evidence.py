"""Bounded model input from raw evidence; never mutates or truncates stored sources."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from agentd.history_metadata import message_sequence
from agentd.memory.models import CompactionSegment
from agentd.subagents.framing import FRAME_END, strip_frames

_RESULT_ROLES = {"tool_result", "tool"}
_COMMAND_TOOLS = {"run_command", "start_session", "write_stdin", "kill_session"}
_SUMMARY_PREFIX = "[MEMORY] Summary of earlier conversation that was compacted:"
_NUMBERED_LINE = re.compile(r"^\s*(\d+):", re.MULTILINE)


@dataclass(frozen=True)
class EvidenceRow:
    text: str
    starts_turn: bool
    mseq: int | None
    priority: int
    category: str
    source_index: int = 0


@dataclass(frozen=True)
class _Call:
    tool: str
    args: Mapping[str, object]
    identity: str | None


def capped(text: str, head: int, tail: int = 0) -> str:
    """Keep the prescribed excerpt plus a marker that counts removed characters."""
    if len(text) <= head + tail:
        return text
    end = text[-tail:] if tail else ""
    return f"{text[:head]}\n[… {len(text) - head - tail} chars omitted …]\n{end}"


def _message(source: Mapping[str, object] | CompactionSegment) -> dict[str, object]:
    if isinstance(source, CompactionSegment):
        # Old untyped segments remain unknown; do not infer a role from arbitrary text.
        return {
            **source.metadata,
            "role": source.role or "unknown",
            "tool": source.tool or "",
            "mseq": source.mseq,
            "content": source.content,
        }
    return dict(source)


def _action(content: str, role: str) -> dict[str, object] | None:
    if role != "assistant":
        return None
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("type"), str) else None


def _calls(action: Mapping[str, object]) -> list[_Call]:
    nodes = action.get("calls") if action.get("type") == "tool_calls" else [action]
    result = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or not isinstance(node.get("tool"), str):
            continue
        args = node.get("args")
        identity = node.get("id")
        result.append(_Call(
            node["tool"], args if isinstance(args, dict) else {},
            str(identity) if identity is not None else None,
        ))
    return result


def _instruction_path(path: object) -> bool:
    return isinstance(path, str) and path.replace("\\", "/").rsplit("/", 1)[-1].lower() in {
        "agents.md", "skill.md",
    }


def _uncapped(text: str, head: int, tail: int = 0) -> str:
    return text


def _action_text(
    action: Mapping[str, object], include_thoughts: bool, *, capped_outputs: bool = True,
) -> str:
    cap = capped if capped_outputs else _uncapped
    fields = {key: value for key, value in action.items() if key != "thought"}
    calls = _calls(action)
    if calls:
        for key in ("calls", "tool", "args"):
            fields.pop(key, None)
    ops = fields.pop("patch_ops", None)
    text = cap(json.dumps(fields, ensure_ascii=False), 8000)
    for call in calls:
        descriptor = json.dumps({"tool": call.tool, "id": call.identity}, ensure_ascii=False)
        text += "\ncall: " + descriptor + "\nargs: " + cap(
            json.dumps(dict(call.args), ensure_ascii=False), 4000,
        )
    if isinstance(ops, list):
        edits = []
        for op in ops:
            if isinstance(op, dict):
                descriptor = json.dumps(
                    {"file": op.get("file"), "op": op.get("op")}, ensure_ascii=False,
                )
                change = {key: value for key, value in op.items() if key not in {"file", "op"}}
                edits.append(
                    descriptor + "\n" + cap(json.dumps(change, ensure_ascii=False), 4000, 1000),
                )
        text += "\n" + "\n".join(edits)
    if include_thoughts and isinstance(action.get("thought"), str):
        text += "\nthought: " + cap(str(action["thought"]), 8000)
    return text


def _result_call(message: Mapping[str, object], calls: list[_Call]) -> _Call | None:
    identity = message.get("id")
    tool = str(message.get("tool", ""))
    if identity is not None:
        return next(
            (call for call in calls if call.tool == tool and call.identity == str(identity)), None,
        )
    matching = [call for call in calls if call.tool == tool]
    # An unlabelled result in a same-tool batch is ambiguous; never attach a guessed path.
    return matching[0] if len(matching) == 1 else None


def _line_number(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def _read_header(content: str, read: Mapping[str, object], call: _Call | None) -> str:
    path = read.get("path") or (call.args.get("path") if call else None)
    numbered = _NUMBERED_LINE.findall(content)
    start = _line_number(read.get("delivered_start")) or (
        _line_number(numbered[0]) if numbered else None
    )
    end = _line_number(read.get("delivered_end")) or (
        _line_number(numbered[-1]) if numbered else None
    )
    total = _line_number(read.get("total_lines"))
    return (
        f"path={json.dumps(path, ensure_ascii=False) if path is not None else 'unavailable'} "
        f"delivered={start if start is not None else 'unavailable'}"
        f"–{end if end is not None else 'unavailable'} "
        f"file_lines={total if total is not None else 'unavailable'} "
        f"delivered_lines={max(0, end - start + 1) if start is not None and end is not None else 0}"
    )


def render_evidence(
    sources: Iterable[Mapping[str, object] | CompactionSegment],
    *,
    include_thoughts: bool = False,
    purpose: Literal["compaction", "curation"] = "compaction",
    capped_outputs: bool = True,
) -> list[EvidenceRow]:
    """Source order is unchanged. Priority is for the future curator's bounded selection."""
    cap = capped if capped_outputs else _uncapped
    rows: list[EvidenceRow] = []
    calls: list[_Call] = []
    for source_index, source in enumerate(sources):
        message = _message(source)
        role, tool = str(message.get("role", "")), str(message.get("tool", ""))
        content = str(message.get("content", ""))
        private = message.get("_evidence")
        private = private if isinstance(private, dict) else {}
        kind = private.get("kind")
        if kind in {"compaction_summary", "injected_memory", "recalled_memory"} or (
            content.startswith(_SUMMARY_PREFIX)
            or content.startswith("Possibly relevant memories from earlier sessions:\n")
            or (role in _RESULT_ROLES and tool == "recall")
        ):
            continue
        if purpose == "curation":
            content = strip_frames(content)
        action = _action(content, role)
        if role not in _RESULT_ROLES:
            calls = _calls(action) if action else []
        call = _result_call(message, calls) if role in _RESULT_ROLES else None
        header = [role]
        if tool:
            header.append("tool=" + tool)
        seq = message_sequence(message)
        header.append(f"mseq={seq if seq is not None else 'unavailable'}")
        for key in (
            "id", "call_id", "batch_id", "provider_call_id", "status", "is_error", "exit_code",
        ):
            if key in message:
                header.append(f"{key}={cap(str(message[key]), 160)}")
        category = "text"
        priority = 0 if role == "user" else 1
        if kind in {"skill_body", "agents_body"}:
            category = "instruction"
            body = "[instruction body omitted; reload from its source when needed]"
        elif action is not None:
            body = _action_text(
                action, include_thoughts and "thought" not in private,
                capped_outputs=capped_outputs,
            )
            if action.get("type") in {"tool_call", "tool_calls", "edit"}:
                priority = 3
                category = "action"
        elif role in _RESULT_ROLES:
            priority, category = 3, "tool"
            failed = message.get("is_error") is True or message.get("status") in {
                "failed", "cancelled", "pending", "running",
            } or content.startswith("Error:")
            read = message.get("delivered_read")
            read = read if isinstance(read, dict) else {}
            path = read.get("path") or (call.args.get("path") if call else None)
            if tool == "read_file":
                header.append(_read_header(content, read, call))
            if not failed and (tool == "read_skill" or (
                tool == "read_file" and _instruction_path(path)
            ) or kind in {"skill_body", "agents_body"}):
                category = "instruction"
                body = "[instruction body omitted; reload from its source when needed]"
            elif failed:
                body = cap(content, 800, 1200) if tool in _COMMAND_TOOLS else cap(
                    content, 1000, 1000,
                )
            elif tool == "read_file":
                category = "file"
                body = cap(content, 2000, 1000)
            elif tool in _COMMAND_TOOLS:
                category = "command"
                body = cap(content, 1600, 3200)
            elif tool in {"team_report", "dispatch_agents", "wait_agents", "team_status"}:
                priority, category = 2, "report"
                body = cap(content, 8000)
                if "<<<agent-content " in content and not body.rstrip().endswith(FRAME_END):
                    body += "\n" + FRAME_END
            elif tool == "edit":
                category = "edit"
                body = cap(content, 4000, 1000)
            else:
                body = cap(content, 1000, 1000)
        else:
            body = cap(content, 8000)
        thought = private.get("thought")
        if include_thoughts and isinstance(thought, str):
            body += "\nthought: " + cap(thought, 8000)
        if not body.strip():
            continue
        rows.append(EvidenceRow(
            "[" + " ".join(header) + "]\n" + body,
            role not in _RESULT_ROLES, seq, priority, category, source_index,
        ))
    return rows


def evidence_text(rows: Iterable[EvidenceRow]) -> str:
    return "\n\n".join(row.text for row in rows)


def evidence_turns(rows: Iterable[EvidenceRow]) -> list[str]:
    """Keep an action and its tool results together; do not cut a batch across folds."""
    turns: list[list[EvidenceRow]] = []
    for row in rows:
        if row.starts_turn or not turns:
            turns.append([])
        turns[-1].append(row)
    return [evidence_text(turn) for turn in turns]
