"""read_file tool implementation (path-traversal safe)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from agentd.tools.read_policy import DeliveredRead
from agentd.tools.registry import ToolOutput

_MAX_LINES = 2000
_MAX_OUTPUT_CHARS = 100_000
_MAX_INPUT_BYTES = 2_000_000


async def read_file(
    *,
    path: str,
    start_line: int | None,
    end_line: int | None,
    shadow_root: Path,
    max_lines: int = _MAX_LINES,
    max_chars: int = _MAX_OUTPUT_CHARS,
) -> ToolOutput:
    worker = asyncio.create_task(asyncio.to_thread(
        _read_file_sync,
        path=path,
        start_line=start_line,
        end_line=end_line,
        shadow_root=shadow_root,
        max_lines=max_lines,
        max_chars=max_chars,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A running thread cannot be cancelled. Keep the caller's workspace lease
        # until the read finishes, so an edit cannot race with a cancelled read.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


def _read_file_sync(
    *,
    path: str,
    start_line: int | None,
    end_line: int | None,
    shadow_root: Path,
    max_lines: int,
    max_chars: int,
) -> ToolOutput:
    if not path:
        return ToolOutput(output="Error: path is required", is_error=True)

    resolved = (shadow_root / path).resolve()
    # Path traversal guard
    try:
        resolved.relative_to(shadow_root.resolve())
    except ValueError:
        return ToolOutput(
            output=f"Error: path traversal rejected — '{path}' is outside the workspace",
            is_error=True,
        )

    if not resolved.exists():
        return ToolOutput(output=f"Error: file not found: {path}", is_error=True)
    if not resolved.is_file():
        return ToolOutput(output=f"Error: '{path}' is a directory, not a file", is_error=True)

    try:
        with resolved.open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
    except OSError as exc:
        return ToolOutput(output=f"Error reading '{path}': {exc}", is_error=True)
    input_limited = len(raw) > _MAX_INPUT_BYTES
    raw = raw[:_MAX_INPUT_BYTES]
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if input_limited and all_lines:
        all_lines.pop()  # never present the input-boundary fragment as a complete line
    total = None if input_limited else len(all_lines)
    start = max(1, start_line or 1)
    end = max(0, end_line) if end_line is not None else len(all_lines)
    selected_end = min(end, len(all_lines))
    # Reserve footer space BEFORE observing evidence; every delivered line stays complete.
    limit = max(0, min(_MAX_OUTPUT_CHARS, max_chars) - 350)
    numbered = []
    used = 0
    reason = "input_bytes" if input_limited else None
    for index in range(start - 1, selected_end):
        line = f"{index + 1:4d}: {all_lines[index]}"
        if len(numbered) >= min(_MAX_LINES, max_lines):
            reason = "lines"
            break
        if used + len(line) + bool(numbered) > limit:
            reason = "characters"
            break
        numbered.append(line)
        used += len(line) + (len(numbered) > 1)
    delivered_end = start + len(numbered) - 1 if numbered else None
    metadata = DeliveredRead(
        path=str(resolved.relative_to(shadow_root.resolve())),
        root=str(shadow_root.resolve()),
        revision=None if input_limited else hashlib.sha256(raw).hexdigest(),
        requested_start=start,
        requested_end=end_line,
        delivered_start=start if numbered else None,
        delivered_end=delivered_end,
        total_lines=total,
        truncated_by=reason,
    )
    output = "\n".join(numbered)
    if reason or not numbered:
        count = str(total) if total is not None else "unknown (input byte limit)"
        visible = f"{start}–{delivered_end}" if numbered else "none"
        output += (f"\n[Read delivery: file has {count} lines; requested {start}–"
                   f"{end_line if end_line is not None else 'end'}; delivered {visible}; "
                   f"limit: {reason or 'empty/out-of-range'}. Request only the missing region.]" )
    return ToolOutput(output=output, delivered_read=metadata)


async def list_directory(*, path: str, root: Path) -> ToolOutput:
    """List files and directories at path within root.

    Returns one entry per line: 'file  <name>' or 'dir   <name>'.
    Capped at 200 entries. Path traversal rejected.
    """
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return ToolOutput(output="Error: path traversal rejected", is_error=True)

    if not resolved.exists():
        return ToolOutput(output=f"Error: '{path}' does not exist", is_error=True)

    if not resolved.is_dir():
        return ToolOutput(output=f"Error: '{path}' is not a directory", is_error=True)

    entries = sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines: list[str] = []
    for entry in entries[:200]:
        kind = "file " if entry.is_file() else "dir  "
        lines.append(f"{kind}  {entry.name}")

    if not lines:
        return ToolOutput(output=f"(empty directory: {path})")

    return ToolOutput(output=f"Contents of {path}:\n" + "\n".join(lines))
