"""What the Auto judge sees (spec §6.3).

Authorization comes only from the user: their messages, AGENTS.md, their clarify answers
and plans they approved (recorded by the server as breadcrumb `metadata.authorization`).
Tool results, file contents, web/MCP output, sub-agent reports and the agent's own text
are never read here, so injected content can neither argue with the judge nor grant
itself permission. The agent's tool *calls* are kept as the action log.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from agentd.chat.models import ChatMessage
from agentd.context_metrics import count_text_tokens

AUTHORIZATION_TOKEN_CAP = 4000
AGENTS_MD_MAX_CHARS = 16000
CALL_SUMMARY_MAX_CHARS = 300
ACTION_LOG_MAX_ENTRIES = 200
# Patch ops carry whole file contents; the judge only needs which files changed.
_EDIT_ACTIONS = frozenset({"edit"})


@dataclass(frozen=True)
class JudgeContext:
    authorizations: tuple[str, ...]   # oldest → newest, newest kept under the cap
    agents_md: str
    action_log: tuple[str, ...]       # the agent's tool calls, no results
    history_anchor: str               # the thread's rewind generation
    # Counts before capping: a judge session sends only what is new since its last
    # judgment, and a count that shrinks (rewind, compaction) restarts it.
    authorization_total: int = 0
    action_total: int = 0


def approx_tokens(text: str) -> int:
    counted = count_text_tokens(text)
    return counted if counted is not None else len(text) // 3


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_call(tool: str, args: dict[str, object]) -> str:
    if tool == "run_command":
        raw_args = args.get("args")
        words = [str(args.get("command", ""))]
        if isinstance(raw_args, list):
            words.extend(str(a) for a in raw_args)
        return _cap("run_command: " + " ".join(w for w in words if w), CALL_SUMMARY_MAX_CHARS)
    return _cap(f"{tool}({json.dumps(args, default=str, sort_keys=True)})",
                CALL_SUMMARY_MAX_CHARS)


def _edited_paths(action: dict[str, object]) -> list[str]:
    ops = action.get("patch_ops")
    paths: list[str] = []
    if isinstance(ops, list):
        for op in ops:
            if isinstance(op, dict):
                path = op.get("file") or op.get("path")
                if isinstance(path, str) and path not in paths:
                    paths.append(path)
    return paths


def _actions(entry: dict[str, object]) -> list[str]:
    try:
        action = json.loads(str(entry.get("content", "")))
    except (TypeError, ValueError):
        return []
    if not isinstance(action, dict):
        return []
    if action.get("type") in _EDIT_ACTIONS:
        paths = _edited_paths(action)
        return [_cap("edited " + ", ".join(paths), CALL_SUMMARY_MAX_CHARS)] if paths else []
    calls = action.get("calls")
    out: list[str] = []
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, dict) and isinstance(call.get("tool"), str):
                args = call.get("args")
                out.append(summarize_call(str(call["tool"]),
                                          args if isinstance(args, dict) else {}))
    return out


def _authorization_text(message: ChatMessage) -> str | None:
    if message.role == "user":
        return message.content
    record = (message.metadata or {}).get("authorization")
    if isinstance(record, dict) and isinstance(record.get("text"), str):
        return f"[{record.get('kind', 'approval')}] {record['text']}"
    return None


def _newest_under_cap(texts: list[str]) -> tuple[str, ...]:
    kept: list[str] = []
    budget = AUTHORIZATION_TOKEN_CAP
    for text in reversed(texts):
        cost = approx_tokens(text)
        if cost > budget:
            if not kept:
                # The newest message alone is over the cap: keep its tail end.
                kept.append(text[-budget * 3:])
            break
        kept.append(text)
        budget -= cost
    return tuple(reversed(kept))


def build_judge_context(messages: Sequence[ChatMessage], history: Sequence[dict[str, object]],
                        agents_md: str | None, *, history_anchor: str = "") -> JudgeContext:
    # Authorization comes only from the persisted thread messages. Loop history's user-role
    # entries are not the user: "New message:" items are framed sub-agent reports and team
    # notes (agent-written), and the user's own mid-turn messages are persisted messages too.
    texts = [t for t in (_authorization_text(m) for m in messages) if t]
    log: list[str] = []
    for entry in history:
        if entry.get("role") == "assistant":
            log.extend(_actions(entry))
        # tool_result and user-role history entries are never read (spec §6.3).
    return JudgeContext(
        authorizations=_newest_under_cap(texts),
        agents_md=(agents_md or "")[:AGENTS_MD_MAX_CHARS],
        action_log=tuple(log[-ACTION_LOG_MAX_ENTRIES:]),
        history_anchor=history_anchor,
        authorization_total=len(texts),
        action_total=len(log),
    )
