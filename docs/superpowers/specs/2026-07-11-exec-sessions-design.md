# Exec Sessions — PTY-backed background processes for the chat controller

**Date:** 2026-07-11
**Status:** Approved (design review with user); revised 2026-07-12 after a
pre-implementation dry-run review — spawn hardening reuse, stable `/live` rows,
deterministic drain-on-exit, win32 import guard, stdin escape decoding,
single-waiter task hygiene, non-blocking PTY writes (see "Review fixes" notes
inline and the expanded Deferred list).
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
- **Master fd is `O_NONBLOCK`** (reads AND writes). A stuffed PTY buffer (child
  not consuming stdin, ~64 KB kernel buffer) must never block the event loop —
  an `os.write` that would block would freeze the whole backend.
- **One persistent waiter task per process**, created at spawn; `wait(timeout)`
  races that single task via `asyncio.wait({task}, timeout=…)`. A fresh
  `wait_for(shield(proc.wait()), t)` per poll leaks one pending task per
  timed-out poll for the life of the session (50 polls on a dev server = 50
  abandoned tasks + "Task was destroyed" warnings at shutdown). Use
  `get_running_loop()`, never the deprecated `get_event_loop()`.
- **Deterministic drain-on-exit:** `proc.wait()` returning does NOT imply the
  reader callback already delivered the final output chunk. When `wait()`
  observes exit, a `drain()` pass reads the master fd (non-blocking) until
  empty/EOF/EIO before the session is reported `exited` — no sleep heuristics.
  Without this, fast commands race the reader (truncated final output) and
  `_drop_if_drained` can drop a session whose last chunk is still in flight,
  silently losing it. This is also the #1 future-flake source in tests.
- Windows: `pywinpty` (`pywinpty; sys_platform == 'win32'` in pyproject) with the
  same reader contract; group-kill maps to the winpty process-tree kill.
  **The platform guard must cover the Unix imports too** — `pty`, `fcntl`, and
  `termios` do not exist on win32, so an unguarded top-level import makes the
  module unimportable on Windows before the winpty branch is ever reached.
  The `WinPtyProcess` adapter must be written against the current pywinpty
  docs at implementation time (its `PTY.read` signature and env format differ
  from naive expectations) and explicitly marked untested-on-CI.
- Writes: `write(chars)` goes to the PTY master — chars are raw at this layer
  (escape decoding happens in the manager, see below). Each write is capped
  (~4 KB) and best-effort: a `BlockingIOError` (buffer full) drops the write
  with a warning rather than blocking the loop.

### `manager.py`

- `sessions: dict[str, Session]`; `Session` carries `session_id`, `thread_id`,
  `command`, `pgid`, `started_at`, `status` (`running | exited`), `exit_code`,
  ring buffer + per-session read cursor.
- Ring buffer: 1 MiB per session (env-tunable). Reads return only bytes past the
  cursor, decoded `errors="replace"`, capped per tool result; cursor advances on
  read. Buffer overflow drops oldest bytes (a `[... output dropped]` marker is
  injected once per overflow episode).
- **`start(...)` reuses `run_command`'s spawn hardening** (`tools/shell.py` +
  `tools/_paths.py`) — dropping it reintroduces three known, already-fixed
  failure classes:
  - `_split_command`: models routinely pack the whole line into `command`
    (`"python -m http.server 8765"`, `args=[]`); `create_subprocess_exec`
    does no word-splitting, so without the split every such spawn is a
    `FileNotFoundError`.
  - `resolve_workspace_bin` probe for naked binary names, so
    `start_session("uvicorn")` finds the workspace `.venv/bin/uvicorn`.
  - Venv env hygiene: `os.environ.copy()` leaks the backend's own
    `VIRTUAL_ENV` into the child; override `UV_PROJECT_ENVIRONMENT` and
    `VIRTUAL_ENV` to the workspace venv (the exact bug `shell.py` already
    fixes for `run_command`).
- **`write_stdin` decodes literal escape sequences** (`\n`, `\r`, `\t`,
  `\xNN`, `\uNNNN`, `\\`) before writing. JSON cannot carry raw control bytes
  except as `\uNNNN`, so a model following the `\x03` teaching emits
  backslash-x-0-3 as four literal characters — without decoding, Ctrl-C never
  fires and the PTY receives garbage text. Already-raw control chars pass
  through untouched. Input per call is capped (~4 KB; oversize → `is_error`
  tool output — stdin is for interactive input, not bulk data).
- `start(...)` enforces the concurrency cap (default 16): at cap → error output
  telling the model to `kill_session` something first. The cap is
  **backend-global** while `list_sessions` is thread-scoped, so the error text
  must say sessions from other conversations may be holding slots (a
  per-thread cap is deferred).
- `kill(session_id)`: `os.killpg(pgid, SIGTERM)`, grace period (2 s), then
  `SIGKILL` to the group. Group-kill is the deliberate fix for the Codex
  child-leak class.
- `shutdown()`: kill-all (same escalation) — kills run **concurrently**
  (`asyncio.gather`), not serially: a worst-case serial sweep is ~4 s × N
  sessions of shutdown stall — then clear the registry file.
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
  tool. Literal escape sequences in `chars` are decoded server-side (see
  manager section); oversize input (> ~4 KB) is an `is_error` output.
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
- `/live` (`ThreadLiveState`) gains `sessions: [{id, command, status, exit_code,
  started_at}]` so the webview shows a small read-only "● running: npm run dev"
  strip that survives reload. **`sessions` MUST be added to `lastLiveSignature`
  in `controller.ts`** (documented `/live` dedup footgun) and to the
  editor-client Zod schema + webview mirror types.
- **`/live` rows MUST be stable while nothing real changes.** The signature
  dedup only works if identical state serializes identically — a ticking
  `age_sec` (changes every second) or `unread_bytes` (changes on every log
  line the server emits) would make the signature differ on every 1 s poll,
  re-firing the entire render block at 1 Hz: exactly the churn the signature
  exists to prevent. So `/live` carries `started_at` (epoch seconds) and the
  webview computes the displayed age locally (display-only, never in app
  state); unread size stays a model-facing detail in `list_sessions` only and
  never rides `/live`.
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

### Accepted-risk warts (dry-run review 2026-07-12 — noted, not fixed in v1)

- **Cap is backend-global, visibility is thread-local:** at the cap, a thread
  that owns none of the sessions cannot free anything via `list_sessions`.
  Mitigated by the reworded cap error (says other conversations may hold
  slots); a per-thread cap is the real fix, deferred.
- **Exited-but-never-read sessions linger in the strip** until a later model
  turn's `list_sessions`/`write_stdin` drains them or `kill_session` fires (no
  dismiss button in v1). An auto-expiry (e.g. exited + unread for > N min) is
  deferred.
- **Pid-reuse reap guard matches only the first command token** — a recycled
  pid running the same interpreter binary would be killed. Rare enough to
  accept for a crash-recovery path.
- **Polls always wait the full yield** (no early-return-on-first-output);
  latency is mitigated by the prompt teaching ("longer yields, fewer polls").
- **UTF-8 code points split at a cursor boundary decode as replacement
  chars** (cosmetic; an incremental decoder per cursor fixes it if it ever
  matters).

## Live smoke 2026-07-12 (Task 12) — VERIFIED

Environment: managed-runtime backend (extension spawn, uvicorn+uvloop, editable
agentd) against `workspaces/crucible-stress`, provider TurboQuant
`qwen3.6:35b-a3b-q4_K_M`, dev host driven via raw CDP.

Verified end to end:
1. **start_session via command gate** — "start `python -m http.server 8765` in
   the background…" → gate rendered (`kind="command"`), Allow once → server
   spawned (whole-line `command` recovered by `_split_command`), poll output
   contained "Serving HTTP on :: port 8765", registry file recorded the session,
   `/live` carried the stable session row, webview strip showed `● running · Ns`.
2. **Expand transcript** — clicking the strip row expanded the PTY tail
   (server log incl. request lines) and it live-refreshed while open (a new
   request line appeared within the 2 s re-poll).
3. **Cross-turn survival + kill** — second turn: ModeGate (model chose
   run_command for curl) → curl 200 → `kill_session` → process gone, registry
   `[]`, `/live` sessions cleared, strip disappeared.
4. **No orphans** — `ps` clean after kill.
5. **Crash reap** — third turn started a server on :8766 (registry recorded),
   backend `kill -9`'d mid-session; extension crash-watcher respawned it; the
   orphan was already dead at respawn (PTY master close → kernel SIGHUP to the
   session's foreground group — a side effect of the TIOCSCTTY fix) and the
   startup reap ran and cleared the registry to `[]`. Belt (SIGHUP) and
   suspenders (reap) both exercised; the reap's kill path stays covered by
   `test_reap_kills_live_recorded_process`.

Two real bugs found by the smoke, both fixed + committed with regression tests:
- **uvloop spawn wedge (CRITICAL):** `asyncio.create_subprocess_exec` +
  `preexec_fn` under uvloop (the production uvicorn loop) wedged the forked
  child pre-exec and blocked the parent inside `uv_spawn`'s exec-status read —
  freezing the ENTIRE event loop (every HTTP request hung; observed live).
  Fixed: spawn via `subprocess.Popen` in a worker thread (CPython's
  thread-hardened `fork_exec`, loop-agnostic). Regression:
  `test_spawn_completes_under_uvloop` (real uvloop loop in a side thread).
  pytest-asyncio's vanilla loop could never catch this.
- **Managed runtime ran the feature dark:** `buildBackendEnv` (extension spawn
  env) lacked `CRUCIBLE_EXEC_SESSIONS_ENABLED=1` — only `start-backend.sh`/.env
  opted in. Added alongside the other chat-feature opt-ins + test assertion.

Known non-blocking observation: weak-model answer-emission attractor (model
emits `tool_call tool=answer` repeatedly before the loop corrects) showed up in
turn 2 — controller-loop behavior, not exec-sessions; already tracked by the
malformed-action correction work.
