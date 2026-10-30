"""ExecSessionToolSource — PTY session tools for the controller.

start_session is gated through the SAME command-approval callback as
run_command (shell policy + remember-rules apply unchanged); write_stdin /
kill_session / list_sessions are ungated (they operate on an already-approved
process). Everything returns ToolOutput; is_error for unknown ids / cap /
spawn failures — the loop adapts, never crashes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentd.exec_sessions.config import result_max_chars
from agentd.exec_sessions.manager import (
    STDIN_MAX_CHARS,
    SessionCapError,
    SessionManager,
    SessionNotFoundError,
    SessionRead,
    SessionSpawnError,
)
from agentd.sandbox.blocks import annotate_block
from agentd.tools.approvals import CommandApprovalCallback, StdinApprovalCallback, denial_text
from agentd.tools.registry import ToolDefinition, ToolOutput

if TYPE_CHECKING:
    from agentd.sandbox.launch import SandboxLaunch
    from agentd.sandbox.runtime import SandboxRuntime

_TOOLS = ("start_session", "write_stdin", "kill_session", "list_sessions")

_STILL_RUNNING_GUIDE = (
    'Session {sid} is still running. Poll with write_stdin(session_id, '
    'chars="") or stop it with kill_session.')

class ExecSessionToolSource:
    name = "exec_sessions"

    def __init__(self, manager: SessionManager, thread_id: str,
                 command_approval_callback: CommandApprovalCallback,
                 stdin_approval_callback: StdinApprovalCallback | None = None,
                 sandbox_runtime: SandboxRuntime | None = None) -> None:
        self._manager = manager
        self._thread_id = thread_id
        self._approve = command_approval_callback
        self._sandbox = sandbox_runtime
        # Per session: how many refused hosts were already reported to the agent.
        self._refused_seen: dict[str, int] = {}
        # (session_id, session command line, chars) → outcome; only Auto mode sets it.
        self._approve_stdin = stdin_approval_callback

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
                session_id = str(args.get("session_id", ""))
                note: str | None = None
                if self._approve_stdin is not None:
                    command_line = self._manager.session_command(self._thread_id, session_id)
                    outcome = await self._approve_stdin(session_id, command_line, chars)
                    if not outcome.approved:
                        return ToolOutput(
                            output=denial_text(
                                outcome, subject=f"input to session {session_id}",
                                user_text=f"Input to session {session_id} rejected by user. "
                                          "Do not retry the same input — adapt or ask."),
                            is_error=True)
                    note = outcome.note
                launch = self._manager.session_sandbox(self._thread_id, session_id)
                read = await self._manager.write_stdin(
                    self._thread_id, session_id, chars, args.get("yield_time_ms"))
                output = self._render(read)
                self._annotate(output, read, launch)
                output.approval_note = note
                return output
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
        outcome = await self._approve(command, cmd_args, cwd)
        if not outcome.approved:
            cmdline = f"{command} {' '.join(cmd_args)}".strip()
            return ToolOutput(
                output=denial_text(
                    outcome, subject=f"command `{cmdline}`",
                    user_text=(f"Command rejected by user: {command}. Do not retry the "
                               "same command — adapt or ask.")),
                is_error=True)
        launch = (self._sandbox.launch_for(shadow_root=None)
                  if self._sandbox is not None else None)
        read = await self._manager.start(
            self._thread_id, command, cmd_args, cwd or None,
            args.get("yield_time_ms"), sandbox=launch)
        output = self._render(read)
        self._annotate(output, read, launch)
        output.approval_note = outcome.note
        return output

    def _annotate(self, output: ToolOutput, read: SessionRead,
                  launch: SandboxLaunch | None) -> None:
        """Name the sandbox when the new output shows it blocked something. Any input
        typed into the session counts, so the retry is always a new start_session."""
        if launch is None:
            return
        output.sandboxed = True
        output.exit_code = None if read.still_running else read.exit_code
        seen = self._refused_seen.get(read.session_id, 0)
        annotate_block(output, launch, "start_session", text=read.new_output,
                       refused_from=seen)
        if launch.lease is not None:
            self._refused_seen[read.session_id] = len(launch.lease.refused)

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
