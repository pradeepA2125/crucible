"""ExecSessionToolSource — PTY session tools for the controller.

start_session is gated through the SAME command-approval callback as
run_command (shell policy + remember-rules apply unchanged); write_stdin /
kill_session / list_sessions are ungated (they operate on an already-approved
process). Everything returns ToolOutput; is_error for unknown ids / cap /
spawn failures — the loop adapts, never crashes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agentd.domain.models import CommandDecision
from agentd.exec_sessions.config import result_max_chars
from agentd.exec_sessions.manager import (
    STDIN_MAX_CHARS,
    SessionCapError,
    SessionManager,
    SessionNotFoundError,
    SessionRead,
    SessionSpawnError,
)
from agentd.tools.registry import ToolDefinition, ToolOutput

_TOOLS = ("start_session", "write_stdin", "kill_session", "list_sessions")

_STILL_RUNNING_GUIDE = (
    'Session {sid} is still running. Poll with write_stdin(session_id, '
    'chars="") or stop it with kill_session.')

CommandApprovalCallback = Callable[[str, list[str], str], Awaitable[CommandDecision]]


class ExecSessionToolSource:
    name = "exec_sessions"

    def __init__(self, manager: SessionManager, thread_id: str,
                 command_approval_callback: CommandApprovalCallback) -> None:
        self._manager = manager
        self._thread_id = thread_id
        self._approve = command_approval_callback

    def definitions(self) -> list[ToolDefinition]:
        yield_prop = {"type": "integer", "description":
                      "How long to wait for output before returning, ms "
                      "(default 2000, clamped 250-30000)"}
        return [
            ToolDefinition(
                name="start_session",
                description=(
                    "Start a command in a PTY session (its own process group). "
                    "Use for anything long-running or interactive: dev servers, "
                    "watchers, REPLs, prompt-driven CLIs. If it exits within the "
                    "yield window you get the final output + exit code (like a "
                    "normal command); otherwise you get a session_id to poll via "
                    "write_stdin. Each start pauses for user approval — that "
                    "pause is expected. For quick one-shot commands prefer "
                    "run_command."),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string",
                                    "description": "Executable to run"},
                        "args": {"type": "array", "items": {"type": "string"},
                                 "description": "Arguments"},
                        "cwd": {"type": "string", "description":
                                "Workspace-relative working dir (default root)"},
                        "yield_time_ms": yield_prop,
                    },
                    "required": ["command"],
                }),
            ToolDefinition(
                name="write_stdin",
                description=(
                    "Send input to a running session AND/OR poll it: writes "
                    "`chars` to the PTY (empty string = pure poll, no write; "
                    "\\n submits a line; \\x03 is Ctrl-C — escape sequences "
                    "like \\x03 are decoded server-side, so sending the "
                    "literal characters works), waits the yield window, "
                    "returns only NEW output since your last read."),
                parameters={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "chars": {"type": "string", "description":
                                  "Raw chars to write; empty = just poll"},
                        "yield_time_ms": yield_prop,
                    },
                    "required": ["session_id"],
                }),
            ToolDefinition(
                name="kill_session",
                description=("Stop a session (SIGTERM then SIGKILL to its whole "
                             "process group). Returns any final unread output. "
                             "Always kill sessions you started unless the user "
                             "asked to keep them running."),
                parameters={
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"],
                }),
            ToolDefinition(
                name="list_sessions",
                description=("List this conversation's sessions: id, command, "
                             "running/exited, age, unread output size."),
                parameters={"type": "object", "properties": {}}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in _TOOLS

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == "start_session":
                return await self._start(args)
            if tool == "write_stdin":
                chars = str(args.get("chars", ""))
                if len(chars) > STDIN_MAX_CHARS:
                    return ToolOutput(
                        output=(f"Error: chars too large ({len(chars)} > "
                                f"{STDIN_MAX_CHARS}). stdin is for interactive "
                                "input, not bulk data — write a file instead."),
                        is_error=True)
                return self._render(await self._manager.write_stdin(
                    self._thread_id, str(args.get("session_id", "")),
                    chars, args.get("yield_time_ms")))
            if tool == "kill_session":
                return self._render(await self._manager.kill(
                    self._thread_id, str(args.get("session_id", ""))),
                    killed=True)
            if tool == "list_sessions":
                return self._render_list()
        except SessionNotFoundError as exc:
            return ToolOutput(output=str(exc), is_error=True)
        except (SessionCapError, SessionSpawnError) as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool '{tool}'", is_error=True)

    async def _start(self, args: dict[str, object]) -> ToolOutput:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolOutput(output="Error: start_session requires a command",
                              is_error=True)
        raw_args = args.get("args")
        cmd_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
        cwd = str(args.get("cwd") or "")
        decision = await self._approve(command, cmd_args, cwd)
        if not getattr(decision, "approve", False):
            return ToolOutput(
                output=(f"Command rejected by user: {command}. Do not retry the "
                        "same command — adapt or ask."), is_error=True)
        read = await self._manager.start(
            self._thread_id, command, cmd_args, cwd or None,
            args.get("yield_time_ms"))
        return self._render(read)

    def _render(self, read: SessionRead, killed: bool = False) -> ToolOutput:
        out = read.new_output[-result_max_chars():]
        if read.still_running:
            head = f"[session {read.session_id} — running]\n"
            tail = "\n" + _STILL_RUNNING_GUIDE.format(sid=read.session_id)
        else:
            verb = "killed" if killed else "exited"
            head = (f"[session {read.session_id} — {verb} "
                    f"(exit code: {read.exit_code})]\n")
            tail = ""
        return ToolOutput(output=head + out + tail)

    def _render_list(self) -> ToolOutput:
        rows = self._manager.list_sessions(self._thread_id)
        if not rows:
            return ToolOutput(output="Sessions: (none)")
        lines = [
            f"- {r['id']}: `{r['command']}` [{r['status']}"
            + (f", exit {r['exit_code']}" if r["exit_code"] is not None else "")
            + f"] age {r['age_sec']}s, {r['unread_bytes']}B unread"
            for r in rows]
        return ToolOutput(output="Sessions:\n" + "\n".join(lines))
