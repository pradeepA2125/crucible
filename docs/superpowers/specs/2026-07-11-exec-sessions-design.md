# Exec Sessions — PTY-backed background processes for the chat controller

**Date:** 2026-07-11
**Status:** Approved (design review with user)
**Scope:** controller-only, flag-gated (`CRUCIBLE_EXEC_SESSIONS_ENABLED`, default OFF)

## Problem

The controller can plan, edit, and run one-shot commands, but it cannot run servers,
hold long-running processes across turns, or perform live smokes ("start the server,
curl /health, iterate"). `run_command` blocks until exit or timeout, so `npm run dev`
/ `uvicorn` / `python -m http.server` are impossible: the turn hangs until the 60s
timeout kills the run.

### Prior art surveyed (2026-07-11)

| Agent | Model | Lessons |
|---|---|---|
| Claude Code | `run_in_background` flag on Bash → shell id; `BashOutput(id)` returns only NEW output since last read + status; `KillShell(id)`. No PTY, no stdin. Killed at session end. | The incremental-read cursor is the polling contract that works. |
| Codex (unified exec) | Every exec is a PTY session. `exec_command` waits a yield time (clamped 250–30,000 ms); still-running → returns partial output + session id. `write_stdin(session_id, chars, yield_time)` sends input AND polls (empty chars = pure poll). 1 MiB buffer, 64 sessions, last-used timestamps. | Most capable (TUIs, REPLs, `git rebase -i`). Their top reported bugs: streaming commands never "finish" so turns hang, and cancelled tasks leak child processes. |
| opencode | No native support (open issue). Community plugins: `createBackgroundProcess`/`list`/`kill` tools, in-memory manager with states, ~100-line rolling buffer, session-scoped vs global, kill-all on shutdown. | Explicit lifecycle rules matter most; orphans are the #1 complaint. |

Consistent lessons adopted here: (1) never block the turn — return an id;
(2) poll = new-output-since-last-read; (3) lifecycle must be explicit, with
group-kill to avoid orphaned children; (4) PTY is what makes dev-server output
appear live (they line-buffer only when they see a tty).

## Decisions (user-approved)

- **Scope:** chat controller only. The task-loop `run_command` and `tools/shell.py`
  are untouched.
- **Capability:** full Codex model — PTY sessions **including `write_stdin`**
  interactivity in v1.
- **Integration:** **additive** tools beside `run_command`, not a replacement.
  The model is taught: `run_command` for quick one-shots, sessions for anything
  long-running or interactive.
- **Lifecycle:** thread-scoped, survive across turns; killed on explicit
  `kill_session`, backend shutdown, and reaped-by-pgid after a crash.
- **Windows:** real PTY via `pywinpty` (conditional dependency), not a pipes fallback.

## Architecture

New self-contained module `agentd/exec_sessions/`:

```
agentd/exec_sessions/
  manager.py       # SessionManager — single owner of all live sessions
  pty_process.py   # one PTY-backed child per session (unix pty / pywinpty)
  tool_source.py   # ExecSessionToolSource — the 4 tools, controller-registered
  registry_file.py # on-disk crash-reap registry (.crucible/state/exec-sessions.json)
  config.py        # env resolution + clamps
```

`ExecSessionToolSource` is appended in `ChatController._build_registry` when
`is_exec_sessions_enabled()` — the `write_doc`/skills/MCP pattern (one
`sources.append(...)`). The `SessionManager` is built once in
`controller_factory.select_chat_handler` from the frozen `workspace_path` and
shared across turns/threads; FastAPI shutdown calls `manager.shutdown()`
(kill-all), startup calls `manager.reap_orphans()` — registered via
`app.add_event_handler`, the `McpConnectionManager` pattern.

### `pty_process.py`

- Unix: `pty.openpty()`; child spawned with `start_new_session=True` (own process
  group / session leader) and the slave fd as stdin/stdout/stderr; master fd read
  via `loop.add_reader` into the session's ring buffer. Window size set to a sane
  default (e.g. 200×50) so TUIs render.
- Windows: `pywinpty` (`pywinpty; sys_platform == 'win32'` in pyproject) with the
  same reader contract; group-kill maps to the winpty process-tree kill.
- Writes: `write(chars)` goes to the PTY master — chars are raw (the model can
  send `\n`, `\x03` for Ctrl-C, `y\n`, etc.).

### `manager.py`

- `sessions: dict[str, Session]`; `Session` carries `session_id`, `thread_id`,
  `command`, `pgid`, `started_at`, `status` (`running | exited`), `exit_code`,
  ring buffer + per-session read cursor.
- Ring buffer: 1 MiB per session (env-tunable). Reads return only bytes past the
  cursor, decoded `errors="replace"`, capped per tool result; cursor advances on
  read. Buffer overflow drops oldest bytes (a `[... output dropped]` marker is
  injected once per overflow episode).
- `start(...)` enforces the concurrency cap (default 16): at cap → error output
  telling the model to `kill_session` something first.
- `kill(session_id)`: `os.killpg(pgid, SIGTERM)`, grace period (2 s), then
  `SIGKILL` to the group. Group-kill is the deliberate fix for the Codex
  child-leak class.
- `shutdown()`: kill-all (same escalation), then clear the registry file.
- Exited sessions stay listed (status `exited`, exit code, buffer readable) so
  the model can read final output after death; an exited session is dropped once
  its remaining output has been read (cursor at end) or on `kill_session`, and
  unconditionally at manager shutdown.

### `registry_file.py`

`.crucible/state/exec-sessions.json`: `[{session_id, pid, pgid, thread_id,
command, started_at}]`, rewritten on every start/exit/kill (the `agentd.lock`
pattern). `reap_orphans()` at startup: for each recorded pgid still alive,
verify the command signature via `ps -o command= -p <pid>` matches before
`killpg` (best-effort — pid-reuse guard; Unix-only — on Windows the reap uses
pywinpty handles when available and otherwise just clears the file); always
ends by rewriting the file to the (empty) live set. All IO best-effort: failures log and degrade, never block
startup or a turn.

## Tool surface (4 tools, Codex semantics)

- **`start_session(command, args?, cwd?, yield_time_ms?)`** — spawn under a PTY as
  its own process group, in the **real workspace** (mirrors controller
  `run_command`, which already passes the workspace as its root —
  `controller.py:199`; binary resolution reuses `tools/_paths.py` helpers).
  Waits up to `yield_time_ms` (clamped 250–30,000, default 2,000). Exited within
  the yield → final output + exit code (behaves like a normal command). Still
  running → `session_id` + output so far + explicit "still running; poll or
  write_stdin" guidance in the tool result.
- **`write_stdin(session_id, chars, yield_time_ms?)`** — write `chars` to the PTY
  (empty string = pure poll), wait the yield, return only NEW output since the
  last read + status/exit code. This is both the interactivity and the polling
  tool.
- **`kill_session(session_id)`** — group SIGTERM → SIGKILL escalation; returns
  final unread output.
- **`list_sessions()`** — this thread's sessions: id, command, status, age,
  unread-buffer size.

Tool results ride the existing `ToolOutput` shape; `is_error=True` for unknown
session id, cap exceeded, spawn failure — the loop adapts, never crashes.

## Gating & phase availability

- `start_session` goes through the **existing command-approval gate**: the same
  `command_approval_callback` / `PendingGate(kind="command")` / `POST
  /command-decision` flow, so shell policy (`ask` / `allow_all`),
  accept-and-remember-for-workspace, and the decision timeout all apply
  unchanged. No new gate kind, no new Zod enum entry.
- `write_stdin`, `kill_session`, `list_sessions` are ungated — they operate on an
  already-approved process.
- **Deliberate divergence:** sessions are available in **both DECIDE and EDIT**
  (controller `run_command` is EDIT-only). Live smokes are often conversational
  ("start the server and hit /health") and must not force an edit session. Safe
  because every start is individually gated.

## Prompt teaching

`_SESSIONS_BLOCK` in `controller_prompts.py`, auto-appended when a
`start_session` tool def is present (the `_MCP_BLOCK` detection pattern — no new
parameter). Teaches: yield semantics; run_command vs sessions ("anything that
serves, watches, or prompts → session"); poll with empty `chars`; Ctrl-C is
`\x03`; **always `kill_session` what you started unless the user asked to keep
it running**; check `list_sessions` when resuming work on a thread.

## UI

- Live tool pills already render the calls (generic `tool_call` SSE path) —
  nothing new needed for in-turn visibility.
- `/live` (`ThreadLiveState`) gains `sessions: [{id, command, status, age_sec}]`
  so the webview shows a small read-only "● running: npm run dev" strip that
  survives reload. **`sessions` MUST be added to `lastLiveSignature` in
  `controller.ts`** (documented `/live` dedup footgun) and to the editor-client
  Zod schema + webview mirror types.
- **PTY inspect (expandable strip):** clicking a session row expands a read-only
  monospace scrollback. Backed by `GET
  /v1/chat/threads/{id}/sessions/{session_id}/transcript` → `{output_tail,
  stdin_history: [{ts, chars}], status, exit_code}` — fetched on expand,
  re-polled ~2 s while expanded (NOT part of `/live`, so the 1 s poll payload
  and `lastLiveSignature` are untouched). `output_tail` is a capped tail
  (default 16,000 chars) of the ring buffer; `stdin_history` is a capped list
  recorded on every `write_stdin` (control chars rendered escaped, e.g. `\x03`).
  **Invariant: the inspect read never advances the model's read cursor** — the
  model cursor and the inspect tail are independent views of the same ring
  buffer; otherwise inspecting a session would silently swallow output the
  model was about to poll.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `CRUCIBLE_EXEC_SESSIONS_ENABLED` | OFF (engine); ON via `start-backend.sh` | master flag; controller-only |
| `CRUCIBLE_EXEC_SESSION_MAX_COUNT` | `16` | concurrent sessions per backend |
| `CRUCIBLE_EXEC_SESSION_BUFFER_BYTES` | `1048576` | per-session ring buffer |
| `CRUCIBLE_EXEC_SESSION_DEFAULT_YIELD_MS` | `2000` | default wait, clamped 250–30,000 |
| `CRUCIBLE_EXEC_SESSION_RESULT_MAX_CHARS` | `4000` | per-tool-result output cap |

`/v1/config` gains `exec_sessions_enabled` (the `memory_enabled` pattern).
Dependency: `pywinpty; sys_platform == 'win32'`.

## Error handling

- Unknown / already-exited session → `is_error` tool output with the live session
  list appended (self-correcting).
- PTY read error / EIO on child exit → session marked `exited`, buffer stays
  readable; never raises into the loop.
- Spawn failure (`FileNotFoundError`, cap hit) → `is_error` output.
- Registry / reap IO failures → `logger.warning` + degrade (memory-harness
  discipline: auxiliary machinery must never break a turn).
- A turn ending (or `POST /stop`) does NOT kill sessions — that is the feature.

## Testing

- **Unit (real processes, no mocks — house style):** `SessionManager` against
  `python -c` sleepers/echoers on tmp cwds: yield-completes-fast-command;
  still-running returns id; cursor semantics (second poll returns only new
  output); ring overflow marker; group-kill reaps a grandchild (`python -c`
  spawning a child sleeper); cap enforcement; exited-session output readable;
  registry write/reap round-trip (fake stale entry with a live dummy process).
- **Controller integration:** scripted engine drives start → poll → write_stdin →
  kill through `ControllerLoop` with the command gate (approve + reject paths),
  DECIDE-phase availability, flag-off ⇒ tools absent.
- **Live smoke (plan exit criteria):** real backend + webview: "start
  `python -m http.server 8765`, curl it, then stop it" across two chat turns;
  verify the `/live` strip, reload persistence, and that backend shutdown leaves
  no orphan (`ps` check).

## Deferred (explicitly out of scope for v1)

- Task-loop (step execution) sessions.
- User-facing kill button in the webview strip (read-only in v1; the user can ask
  the model to kill).
- Cross-backend-restart session survival (Codex-style detach/re-adopt).
- Per-session log files on disk / artifact persistence of session transcripts.
