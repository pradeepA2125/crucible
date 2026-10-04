# Backend authentication — design

**Status:** rev 2, 2026-10-05 — revised after a security review and an integration review (27 findings,
all addressed below). Separate from the sub-agents v2 spec (decision E17); it must land before v2 Phase 3
(teams) merges to `main`.

## 1. Problem

`agentd` serves an unauthenticated HTTP API on `127.0.0.1`. Anything that can reach that socket can drive
it: start chat turns, answer approval cards, accept edits, run commands through remembered allow rules,
stop or resume agents, rewind threads. Measured on the dev host, 2026-10-04:

- No Host check: `curl -H "Host: attacker.example:50115" http://127.0.0.1:50115/v1/config` → 200. A web page
  can reach the backend through **DNS rebinding** (its own domain re-pointed at 127.0.0.1, so the browser
  treats the requests as same-origin and reads the responses).
- No CORS middleware. Browsers block reading cross-origin responses, but a page can still **send** simple
  requests (CSRF).
- No token: any process on the machine — another OS user's included — can call every route.

Sub-agents v2 raises the stakes: agents keep working after the user's turn, notice turns start themselves,
and "Review each edit" can be off for all of them. Teams (Phase 3) multiply this.

## 2. Goals and non-goals

**Goals.** Only the Crucible extension host, the indexer watcher the backend's launcher starts, and tools
the user runs deliberately can use a backend. Web pages cannot (no DNS rebinding, no CSRF). Other OS users
cannot. Nothing changes for the user in the normal flow: no token to copy.

**Non-goals, stated plainly.**
- Code running as the same OS user. It can read the user's files — the token file included — attach to
  processes and drive VS Code itself. **This includes the agent's own tools:** a prompt-injected model with
  shell access (an approved or remembered command, or `shell_policy=allow_all`) can read the token and call
  `/edit-decision`, `/mcp-decision` or `/review-pref` to approve its own cards. Approval cards are a
  containment boundary only while the agent has no shell access; this design does not change that. §3.8
  adds a best-effort refusal of commands that name the token directory, which raises the bar without
  closing the gap.
- Remote access, TLS, multiple users, per-route permissions.

## 3. Design

### 3.1 Which port: `CRUCIBLE_LISTEN_PORT`

The token file is named by port, so the backend must know the port it serves. uvicorn's `--port` is not
visible to the app, and uvicorn runs lifespan startup before it binds, so the app cannot ask the socket.
Every launcher therefore sets **`CRUCIBLE_LISTEN_PORT`** from the same variable it passes to `--port`:

- the managed spawn (`buildBackendEnv` in `backend-process.ts`; it keeps setting `CRUCIBLE_PORT` too,
  which still means "managed: write the workspace lockfile" — the two stay separate so dev backends never
  enter the extension's reuse path);
- `scripts/stress/start-backend.sh` and `scripts/e2e-scripted.sh`;
- the documented manual command in CLAUDE.md becomes
  `CRUCIBLE_LISTEN_PORT=8000 uvicorn agentd.main:app --host 127.0.0.1 --port 8000`.

A mismatch fails closed: the Host check (§3.3) requires the Host header's port to equal
`CRUCIBLE_LISTEN_PORT`, so a backend told the wrong port answers every request with 421. If the variable is
missing, the first startup hook raises — startup aborts with a message naming the variable — unless auth is
disabled (§3.6). The check runs in a startup hook, not at import, so tests and tools that import
`agentd.main` are unaffected.

### 3.2 The token and its file

- In the first startup hook (synchronous, before any `in_background` work, before serving):
  `secrets.token_urlsafe(32)` (256 bits), new on every start, then written to
  **`<run_dir>/agentd-<port>.token`**. A failure to write aborts startup — a backend whose token nobody can
  read is useless.
- **`run_dir`** is `~/.crucible/run`, overridable by `CRUCIBLE_RUN_DIR` (tests only). One shared function
  computes it in Python and one in TypeScript, both from the same two inputs, so the backend and the
  extension always agree.
- **Directory hardening** before writing: `lstat` the directory; refuse (abort startup) if it is a symlink,
  not owned by the current user, or group/world-writable after an attempt to `chmod` it to `0700`; create it
  with mode `0700` if missing.
- **File write:** `os.open(tmp, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0o600)` on a random temp name in
  the same directory, write, `fsync`, `os.replace` onto the final name. A reader never sees a partial token.
  On Windows, `os.replace` over a file another process has open can raise `PermissionError`: retry three
  times with a short delay, then abort.
- **Deletion at shutdown is ownership-checked:** delete the file only if its contents equal this process's
  token (the same lesson as `clear_lock` in `runtime_lock.py`). uvicorn closes its listener before running
  shutdown hooks, so a successor can bind the same port and write its own file in between; an unconditional
  delete would remove it. A file left behind by a crash is harmless — it names a port nobody serves, or one
  whose new backend overwrote it.
- The token is never logged, never put in a response body, never placed in any process's environment (the
  backend's child processes — MCP servers, the indexer, `run_command` — therefore cannot see it there), and
  never written to the workspace or the lockfile.

### 3.3 The middleware

A pure ASGI middleware — not `BaseHTTPMiddleware`, which buffers and would break the SSE streams —
installed by `install_auth(app, port, token)` as the **outermost** layer, so a router or sub-app mounted
later cannot bypass it. By scope type:

- `lifespan` → passed through.
- `websocket` → closed with code 1008. There are no websocket routes; this fails closed for future ones.
- `http` → three checks, in order:
  1. **Host → 421 Misdirected Request.** Exactly one `Host` header, compared lowercase and exactly, port
     required: `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`. A missing Host, a duplicate Host, or
     an absolute-form request target (`GET http://… HTTP/1.1`) is refused. The 421 body says "use 127.0.0.1 or
     localhost"; `0.0.0.0` or the machine's hostname in `crucible.backendBaseUrl` now gets 421 (the setting's
     description says so). This check alone defeats DNS rebinding — the rebinding page's requests carry its
     own domain in Host.
  2. **Origin → 403.** Any request with an `Origin` header (including `Origin: null`) is refused. This
     blocks browser requests that send Origin (`fetch`/XHR, form POSTs). It does **not** stop a page's plain
     cross-site GETs (`<img>`, `<script>`, navigation), which send no Origin and carry
     `Host: 127.0.0.1:<port>` — the token (3) is what stops those. Rule recorded for route authors: **GET
     routes must have no side effects** (true today; a test enumerates the GET routes and fails on any that
     is registered as mutating — see §4).
  3. **Token → 401.** `Authorization` header, scheme `Bearer` matched case-insensitively, compared as bytes
     with `hmac.compare_digest` (a non-ASCII header value is a 401, not a 500). The body names what is
     missing, never the expected value.

`OPTIONS` gets no special treatment: with no CORS headers a browser preflight fails, which is the intent.

### 3.4 Identifying ourselves: `/health`

`/health` is authenticated like everything else and its body becomes
`{"status": "ok", "auth": "bearer"}`. The field lets a client tell an authenticated backend from a pre-auth
one: a pre-auth backend answers `/health` with 200 to any token, and lacks the field. §3.5 uses this.

### 3.5 Clients

Every caller, by component:

- **editor-client** (`http-backend-client.ts`). `HttpBackendClientOptions` gains
  `authToken: () => string | undefined`. The constructor wraps `fetchFn` itself, so **every** call — the raw
  `fetchFn` call sites (mode and clarify decisions, both SSE streams, the chat message, `discardInlineChange`)
  and `fetchJson` alike — carries `Authorization`, and no call site is exempt. A 401 raises a typed
  `BackendAuthError`. `discardInlineChange` starts checking `response.ok` (it ignores errors today).
- **Extension** (`apps/vscode-extension/src`):
  - `runtime/backend-token.ts` (vscode-free): `runDir()` (mirrors the Python one) and
    `readBackendToken(port)`. Before trusting a file it checks owner and mode (POSIX: owned by the current
    uid, not group/world-readable). It caches by `(port, mtimeMs)` — the client factory runs on nearly every
    controller action plus the 1 s `/live` poll — and on a 401 invalidates and re-reads once (a dev
    `--reload` rotates the token on the same port).
  - The client factory in `extension.ts` passes `authToken: () => readBackendToken(portOf(url))`.
  - `ProcessDeps.fetchJson` (managed spawn: health poll, index build) takes the header and merges it
    unconditionally (today it sets headers only when there is a body).
  - `settings.ts::checkBackendHealth` (explicit `crucible.backendBaseUrl`) sends the token.
  - `GraphPanel` takes a URL resolver and the token reader instead of a URL captured at construction.
- **Rust indexer watcher** (`services/indexer-rs/src/service.rs`, `POST /v1/index/build` after each snapshot
  write). It reads `<run_dir>/agentd-<port>.token` — port from `CRUCIBLE_BACKEND_URL` — fresh on every call
  (a `--reload` rotates it), never from the environment (its environment flows to its LSP children). A 401 is
  logged once per token value, not per call.
- **Dev scripts.** Python: one helper `scripts/_backend_auth.py` (base URL → port → token → header). Shell:
  `scripts/_backend_auth.sh` providing `crucible_auth_header <base_url>`. Every script under `scripts/` that
  targets the backend uses one of them — `scripts/stress/start-backend.sh` itself (readiness loop and
  pre-warm calls), `scripts/e2e-scripted.sh`, `scripts/stress/{verify-task,run-constrained-task}.sh`,
  `scripts/verify/*.py`, `scripts/smoke_clarify_gate.py`, `scripts/drive_clarify_live.py`,
  `scripts/eval/skill_trigger_eval.py`, `scripts/stress/e2e-stress-test.py`. A test greps `scripts/` for
  backend URLs and fails on any file that doesn't import or source a helper.
- **curl:** `-H "Authorization: Bearer $(cat ~/.crucible/run/agentd-8000.token)"` — documented in CLAUDE.md.

### 3.6 Escape hatch

`CRUCIBLE_AUTH_DISABLED=1` turns off the token check only; Host and Origin checks stay (they cost a caller
nothing and still stop DNS rebinding). **With it set, any local user — and any web page doing blind GETs —
can drive the backend**; the startup warning says so in those words and repeats every 60th request. It
exists for debugging a client that cannot send headers; nothing in the repo sets it, and it is not a
settings option.

The managed spawn must never inherit it: `buildBackendEnv` deletes `CRUCIBLE_AUTH_DISABLED` and
`CRUCIBLE_RUN_DIR` from the environment it passes on (a user who launched VS Code from a shell that had
them set, or who exported the repo `.env`, would otherwise start an unauthenticated backend, or one writing
its token where the extension doesn't look).

### 3.7 Lockfile reuse, reaping and old runtimes

Today (`backend-process.ts` `start()`): a lock whose pid is alive and whose `/health` answers is reused; any
other lock is unlinked and a new backend spawned — the old process is never killed. With auth:

- **Reuse** requires the token file for the lock's port to exist and pass §3.5's checks, and `/health` to
  answer 200 with `auth: "bearer"`. No token file means never reuse.
- **Reap:** when a lock is not reusable and its pid is alive and owned by the current user, the extension
  kills it (SIGTERM, then SIGKILL after 5 s) before spawning — otherwise two backends serve one workspace
  (the state `runtime_lock.py` exists to prevent). If it cannot be killed (EPERM: another user's process),
  it refuses to start and shows an error naming the pid, rather than spawning a second backend.
- **Pre-auth runtime** (a new extension with an old installed runtime; the "Install now?" upgrade prompt is
  optional today): detected by a spawned backend that becomes healthy without writing a token file, or a
  `/health` without `auth`. The extension stops it and shows a **blocking** "Crucible runtime update
  required" notification whose action installs the runtime and restarts — it does not fall back to
  unauthenticated use. The release manifest gains `minRuntime`; an installed runtime below it triggers the
  same flow at activation, before any spawn. The editable dev venv runs the repo's own code, so it is never
  below.

### 3.8 Best-effort: keep agent shells away from the token

`run_command`'s `command_guard` (the same seam as `vcs_refusal`) refuses any command line that names the
run directory (`.crucible/run`, or the expanded `CRUCIBLE_RUN_DIR`). This is a raised bar, not a wall: a
command can reach the file without naming it. §2 records the gap.

### 3.9 Failure surfacing

Today several callers swallow every error (`pollThreadLiveState`, `refreshCapabilityFlags`,
`BackendProcess.healthy`, `checkBackendHealth`), so a persistent 401 would show only a frozen UI. With auth:

- editor-client raises `BackendAuthError` (status 401/403/421 with the body's reason).
- `pollThreadLiveState` and `refreshCapabilityFlags` catch it specifically: they set the status bar to
  `$(error) Crucible: not authorized` and show one notification per backend URL (not per poll) with the
  reason and, for 401, "No token for the backend on port N — was it started by Crucible or
  start-backend.sh?".
- Auth failures are not retried, except the single token re-read in §3.5.

### 3.10 Bind address

The managed spawn and every script pass `--host 127.0.0.1` explicitly. The backend logs a warning at startup
if `CRUCIBLE_LISTEN_HOST` (set by the same launchers) is not a loopback address.

## 4. Testing

- **Middleware** (Starlette `TestClient` with `base_url="http://127.0.0.1:<port>"` — the default
  `testserver` Host would 421): each check alone and in order; missing, duplicate and absolute-form Host;
  `Origin: null`; `Bearer` case; non-ASCII token → 401; lifespan passes; a websocket scope is closed; an SSE
  route streams its first chunk before the generator finishes (no buffering). Route tests that build their
  own app from `build_router` stay unauthenticated; one end-to-end test installs auth via `install_auth` on
  `chat/app_factory.build_app`.
- **GET routes have no side effects:** a test lists every GET route and asserts each is in a reviewed
  read-only allowlist, so a new GET route forces a decision.
- **Token file:** created via `O_EXCL|O_NOFOLLOW`, `0600`, in a `0700` dir; a symlinked, foreign-owned or
  world-writable run dir aborts startup; atomic replace; ownership-checked delete leaves a successor's file;
  keyed by port; missing `CRUCIBLE_LISTEN_PORT` aborts startup unless auth is disabled.
- **Log hygiene:** an accepted and a rejected request leave no token in captured logs.
- **editor-client:** a parameterized test over every public method with a recording `fetchFn` asserts the
  header, SSE included; `BackendAuthError` on 401; `discardInlineChange` surfaces a non-ok response.
- **Extension:** `readBackendToken` (owner/mode check, cache by mtime, re-read on 401); `buildBackendEnv`
  strips `CRUCIBLE_AUTH_DISABLED`/`CRUCIBLE_RUN_DIR`; the reuse path refuses without a token file, kills a
  same-user stale pid, refuses on EPERM; pre-auth detection triggers the update flow.
- **indexer-rs:** the build POST carries the header read from the file; a rotated token is picked up; a 401
  is logged once.
- **Scripts:** the grep test in §3.5.
- **Live:** `Host: attacker.example` → 421; `Origin: https://evil.example` → 403; no token → 401; the dev
  host works end to end with no user action, including the indexer's self-update; `start-backend.sh` plus a
  verify script work; a check that the extension host's `fetch` (Electron's Node/undici, including VS Code's
  proxy-patched fetch) and `reqwest` send no `Origin` header.

## 5. Rollout

One change across backend, indexer, editor-client, extension and scripts — a backend that requires tokens
with clients that don't send them breaks every call, so they ship together. The managed runtime installs
both from one release; §3.7 handles a mismatched pair. A developer running an old extension against a new
`start-backend.sh` sees 401s with the actionable message; `CRUCIBLE_AUTH_DISABLED=1` is the documented
stopgap, with its warning.

## 6. Windows

`chmod` modes do not express owner-only access; `%USERPROFILE%\.crucible\run` is private to the user by
default ACL inheritance, and the writer does not set an explicit ACL in v1. The owner/mode checks in §3.2
and §3.5 are POSIX-only. `child.kill()` on Windows skips shutdown hooks, so stale token files are normal
there and harmless (§3.2). This is recorded as the accepted Windows posture.

## 7. Related fix already made

The integration review traced the "chat stays on a dead backend after a reload" finding from the v2 live
smoke to a pre-existing bug: a crash-respawn picks a new port, but only activation and the restart command
updated the managed URL. Fixed separately in `8ef6e8e` (`onBackendReady` updates the URL and keeps a list
of listeners). Not part of this design, but it means a respawn with a new token **and** a new port is
already followed by the client.

Out of scope, recorded for follow-up: a **reused** backend is never watched for exit (`watchCrash` returns
early: "not our child"), and the `/live` poll swallows connection errors, so a reused backend that dies
leaves the client silently disconnected until a reload. Auth neither causes nor detects this; a liveness
watch for reused backends (poll the lock pid, respawn, update the URL) belongs with the runtime manager.
