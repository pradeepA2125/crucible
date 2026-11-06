"""The request episode: a user turn plus every notice turn and continuation that follows it,
until the next user message (observability spec §6.3). Built only from what a run leaves
behind (turn traces, the transcript, agent records), so the judge never re-runs anything."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentd.chat.models import ChatMessage
from agentd.chat.storage import ChatThreadStore
from agentd.runtime.artifacts import chat_turn_artifacts_root

logger = logging.getLogger(__name__)

_EVENT_ARGS_CHARS = 200
_EVENT_OUTPUT_CHARS = 300
_REPORT_CHARS = 2000


@dataclass(frozen=True)
class TurnRecord:
    turn_id: str
    turn_kind: str
    goal: str
    outcome_kind: str
    outcome_text: str
    tool_events: list[dict[str, object]]
    started_at: str | None
    trace_id: str | None
    span_id: str | None


@dataclass(frozen=True)
class AgentSummary:
    label: str
    status: str
    report: str
    tool_count: int


@dataclass(frozen=True)
class Episode:
    thread_id: str
    request_id: str | None
    user_message: str
    turns: list[TurnRecord]
    agents: list[AgentSummary]
    final_answer: str
    diff: str
    # The episode's tool calls in order, from the transcript's pills (a turn trace keeps
    # them only for some outcomes), and the breadcrumbs: a rejected card, a stop.
    tool_events: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Trace:
    record: TurnRecord
    request_id: str | None
    at: datetime


def _read_traces(workspace_path: Path, thread_id: str,
                 artifacts_root: Path | None) -> list[_Trace]:
    root = (artifacts_root / "chat" / thread_id if artifacts_root is not None
            else chat_turn_artifacts_root(thread_id, "", workspace_path))
    traces: list[_Trace] = []
    for path in sorted(root.glob("*/turn-trace.json")):
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.debug("[eval] unreadable turn trace %s", path, exc_info=True)
            continue
        started = data.get("started_at")
        # Traces written before turn times were recorded fall back to the file's mtime.
        at = (datetime.fromisoformat(started) if isinstance(started, str)
              else datetime.fromtimestamp(path.stat().st_mtime, UTC))
        events = data.get("tool_events")
        traces.append(_Trace(TurnRecord(
            turn_id=str(data.get("turn_id") or path.parent.name),
            turn_kind=str(data.get("turn_kind") or "user"),
            goal=str(data.get("goal") or ""),
            outcome_kind=str(data.get("outcome_kind") or ""),
            outcome_text=str(data.get("outcome_text") or ""),
            tool_events=[e for e in events if isinstance(e, dict)] if isinstance(events, list)
            else [],
            started_at=started if isinstance(started, str) else None,
            trace_id=data.get("trace_id"), span_id=data.get("span_id")),
            request_id=data.get("request_id"), at=at))
    return traces


def _window(messages: list[ChatMessage], request_id: str | None,
            ) -> tuple[ChatMessage | None, datetime | None]:
    users = [m for m in messages if m.role == "user"]
    if not users:
        return None, None
    index = next((i for i, m in enumerate(users) if request_id and m.id == request_id),
                 len(users) - 1)
    following = users[index + 1].timestamp if index + 1 < len(users) else None
    return users[index], following


def _is_answer(message: ChatMessage) -> bool:
    meta = message.metadata or {}
    return (message.role == "agent" and message.type == "text" and bool(message.content.strip())
            and not meta.get("breadcrumb") and not meta.get("tool_events"))


def build_episode(store: ChatThreadStore, workspace_path: Path, thread_id: str, *,
                  request_id: str | None = None,
                  artifacts_root: Path | None = None) -> Episode:
    """`artifacts_root` overrides where turn traces are read from (a kept trial folder,
    read by a process whose CRUCIBLE_ARTIFACTS_ROOT points elsewhere)."""
    thread = store.get_thread(thread_id)
    messages = thread.messages if thread is not None else []
    request, until = _window(messages, request_id)
    start = request.timestamp if request is not None else None
    chosen_id = request.id if request is not None else request_id

    def in_window(at: datetime) -> bool:
        return (start is None or at >= start) and (until is None or at < until)

    traces = [t for t in _read_traces(workspace_path, thread_id, artifacts_root)
              if (t.request_id == chosen_id if t.request_id is not None else in_window(t.at))]
    traces.sort(key=lambda t: t.at)
    turn_ids = {t.record.turn_id for t in traces}
    window = [m for m in messages if in_window(m.timestamp) and m is not request]
    answers = [m.content for m in window if _is_answer(m)]
    diffs = [str(entry.get("unified_diff", ""))
             for m in window if m.type == "diff_card"
             and (m.metadata or {}).get("resolved") == "applied"
             for entry in (m.metadata or {}).get("diff_entries") or []
             if isinstance(entry, dict)]
    agents = [AgentSummary(label=a.label, status=a.status, report=a.report,
                           tool_count=sum(len((m.metadata or {}).get("tool_events") or [])
                                          for m in a.transcript))
              for a in store.list_agents(thread_id) if a.turn_id in turn_ids]
    final = answers[-1] if answers else (traces[-1].record.outcome_text if traces else "")
    pills = [e for m in window for e in (m.metadata or {}).get("tool_events") or []
             if isinstance(e, dict)]
    if not pills:
        pills = [e for t in traces for e in t.record.tool_events]
    notes = [m.content for m in window if (m.metadata or {}).get("breadcrumb")]
    return Episode(
        thread_id=thread_id, request_id=chosen_id,
        user_message=request.content if request is not None else "",
        turns=[t.record for t in traces], agents=agents, final_answer=final,
        diff="\n".join(d for d in diffs if d), tool_events=pills, notes=notes)


def _event_line(event: dict[str, object]) -> str:
    args = json.dumps(event.get("args") or {}, default=str, ensure_ascii=False)
    output = str(event.get("output") or "").replace("\n", " ")
    error = " [error]" if event.get("isError") or event.get("is_error") else ""
    return (f"  {event.get('tool', '?')}({args[:_EVENT_ARGS_CHARS]}){error} → "
            f"{output[:_EVENT_OUTPUT_CHARS]}")


def _render(episode: Episode, keep: int | None) -> str:
    """`keep` = how many tool calls to keep at each end (None keeps all)."""
    events = episode.tool_events
    total = len(events)
    kept = set(range(total)) if keep is None or 2 * keep >= total else \
        set(range(keep)) | set(range(total - keep, total))
    parts = [f"Request:\n{episode.user_message}", "Turns:"]
    parts.extend(f"[{n}] {t.turn_kind} turn, outcome {t.outcome_kind}: "
                 f"{t.outcome_text[:_EVENT_OUTPUT_CHARS]}"
                 for n, t in enumerate(episode.turns, start=1))
    first_omitted = min(set(range(total)) - kept, default=None)
    calls: list[str] = []
    for position, event in enumerate(events):
        if position in kept:
            calls.append(_event_line(event))
        elif position == first_omitted:
            calls.append(f"  [{total - len(kept)} tool events omitted]")
    parts.append("Tool calls:\n" + ("\n".join(calls) if calls else "  (none)"))
    if episode.notes:
        parts.append("Notes:\n" + "\n".join(f"- {n}" for n in episode.notes))
    if episode.agents:
        parts.append("Agents:")
        parts.extend(f"- {a.label} ({a.status}, {a.tool_count} tools): "
                     f"{a.report[:_REPORT_CHARS]}" for a in episode.agents)
    parts.append(f"Final answer:\n{episode.final_answer}")
    if episode.diff:
        parts.append(f"Diff:\n{episode.diff}")
    return "\n\n".join(parts)


def render_for_judge(episode: Episode, max_chars: int = 60_000) -> str:
    """The episode as plain text. Over budget, tool events in the middle go first: the
    opening reads and the closing checks say the most about how the request went."""
    text = _render(episode, None)
    if len(text) <= max_chars:
        return text
    low, high = 0, len(episode.tool_events) // 2
    while low < high:
        middle = (low + high + 1) // 2
        if len(_render(episode, middle)) <= max_chars:
            low = middle
        else:
            high = middle - 1
    text = _render(episode, low)
    if len(text) <= max_chars:
        return text
    marker = "\n[truncated]"
    return text[: max_chars - len(marker)] + marker
