"""search_code (ripgrep) and search_semantic tool implementations."""
from __future__ import annotations

import asyncio
import json
import shutil
from asyncio.subprocess import PIPE
from pathlib import Path

from agentd.child_env import child_env
from agentd.tools.registry import ToolOutput

_MAX_MATCHES = 50
_MAX_OUTPUT_CHARS = 8000


def _line_count(path: Path) -> int | None:
    """Lines in a matched file, so the model can request the whole file or the right range
    in one read instead of guessing a range and asking again."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)


async def search_code(
    *,
    pattern: str,
    path_filter: str | None,
    context_lines: int,
    fixed_strings: bool,
    shadow_root: Path,
    ripgrep_cmd: str = "rg",
) -> ToolOutput:
    if not pattern:
        return ToolOutput(
            output=(
                "Error: the 'pattern' argument is required (the regex/text to search for). "
                'Example: search_code(pattern="def build_router"). '
                "Optional: path_filter (a glob like '*.py'), context_lines, fixed_strings."
            ),
            is_error=True,
        )
    # ripgrep runs from the root (below); a missing cwd would otherwise surface as the
    # FileNotFoundError that means "rg is not installed".
    if not Path(shadow_root).is_dir():
        return ToolOutput(output=f"Error: search root does not exist: {shadow_root}",
                          is_error=True)

    rg = shutil.which(ripgrep_cmd) or ripgrep_cmd
    cmd = [rg, "--json", "-C", str(max(0, context_lines))]
    if fixed_strings:
        cmd.append("--fixed-strings")
    if path_filter:
        glob = path_filter
        # Models routinely pass the search root itself (absolute or ".") as the
        # filter; as a glob it matches nothing and silently kills every search.
        # Drop a root-equal filter, relativize an absolute path under the root.
        root_str = str(shadow_root)
        if glob.rstrip("/") in (root_str, ".", "./"):
            glob = ""
        elif glob.startswith(root_str + "/"):
            glob = glob[len(root_str) + 1 :]
        # With an explicit absolute search root, ripgrep matches a glob containing "/"
        # against absolute paths, so "a/b/c.py" and "pkg/**/*.py" both match nothing.
        # Anchor every such relative filter with "**/" (live: "who calls X?" got no
        # matches for "services/agentd-py/**/*.py").
        if glob.startswith("./"):
            glob = glob[2:]
        if glob and "/" in glob and not glob.startswith(("**/", "/")):
            glob = "**/" + glob
        if glob:
            cmd += ["-g", glob]
    # Crucible's own state (log, DBs, shadows, artifacts) is never search material. rg
    # skips hidden dirs by itself, but a positive glob overrides that — "*.*" matches the
    # directory name ".crucible" (live: the agent got hits from its own agentd.log). It
    # goes LAST because rg lets a later glob win over an earlier one.
    cmd += ["-g", "!**/.crucible/state"]
    cmd += [pattern, str(shadow_root)]

    from agentd.tools.processes import bounded_communicate, kill_and_reap
    proc = None
    try:
        # ripgrep matches globs against paths relative to its cwd; the backend runs from
        # services/agentd-py, where "services/agentd-py/**/*.py" matched nothing (live).
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=PIPE, stderr=PIPE, env=child_env(), cwd=str(shadow_root))
        stdout, stderr = await asyncio.wait_for(bounded_communicate(proc), timeout=15)
    except asyncio.CancelledError:
        if proc is not None:
            await kill_and_reap(proc)
        raise
    except TimeoutError:
        if proc is not None:
            await kill_and_reap(proc)
        return ToolOutput(
            output="Error: search timed out after 15s", is_error=True, status="timed_out"
        )
    except FileNotFoundError:
        return ToolOutput(
            output=f"Error: ripgrep not found at '{ripgrep_cmd}'. Install ripgrep or set CRUCIBLE_RIPGREP_CMD.",
            is_error=True,
        )
    except Exception as exc:
        if proc is not None:
            await kill_and_reap(proc)
        return ToolOutput(output=f"Error: search failed: {exc}", is_error=True)

    # Parse ripgrep --json output into readable text
    capture_limited = len(stdout) >= 100_000
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    matches: list[str] = (
        ["[Search capture bounded; refine the query for omitted matches.]"]
        if capture_limited
        else []
    )
    current_file: str | None = None
    match_count = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")
        data = obj.get("data", {})

        if msg_type == "begin":
            path_obj = data.get("path", {})
            current_file = path_obj.get("text") if isinstance(path_obj, dict) else str(path_obj)
            if current_file:
                # Strip shadow root prefix to show relative paths
                try:
                    current_file = str(Path(current_file).relative_to(shadow_root))
                except ValueError:
                    pass
            line_count = _line_count(shadow_root / current_file) if current_file else None
            suffix = f" ({line_count} lines)" if line_count is not None else ""
            matches.append(f"\n--- {current_file}{suffix} ---")

        elif msg_type == "match":
            match_count += 1
            if match_count > _MAX_MATCHES:
                matches.append(f"... (truncated after {_MAX_MATCHES} matches)")
                break
            lines_obj = data.get("lines", {})
            text = lines_obj.get("text", "") if isinstance(lines_obj, dict) else ""
            line_num = data.get("line_number", "?")
            matches.append(f"  {line_num}: {text.rstrip()}")

        elif msg_type == "context":
            lines_obj = data.get("lines", {})
            text = lines_obj.get("text", "") if isinstance(lines_obj, dict) else ""
            line_num = data.get("line_number", "?")
            matches.append(f"  {line_num}| {text.rstrip()}")

    # Exit 2 with no match is a failed search, not an empty one. Reporting it as "No
    # matches" cost a model round each time; an invalid regex is retried as literal text.
    if proc is not None and proc.returncode == 2 and match_count == 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if not fixed_strings and "regex parse error" in stderr_text:
            literal = await search_code(
                pattern=pattern, path_filter=path_filter, context_lines=context_lines,
                fixed_strings=True, shadow_root=shadow_root, ripgrep_cmd=ripgrep_cmd)
            if literal.is_error:
                return literal
            note = (f"Note: {pattern!r} is not a valid regex, so it was searched as "
                    "literal text (fixed_strings).")
            return ToolOutput(output=f"{note}\n{literal.output}")
        return ToolOutput(output=f"Error: search failed: {stderr_text[:500]}", is_error=True)

    if not matches or match_count == 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            return ToolOutput(output=f"No matches found. (stderr: {stderr_text[:500]})")
        return ToolOutput(output="No matches found.")

    result = "\n".join(matches)
    if len(result) > _MAX_OUTPUT_CHARS:
        # Find the last visible line number so the model knows where to read_file from.
        last_line_num: str | None = None
        for chunk in reversed(matches):
            stripped = chunk.strip()
            if stripped and stripped[0].isdigit() and ("|" in stripped or ":" in stripped):
                last_line_num = stripped.split("|")[0].split(":")[0].strip()
                break
        hint = (
            " The result is cut off. Do NOT increase context_lines — it will not help. "
            "Instead call read_file with start_line/end_line around the line numbers shown above"
            + (f" (last visible line: {last_line_num})" if last_line_num else "")
            + " to read the section you need."
        )
        result = result[:_MAX_OUTPUT_CHARS] + f"\n...{hint}"
    return ToolOutput(output=result)


async def search_semantic(
    *,
    query: str,
    top_k: int,
    semantic_index: object,
) -> ToolOutput:
    if not query:
        return ToolOutput(output="Error: query is required", is_error=True)
    try:
        chunks = semantic_index.query(query, top_k=max(1, min(top_k, 20)))  # type: ignore[attr-defined]
    except Exception as exc:
        return ToolOutput(output=f"Error: semantic search failed: {exc}", is_error=True)

    if not chunks:
        return ToolOutput(output="No semantically similar code found.")

    lines: list[str] = []
    for sc in chunks:
        chunk = sc.chunk
        score = sc.score
        lines.append(f"\n[score={score:.3f}] {chunk.path}:{chunk.line_start}-{chunk.line_end}")
        if chunk.name:
            lines.append(f"  {chunk.kind}: {chunk.name}")
        lines.append(chunk.text_with_lines[:600])

    result = "\n".join(lines)
    if len(result) > _MAX_OUTPUT_CHARS:
        result = result[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return ToolOutput(output=result)
