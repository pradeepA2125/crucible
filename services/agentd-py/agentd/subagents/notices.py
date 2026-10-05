"""Text for agent notices (spec §5)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from agentd.chat.models import NoticeRecord
from agentd.subagents.framing import frame


def notice_author(notice: NoticeRecord) -> str:
    if notice.source_kind == "team":
        return f"team {notice.payload.get('team_name') or notice.source_id}"
    label = str(notice.payload.get("label") or notice.source_id)
    name = str(notice.payload.get("name") or "")
    return f"{label} ({name})" if name else label


def notice_body(notice: NoticeRecord) -> str:
    """The whole report, never truncated (v1 D8), with what the agent changed. A team
    milestone is already compact (spec v2 §8.8)."""
    if notice.source_kind == "team":
        return str(notice.payload.get("body", ""))
    files = notice.payload.get("files_changed") or []
    lines = [f"status: {notice.payload.get('status', '')}"]
    if files:
        lines.append("files_changed: " + ", ".join(str(f) for f in files))
    lines.append("")
    lines.append(str(notice.payload.get("report", "")))
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    # The same rough rule the memory compactor's budget uses: about four characters a token.
    return len(text) // 4 + 1


@dataclass(frozen=True)
class Fold:
    text: str                       # "While you were away:" block, "" when nothing was folded
    folded: list[str]               # notice ids folded into the text
    overflow: list[NoticeRecord]    # reports past the budget: delivered one per iteration


def notice_fold_max_tokens() -> int:
    try:
        return max(0, int(os.environ.get("CRUCIBLE_NOTICE_FOLD_MAX_TOKENS", "8000")))
    except ValueError:
        return 8000


def build_fold(notices: list[NoticeRecord], max_tokens: int) -> Fold:
    """Reports up to the budget go into the message; the rest are named and follow one
    per iteration, never truncated (spec §5.2)."""
    if not notices:
        return Fold(text="", folded=[], overflow=[])
    blocks: list[str] = []
    folded: list[str] = []
    overflow: list[NoticeRecord] = []
    used = 0
    for notice in notices:
        kind = "team milestone" if notice.source_kind == "team" else "report"
        block = frame(notice_author(notice), kind, notice_body(notice))
        cost = estimate_tokens(block)
        if notice.source_kind == "agent" and used + cost > max_tokens:
            overflow.append(notice)
            continue
        used += cost
        blocks.append(block)
        folded.append(notice.notice_id)
    lines = ["While you were away:", *blocks]
    if overflow:
        lines.append("Also finished: " + ", ".join(notice_author(n) for n in overflow)
                     + " — their reports follow.")
    return Fold(text="\n".join(lines), folded=folded, overflow=overflow)
