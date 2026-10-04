# Backend authentication — design

**Status:** rev 3, 2026-10-05. Rev 1 → 2 addressed 27 findings from a security and an integration review;
rev 2 → 3 addresses 29 more from a second round of the same two lenses. Separate from the sub-agents v2
spec (decision E17); it must land before v2 Phase 3 (teams) merges to `main`.

## 1. Problem

`agentd` serves an unauthenticated HTTP API on `127.0.0.1`. Anything that reaches that socket can drive it:
start chat turns, answer approval cards, accept edits, run commands through remembered allow rules, stop or
resume agents, rewind threads. Measured on the dev host, 2026-10-04:

- No Host check: `curl -H "Host: attacker.example:50115" http://127.0.0.1:50115/v1/config` → 200, so a web
  page can reach the backend by **DNS rebinding** (its domain re-pointed at 127.0.0.1; the browser treats
  the requests as same-origin and reads the responses).
- No CORS middleware: browsers block reading cross-origin responses, but a page can still **send** simple
  requests (CSRF).
- No token: any process on the machine — another OS user's included — can call every route.

Sub-agents v2 raises the stakes (agents work after the user's turn, notice turns start themselves, review
can be off); teams (Phase 3) multiply it.

## 2. Goals and non-goals

**Goals.** Only the Crucible extension host, the indexer watcher started alongside the backend, and tools
the user runs deliberately can use a backend. Web pages cannot (no DNS rebinding, no CSRF). Other OS users
cannot. Nothing changes for the user in the normal flow.

**Non-goals, stated plainly.**
- **Code running as the same OS user**, which can read the user's files (the token file included), attach
  to processes and drive VS Code. This includes **the agent's own tools**. A prompt-injected model with shell
  access — an approved or remembered `run_command`, an approved exec session (whose `write_stdin` input is
  free text no guard can inspect), or `shell_policy=allow_all` — can read the token and call
  `/edit-decision`, `/mcp-decision` or `/review-pref` to approve its own cards. If the workspace is the home
  directory or an ancestor of it, even `read_file` can reach the token without approval. Approval cards are
  therefore a containment boundary only for an agent with no shell access; this design does not change that
  and adds no command guard (one would be bypassable by exec-session input and give false assurance).
- **Server impersonation by another OS user beyond what §3.4's proof covers**, remote access, TLS,
  multiple users, per-route permissions.

## 3. Design

### 3.1 Starting a backend: `python -m agentd.serve`

A new entrypoint replaces `uvicorn agentd.main:app` everywhere a backend is launched (the managed spawn in
`backend-process.ts`, `scripts/stress/start-backend.sh`, `scripts/e2e-scripted.sh`, CLAUDE.md). It uses
uvicorn's own public pieces in the order `uvicorn.run` already uses for `--reload` (verified, uvicorn
0.41 `main.py:600-606`), with the token step inserted:

1. Build `uvicorn.Config("agentd.main:app", host="127.0.0.1", port=<--port>, reload=<--reload>, …)`.
2. **Bind:** `sock = config.bind_socket()`. If binding fails, exit — nothing has been written, so a second
   backend on a busy port can no longer touch the live backend's token. The real port is
   `sock.getsockname()[1]`, so `--port 0` works too.
3. **Token:** generate it and write the token file (§3.2) — once, in this parent process.
4. Export `CRUCIBLE_LISTEN_PORT=<port>` (not secret) so the app process(es) know the port.
5. **Serve on that socket:** `uvicorn.Server(config).run(sockets=[sock])`, or with `--reload`
   `ChangeReload(config, target=server.run, sockets=[sock]).run()`.
6. On exit, delete the token file **if its contents are still our token** (a successor may have written its
   own; the `clear_lock` lesson).

Consequences: the token is stable across `--reload` restarts (written once by the parent; workers read it),
so dev saves cause no token churn. The bind address is always `127.0.0.1` — `agentd.serve` has no `--host`
option. Starting the app another way (bare `uvicorn agentd.main:app`) leaves `CRUCIBLE_LISTEN_PORT` unset,
and the app refuses to start (§3.3) — including with auth disabled, so the Host check always has a port.

### 3.2 The token file

- `secrets.token_urlsafe(32)` (256 bits), new on every `agentd.serve` start. Never logged, never in a
  response body, never in any environment variable, never in the workspace or the lockfile.
- Location: **`~/.crucible/run/agentd-<port>.token`**. There is **no environment override** (none in Python,
  TypeScript or Rust): every reader computes `<home>/.crucible/run` with its platform's home lookup
  (`Path.home()`, `os.homedir()`, `std::env::home_dir()` — correct on Windows from Rust 1.86; the toolchain is
  1.93), so the writer and every reader always agree and no inherited variable can split them. Tests
  redirect it by setting `HOME` for the process under test.
- **Directory handling, race-free:** check that `~/.crucible` is owned by the user and not group- or
  world-writable; create `run` with mode `0700` if missing; open it once with
  `O_DIRECTORY | O_NOFOLLOW`; on that descriptor `fstat` it (refuse if not owned by the user), `fchmod` it to
  `0700`; then `os.open(tmp, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0o600, dir_fd=fd)` on a random temp
  name, write, `fsync`, and `os.replace(tmp, final, src_dir_fd=fd, dst_dir_fd=fd)`. Any refusal or failure
  exits before serving. (Windows lacks `dir_fd`; see §6.)
- **Reading** (backend app workers, extension, indexer): POSIX — refuse a file not owned by the current
  user or readable by group/world.

### 3.3 The middleware

Installed **at import** in `main.py` right after `app = FastAPI(...)`, holding a mutable `AuthState`. A
startup hook inserted at **index 0** of `app.router.on_startup` (other hooks are registered at import and
must not run first) fills it: it reads `CRUCIBLE_LISTEN_PORT` — missing → startup aborts with a message
naming `agentd.serve` — then the token file for that port (§3.2 reading rules; missing → abort). While
`AuthState` is empty the middleware answers 503, never passing a request through. It is a pure ASGI
middleware (not `BaseHTTPMiddleware`, which buffers SSE). It sits inside Starlette's
`ServerErrorMiddleware` — which only renders 500s — and outside every router, so nothing mounted later can
bypass it.

By scope type: `lifespan` passes through; `websocket` is closed with code 1008 (none exist; this fails
closed for future ones); `http` runs these checks in order:

1. **Peer → 403.** `scope["client"]` must be a loopback address (127.0.0.0/8 or ::1). Only matters if
   something binds non-loopback in future; `agentd.serve` binds 127.0.0.1.
2. **Host → 421.** Exactly one `Host` header, compared lowercase and exactly, port required:
   `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`. Missing or duplicate Host → 421. The body says
   "use 127.0.0.1 or localhost" (`0.0.0.0` or the hostname in `crucible.backendBaseUrl` now gets 421; the
   setting's description says so). This defeats DNS rebinding.
3. **Origin → 403.** Any `Origin` header (including `null`). Blocks requests browsers send with Origin
   (fetch/XHR, form POSTs). It does not stop plain cross-site GETs (`<img>`, `<script>`, navigation), which
   send no Origin — the token stops those. Route rule: GET routes stay free of side effects (§4).
4. **Token → 401** (skipped only when `CRUCIBLE_AUTH_DISABLED=1`, §3.6). `Authorization: Bearer <token>`,
   scheme matched case-insensitively, compared as bytes with `hmac.compare_digest` (non-ASCII → 401, not
   500). The body names what is missing, never the expected value.

### 3.4 `/health` proves the token

`GET /health?nonce=<random>` (authenticated like everything else) answers
`{"status": "ok", "proof": hex(hmac_sha256(token, nonce))}`. A client that verifies `proof` knows it is
talking to the backend that holds the token it read — not a pre-auth build, and not some other process
that took the port. Clients use one helper, `probeHealth(port)`, returning one of:

| Result | Meaning |
|---|---|
| `authed` | 200 with a valid proof |
| `preauth` | 200 with no `proof` field — a backend built before this change |
| `unauthorized` | 401/403/421, or a wrong proof |
| `down` | connection refused / timeout |

### 3.5 Clients

Every caller (verified by the integration review: all extension clients — memory panel, settings and MCP
admin, setup wizard, agent views, rewind — go through the one client factory in `extension.ts`; the only
raw fetches are `graph-panel.ts`, `settings.ts::checkBackendHealth` and `ProcessDeps.fetchJson`):

- **editor-client** (`http-backend-client.ts`). `HttpBackendClientOptions` gains
  `authToken: () => string | undefined`. The constructor wraps `fetchFn` itself, so every call — the raw
  call sites (mode/clarify decisions, both SSE streams, the chat message, `discardInlineChange`) and
  `fetchJson` — carries the header. 401/403/421 raise a typed `BackendAuthError` (status + reason). No
  retry. `discardInlineChange` starts checking `response.ok`.
- **Extension:**
  - `runtime/backend-token.ts` (vscode-free): `runDir()`, `readBackendToken(port)` with the §3.2 owner/mode
    check, cached by `(port, mtimeMs)` — a restart rewrites the file, so the key changes with the token; a
    missing file is never cached.
  - `probeHealth(port)` (§3.4) — used by the managed start, the reuse path and
    `settings.ts::checkBackendHealth`.
  - The client factory passes `authToken: () => readBackendToken(portOf(url))`.
  - `ProcessDeps.fetchJson` merges the header unconditionally (today it sets headers only with a body).
  - `GraphPanel` takes a URL resolver and the token reader instead of a captured URL, and checks
    `response.ok`.
- **Rust indexer watcher** (`services/indexer-rs/src/service.rs:311-315`). Inside the spawned task, read
  the token per call from `<home>/.crucible/run/agentd-<port>.token`, port from
  `Url::port_or_known_default` on `CRUCIBLE_BACKEND_URL`, same owner/mode check; log a 401 once per token.
- **Dev scripts.**
  - Helpers: `scripts/_backend_auth.py` (`auth_headers(base_url)`) and `scripts/_backend_auth.sh`
    (`crucible_auth_header <base_url>`). Python scripts run as `python scripts/x/y.py`, so each adds the
    `scripts/` directory to `sys.path` before importing the helper (a two-line preamble).
  - Users: `start-backend.sh` (readiness loop, pre-warm), `e2e-scripted.sh`,
    `scripts/stress/{verify-task,run-constrained-task}.sh` and `e2e-stress-test.py`,
    `scripts/verify/{01_create_task,02_feedback,03_finalize,04_resume,controller_ux_smoke,env_profile_e2e}.py`,
    `scripts/drive_clarify_live.py`, `scripts/eval/skill_trigger_eval.py`. (`scripts/smoke_clarify_gate.py`
    runs in-process over `build_router` and needs nothing.)
  - A grep test fails on any file under `scripts/` that names a backend URL (one containing `/v1/` or
    `/health`) without using a helper. Excluded: `scripts/playwright/node_modules/**`, `scripts/release/*`,
    `scripts/start-tqp.sh`, `scripts/verify/probe_reasoning_effort.py`, and the Ollama/TQP URL lines in
    `start-backend.sh`.
- **curl:** `-H "Authorization: Bearer $(cat ~/.crucible/run/agentd-<port>.token)"`, with the port from the
  status bar or the lockfile; documented in CLAUDE.md.

### 3.6 Escape hatch

`CRUCIBLE_AUTH_DISABLED=1` skips the token check only; peer, Host and Origin checks stay. **With it set, any
local user — and any web page doing blind GETs — can drive the backend**; the startup warning says so and
repeats every 60th request. For debugging a client that cannot send headers; nothing in the repo sets it.

The managed spawn never inherits it: both spawn sites in `backend-process.ts` (backend and watcher) build
their environment as `{ ...sanitizedParentEnv(), …built env }`, where `sanitizedParentEnv()` is
`process.env` minus `CRUCIBLE_AUTH_DISABLED`. The test inspects the environment actually passed to
`spawn`, not `buildBackendEnv`'s return value.

### 3.7 Lockfile reuse, reaping and runtime versions

Today `BackendProcess.start()` reuses a locked backend whose pid is alive and `/health` answers, and
otherwise unlinks the lock and spawns — never killing the old process.

- **Reuse** only when `probeHealth(lock.port)` is `authed`.
- **A lock younger than 60 s** whose backend is `down` may still be starting (another window's spawn): poll
  `probeHealth` until the 60 s mark before deciding.
- **Reap** a lock that is not reusable. Kill its pid **only when it is provably ours**: the process command
  line contains `agentd.serve` and `--port <lock.port>` (POSIX `ps -o command= -p <pid>`), and the process
  started no later than `lock.started_at`. A recycled pid, someone else's process, or a lock naming `pid 1`
  is never signalled — the lock is treated as stale, unlinked, and a new backend spawned. Never refuse to
  start on a lock. On Windows no process is killed (§6). A killed backend gets SIGTERM, then SIGKILL after
  5 s.
- **Runtime versions.**
  - `start()` maps `probeHealth == preauth` to a typed `RuntimeUpdateRequiredError`.
  - `extension.ts`'s managed-start failure handler — which today only logs — routes that error to a modal
    dialog (`{ modal: true }`): "Crucible runtime update required", whose action installs and restarts.
  - The release manifest gains `minComponents: { agentd, indexer }` (`make_manifest.py --min-runtime`, the
    `RuntimeManifest` type, and the bundled `resources/runtime-manifest.json`). At activation, **before**
    `startForWorkspace` (replacing the optional "Install now?" prompt that runs concurrently with it), the
    extension compares each component's installed version (semver on the release tag; non-semver tags such
    as `dev-unpinned` are never below) and shows the same modal if one is below.
  - An **editable** agentd install (the dev venv: its `.dist-info/direct_url.json` has
    `"dir_info": {"editable": true}`) skips the agentd comparison, but the **indexer** binary is still
    checked — a pre-auth watcher would otherwise 401 forever with no stale-runtime signal.

### 3.8 Failure surfacing

- The vscode-free controller gains `ControllerUI.onBackendAuthError(url, reason)`. `extension.ts`
  implements it with one status item (shared by the managed and explicit-URL flows; the runtime manager
  gets `setAuthError(reason | null)`) reading `$(error) Crucible: not authorized`, plus one notification per
  backend URL per session.
- Catch sites that call it on `BackendAuthError`: `pollThreadLiveState`, `pollAttention`,
  `refreshCapabilityFlags`, `composerModelState`, `AgentViewManager`'s backfill and stream loops, and
  `GraphPanel`.
- The status clears on the next authenticated success, so a transient 401 during a backend restart does
  not linger.

## 4. Testing

- **`agentd.serve`:** binds before writing (a second instance on a busy port exits without touching the
  first's file); `--port 0` yields the real port; token stable across a `--reload` restart; delete-at-exit
  is ownership-checked.
- **Middleware** (TestClient with `base_url="http://127.0.0.1:<port>"`; the default `testserver` Host
  would 421): each check alone and in order; missing/duplicate Host; `Origin: null`; non-loopback peer;
  `Bearer` case; non-ASCII token → 401; empty `AuthState` → 503; lifespan passes; websocket closed; an SSE
  route streams its first chunk before the generator ends. Route tests that build their own app from
  `build_router` stay unauthenticated; one end-to-end test runs the real middleware.
- **GET routes:** a reviewed allowlist of every GET route — a new GET route fails the test until someone
  adds it after checking it is read-only. Reviewed today: all read-only; `GET /channels/{channel_id}/stream`
  creates an empty replay entry for an unknown channel (accepted — no user-visible effect).
- **Token file:** directory and file modes, symlinked/foreign/writable directory refused, `~/.crucible`
  checks, atomic replace, reading rules.
- **Log hygiene:** an accepted and a rejected request leave no token in captured logs.
- **editor-client:** a parameterized test over every public method with a recording `fetchFn` (SSE
  included) asserts the header; `BackendAuthError` on 401/403/421; `discardInlineChange` surfaces failure.
- **Extension:** `readBackendToken` (owner/mode, mtime cache, missing never cached); `probeHealth`'s four
  results incl. a wrong proof; the spawn environment lacks `CRUCIBLE_AUTH_DISABLED`; reaping kills only a
  verified agentd (recycled pid, foreign pid and `pid 1` are not signalled; none of them blocks start); a
  young lock is waited on; `preauth` → `RuntimeUpdateRequiredError`; the per-component version check incl.
  editable agentd and a stale indexer.
- **indexer-rs:** the build POST carries the header from the file; a rewritten token is picked up; a 401 is
  logged once.
- **Scripts:** the grep test (§3.5).
- **Live:** `Host: attacker.example` → 421; `Origin: https://evil.example` → 403; no token → 401; the dev
  host works with no user action, including the indexer's self-update and a crash-respawn;
  `start-backend.sh --reload` survives a save without auth errors; a verify script works; the extension
  host's `fetch` (Electron's Node/undici incl. VS Code's proxy-patched fetch) and `reqwest` send no Origin.

## 5. Rollout

One change across backend, indexer, editor-client, extension and scripts; they ship together in one release.
§3.7 handles a mismatched pair. A developer with an old extension against new scripts sees 401s with the
actionable message; `CRUCIBLE_AUTH_DISABLED=1` is the stopgap, with its warning.

## 6. Windows

Owner/mode checks and `dir_fd` operations are POSIX-only. On Windows the writer creates the file under
`%USERPROFILE%\.crucible\run` (user-private by default ACL inheritance), retries `os.replace` three times
on `PermissionError`, and does not set an explicit ACL in v1. `child.kill()` skips shutdown hooks, so stale
token files are normal and harmless (a stale file's token matches no backend; §3.4's proof exposes it). No
process is killed when reaping a lock (§3.7): the lock is unlinked and a new backend spawned. Accepted
Windows posture for v1.

## 7. Related, out of scope

- Fixed separately (`8ef6e8e`): a crash-respawn picks a new port, and only activation and the restart
  command used to update the managed URL. `onBackendReady` now updates it.
- Not fixed, recorded for the runtime manager: a **reused** backend is never watched for exit
  (`watchCrash` returns early), so if it dies the client is silently disconnected until a reload; and a task
  session's persisted `backendBaseUrl` (`controller.ts::clientForSession`) is not updated after a
  respawn either.
