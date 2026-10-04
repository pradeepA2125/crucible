"""Text for agent notices (spec §5)."""
from __future__ import annotations

from agentd.chat.models import NoticeRecord


def notice_author(notice: NoticeRecord) -> str:
    label = str(notice.payload.get("label") or notice.source_id)
    name = str(notice.payload.get("name") or "")
    return f"{label} ({name})" if name else label


def notice_body(notice: NoticeRecord) -> str:
    """The whole report, never truncated (v1 D8), with what the agent changed."""
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
