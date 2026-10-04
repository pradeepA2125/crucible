# Backend authentication — design

**Status:** rev 5, 2026-10-05. Four review rounds, each by a security lens and an integration lens:
rev 1 → 2 (27 findings), 2 → 3 (29), 3 → 4 (26), 4 → 5 (26). Separate from the sub-agents v2 spec
(decision E17); it must land before v2 Phase 3 (teams) merges to `main`.

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
cannot use it, cannot obtain its token by impersonating it, and cannot steer the extension through files
they write into a shared workspace. Nothing changes for the user in the normal flow.

**Non-goals, stated plainly.**
- **Code running as the same OS user**, which can read the user's files (the token file included), attach
  to processes, set `HOME` for processes it starts, and drive VS Code. This includes **the agent's own
  tools**: a prompt-injected model with shell access — an approved or remembered `run_command`, an approved
  exec session (whose `write_stdin` input is free text no guard can inspect), or `shell_policy=allow_all`
  — can read the token and call `/edit-decision`, `/mcp-decision` or `/review-pref` to approve its own
  cards. If the workspace is the home directory or an ancestor of it, even `read_file` reaches the token
  without approval. Approval cards are a containment boundary only for an agent with no shell access; this
  design adds no command guard (exec-session input would bypass it and it would give false assurance).
- Remote access, TLS, multiple users, per-route permissions.

## 3. Design

### 3.1 Starting a backend: `python -m agentd.serve`

A new entrypoint replaces `uvicorn agentd.main:app` at every launch site (§3.9). Its logic sits under
`if __name__ == "__main__": main()` (with `--reload`, multiprocessing re-imports the module as
`__mp_main__` in the worker). Arguments: `--port N` (0 = any free port), `--reload`, and, from the managed
spawn only, `--workspace-lock <workspace>` — taken verbatim, not resolved, because the backend's
`CRUCIBLE_WORKSPACE_PATH` is the same unresolved string and the lock must sit where readers look.

`main()`, wrapped in one `try/finally` from step 3 on:

1. **Migrate first:** with `--workspace-lock`, call `migrate_legacy_dirs(workspace)` before writing
   anything under `.crucible` (it renames `.ai-editor`/`.agentd` only when `.crucible` does not exist yet).
2. **Socket, bound and listening.** Create it ourselves rather than via `Config.bind_socket()`: on POSIX
   `SO_REUSEADDR` (as uvicorn does), on Windows `SO_EXCLUSIVEADDRUSE` and **not** `SO_REUSEADDR` (which on
   Windows lets another socket share the port); bind `127.0.0.1:<port>`; then `listen(backlog)` at once — a
   bound-but-not-listening socket can, on Linux, let another `SO_REUSEADDR` socket co-bind and listen first.
   On failure exit; nothing has been written. Real port: `sock.getsockname()[1]`. Without `--reload`,
   `set_inheritable(False)`, so a child spawned without `close_fds` (MCP server, LSP launcher) never keeps
   the port after a crash. No `--host` option exists: the address is always `127.0.0.1`.
3. **Tidy stale tokens:** delete `agentd-*.token` files in the run directory whose port can be bound right now
   (no live owner) — SIGKILL/OOM leaves them behind.
4. **Token** (§3.2), written once, in this process.
5. **Lock** (with `--workspace-lock`): `{pid, port, started_at (epoch seconds)}`, written via
   `runtime_lock.write_lock`, which becomes atomic and symlink-safe — `O_CREAT | O_EXCL | O_NOFOLLOW` temp
   file in `.crucible/state`, then rename; readers open it `O_NOFOLLOW`. The lock is only a hint for
   reusing a backend across windows (§3.7); nothing trusts it for more than that.
6. Export `CRUCIBLE_LISTEN_PORT=<port>` for the app process (§3.3).
7. **Handshake:** print one line to stdout, `CRUCIBLE_SERVE {"pid": <pid>, "port": <port>}`, and flush. The
   managed spawn learns its port from its own child's output, never from a file.
8. **Serve:** without `--reload`, a `uvicorn.Server` subclass whose `shutdown()` first deletes the token and
   lock files, then calls the base `shutdown()` (which closes the socket) — files go before the port is
   released, so no successor can bind it and write its own in between; `server.run(sockets=[sock])`. With
   `--reload`, `ChangeReload(config, target=server.run, sockets=[sock]).run()`; the parent still holds
   `sock` afterwards, so it deletes the files, then closes `sock`.
9. **`finally`:** delete the token file if its contents are still our token, and the lock if its pid is
   ours — this covers startup failures, `sys.exit` from `Server.run` and reload-supervisor errors.

Consequences: the token is stable across `--reload` restarts; a backend that cannot bind never touches
another's files; bare `uvicorn agentd.main:app` has no `CRUCIBLE_LISTEN_PORT`, so the app refuses to start
(§3.3) with auth enabled or not.

### 3.2 The token file

- `secrets.token_urlsafe(32)` (256 bits), new on every `agentd.serve` start. Never logged, never in a
  response body, never in an environment variable, never in the workspace or the lockfile.
- Location: **`<home>/.crucible/run/agentd-<port>.token`**, with no override variable in Python,
  TypeScript or Rust; each computes it from its platform's home lookup (`Path.home()`, `os.homedir()`,
  `std::env::home_dir()` — correct on Windows from Rust 1.86; the toolchain is 1.93). These read `HOME` on
  POSIX; the managed spawn passes `HOME` through unchanged, so writer and readers agree. Readers that need
  testing take the home or token path as a parameter (Rust `read_token(home, port)`, TS
  `ProcessDeps.readToken(port)`), so no test mutates process-wide `HOME`.
- **Writing, race-free:** create `<home>/.crucible` with mode `0700` if missing, then require it to be owned
  by the user and not group/world-writable; create `run` with `0700` if missing; open it once with
  `O_DIRECTORY | O_NOFOLLOW`; `fstat` the descriptor (refuse if not ours), `fchmod` it to `0700`;
  `os.open(tmp, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0o600, dir_fd=fd)` on a random name, write,
  `fsync`, `os.replace(tmp, final, src_dir_fd=fd, dst_dir_fd=fd)`. Any refusal or failure exits before
  serving. Deletions also go through the directory descriptor.
- **Reading** (app workers, extension, indexer): open with `O_NOFOLLOW`, then `fstat` **the descriptor**
  and refuse a file not owned by the user or readable by group/world — never stat-then-open. (Node:
  `fs.openSync(path, O_RDONLY | O_NOFOLLOW)` + `fs.fstatSync`; Rust: `custom_flags(libc::O_NOFOLLOW)` +
  `metadata()` on the handle, `libc` added under `cfg(unix)`.)

### 3.3 The middleware: `agentd/auth.py`

`agentd/auth.py` holds the pure ASGI middleware (not `BaseHTTPMiddleware`, which buffers SSE), its
`AuthState`, and `install_auth(app)`. `main.py` passes it in the `FastAPI(middleware=[…])` constructor. It
then runs inside Starlette's `ServerErrorMiddleware` (which only renders 500s) and before routing. A later
`add_middleware` call would insert **outside** it (Starlette inserts at index 0), so a test asserting it is
the outermost user middleware is the guard. Sub-apps mounted with `app.mount` stay inside it.

A startup hook inserted at **index 0** of `app.router.on_startup` (other hooks are registered at import)
fills `AuthState`: `CRUCIBLE_LISTEN_PORT` — missing → startup aborts naming `agentd.serve` — then the token
file for that port (§3.2 reading rules; missing → abort). While `AuthState` is empty the middleware answers
503.

By scope type: `lifespan` passes through; `websocket` is closed with code 1008 (none exist; fails closed for
future ones); `http` runs these checks in order:

1. **Peer → 403.** `scope["client"]` must parse as a loopback address (127.0.0.0/8 or ::1). A missing or
   non-IP peer (TestClient's default `"testclient"`) is a 403, never an exception.
2. **Host → 421.** Exactly one `Host` header equal to **`127.0.0.1:<port>`** (lowercase, exact, port
   required). Missing, duplicate or anything else → 421, body "connect to 127.0.0.1:<port>". `localhost` is
   refused because it may resolve to `::1`, where another user could listen on the same port number while
   the real backend holds 127.0.0.1; clients never connect by name (§3.5).
3. **Origin → 403.** Any `Origin` header (including `null`). Blocks browser fetch/XHR and form POSTs. Plain
   cross-site GETs (`<img>`, `<script>`, navigation) send no Origin and are stopped by the token. GET routes
   stay free of side effects (§4).
4. **Token → 401**, except the health exemption below and `CRUCIBLE_AUTH_DISABLED=1` (§3.6).
   `Authorization: Bearer <token>`, scheme case-insensitive, compared as bytes with `hmac.compare_digest`
   (non-ASCII → 401, not 500). The body names what is missing, never the expected value.

**Health exemption, exact:** `scope["method"] == "GET"` and `scope["path"] == "/health"` — no prefix match,
no trailing slash (`/health/` gets Starlette's redirect, which is not exempt), `HEAD` not exempt.

### 3.4 `/health` proves the token without receiving it

Clients call `GET /health?nonce=<n>` **without** an `Authorization` header. The backend parses
`scope["query_string"]`; with exactly one `nonce` matching `[0-9a-f]{16,64}` it answers
`{"status": "ok", "proof": hex(hmac_sha256(token, "crucible-health-v1\0" + nonce))}`, otherwise
`{"status": "ok"}`. A client that verifies `proof` against the token it read knows the server holds that
token without having revealed it; seeing proofs reveals nothing about the key (HMAC is a PRF). A relaying
man-in-the-middle would need to share the port with the live backend, which loopback-only addresses, listen
before writing, and `SO_EXCLUSIVEADDRUSE` on Windows (§3.1) prevent.

`probeHealth(port)` — the one helper all clients use, **2 s timeout**:

| Result | Meaning |
|---|---|
| `authed` | 200 with a valid proof |
| `preauth` | 200 with no `proof` — something other than an authenticated backend answered |
| `unauthorized` | 403 or 421, or a wrong proof |
| `down` | connection refused / timeout |

### 3.5 Clients

All extension clients (memory panel, settings and MCP admin, setup wizard, agent views, rewind) go through
the one client factory in `extension.ts`; the only raw fetches are `graph-panel.ts`,
`settings.ts::checkBackendHealth` and `ProcessDeps.fetchJson` (verified in review).

- **Addresses.** Every client connects to the literal `127.0.0.1`. The managed URLs switch from
  `localhost:<port>` to `127.0.0.1:<port>`: `backend-process.ts` (health, index build, the watcher's
  `CRUCIBLE_BACKEND_URL`), `vscode-runtime.ts::backendUrl()`, and `start-backend.sh`. The client factory,
  the indexer and the script helpers also rewrite a `localhost` host to `127.0.0.1` before connecting, and
  never attach the token to any other host.
- **No redirects with a token.** The wrapped `fetchFn`, `ProcessDeps` and `GraphPanel` use
  `redirect: "manual"`; the indexer uses `redirect::Policy::none()`.
- **editor-client** (`http-backend-client.ts`). `HttpBackendClientOptions` gains
  `authToken: () => string | undefined` and `onAuthStatus?: (ok: boolean, reason?: string) => void`. The
  constructor wraps `fetchFn` itself, so every call — the raw call sites (mode/clarify decisions, both SSE
  streams, the chat message, `discardInlineChange`) and `fetchJson` — carries the header and reports to
  `onAuthStatus` (ok on any non-auth response; not ok with the reason on 401/403/421). 401/403/421 also raise
  a typed `BackendAuthError`. No retry. `discardInlineChange` starts checking `response.ok`.
- **Extension:**
  - `runtime/backend-token.ts` (vscode-free): `runDir()` and `readBackendToken(port)` (§3.2 reading rules),
    cached by `(port, mtimeMs)` — a restart rewrites the file, so the key changes; a missing file is never
    cached.
  - `probeHealth(port)` (§3.4), used by the managed start, the reuse path and `checkBackendHealth`.
  - The client factory passes `authToken` and `onAuthStatus` (§3.8).
  - `ProcessDeps` gains `fetchRaw(url, init) → {status, body}` (today's `fetchJson` throws a generic error
    and cannot tell 401 from down), `processInfo(pid) → {uid, command, startedAtSec} | null`,
    `signal(pid, sig)`, `readToken(port)`, `exec(cmd, args) → {code, stdout, stderr}`, and access to the
    child's stdout lines. `fetchJson` merges the header unconditionally. `buildBackendEnv` loses its `port`
    parameter and `pickFreePort` is removed.
  - `GraphPanel` takes a URL resolver and the token reader instead of a captured URL, and checks
    `response.ok`.
- **Rust indexer** (`services/indexer-rs/src/service.rs`, the build POST). Inside the spawned task: rewrite
  `localhost`, port from `Url::port_or_known_default`, `read_token(home, port)` per call, a client built with
  `.no_proxy()` (the default honours `HTTP(S)_PROXY` and would send the token to a proxy) and
  `redirect::Policy::none()`. A 401 is logged once per token. New `--version` arm in `main.rs`'s positional
  match prints `<version> auth=1` (§3.7).
- **Subprocesses started by the backend.** One helper, `child_env()`, returns `os.environ` minus
  `CRUCIBLE_LISTEN_PORT`; every subprocess site uses it — `tools/shell.py`, `tools/env.py`,
  `tools/search.py`, `tools/post_patch/checker.py`, `exec_sessions/pty_process.py` and `manager.py`,
  `env/probe.py`, `retrieval/artifact_client.py`, `validation/command_validator.py`,
  `orchestrator/engine.py`. A nested app started from one of them (an agent or validator dogfooding this
  repo) then refuses to start instead of inheriting the parent's port. A test enumerates the subprocess
  call sites and fails on one that bypasses `child_env()`.
- **Dev scripts.**
  - Helpers: `scripts/_backend_auth.py` (`backend_url(base) → 127.0.0.1 URL`, `auth_headers(base)`) and
    `scripts/_backend_auth.sh` (`crucible_auth_header <base_url>`), both reading the token per request.
  - Python preamble, independent of the working directory:
    `sys.path.insert(0, str(Path(__file__).resolve().parents[N]))`, `N` = the script's depth below
    `scripts/` (`scripts/x.py` → 0, `scripts/a/b.py` → 1).
  - Users: `start-backend.sh` (readiness loop re-reads the token each iteration, since the file appears just
    after bind; pre-warm), `e2e-scripted.sh` (both launch sites), `scripts/stress/{verify-task,
    run-constrained-task}.sh` and `e2e-stress-test.py`,
    `scripts/verify/{01_create_task,02_feedback,03_finalize,04_resume,controller_ux_smoke,env_profile_e2e}.py`,
    `scripts/drive_clarify_live.py`, `scripts/eval/skill_trigger_eval.py`.
  - The grep test lives at `services/agentd-py/tests/test_scripts_use_auth_helper.py`, walks `scripts/`, and
    fails on a file that contains `/v1/` or `/health` without using a helper. Excluded:
    `scripts/playwright/node_modules/**` and `scripts/smoke_clarify_gate.py` (in-process over
    `build_router`; it reaches no backend).
- **curl:** `-H "Authorization: Bearer $(cat ~/.crucible/run/agentd-<port>.token)" http://127.0.0.1:<port>/…`,
  port from the status bar or the lockfile; documented in CLAUDE.md.

### 3.6 Escape hatch

`CRUCIBLE_AUTH_DISABLED=1` skips the token check only; peer, Host and Origin checks stay. **With it set, any
local user — and any web page doing blind GETs — can drive the backend**; the startup warning says so and
repeats every 60th request. Nothing in the repo sets it.

The managed spawn never inherits it: both spawn sites in `backend-process.ts` (backend and watcher) remove
it from the **final merged** environment — after `process.env`, the built env and the user-supplied
`extraEnv` (which carries user-named provider variables) are combined. The test inspects the environment
actually passed to `spawn`.

### 3.7 Managed start, reuse, reaping and runtime versions

**Before spawning:**
- `venvPython -c "import agentd.serve"` (via `ProcessDeps.exec`). Failure → `RuntimeUpdateRequiredError`.
  An old runtime would otherwise die at once on `No module named agentd.serve` and surface as a generic
  crash.
- The indexer: a missing binary skips the watcher, as today. A present binary whose `--version` lacks
  `auth=1` → `RuntimeUpdateRequiredError`. (Watcher tests that write an empty `crucible-indexer` file stub
  `ProcessDeps.exec` instead.)

**Spawn.** Arguments exactly `[-m, agentd.serve, --port, 0, --workspace-lock, <workspace>]`. The extension
reads the child's stdout for the `CRUCIBLE_SERVE {…}` line (10 s timeout; the child exiting first fails at
once), takes the port from it, then polls `probeHealth`. Nothing in this path reads the workspace lockfile.
This replaces `pickFreePort`-then-spawn and its race. The status bar, `ports.set` and `watchCrash` already
run after `start()` returns, and the watcher is spawned after health, with the port in hand.

**Reuse** (another window's backend for the same workspace): read the lock with `O_NOFOLLOW`; reuse only when
`probeHealth(lock.port)` is `authed`. If the port is `down` and the lock is younger than 60 s, its spawn may
still be starting: wait **once per `start()`**, up to the lock's 60 s mark — skipped when the lock's pid is
dead or is this `BackendProcess`'s own previous child.

**Reap** a lock that is not reusable, including one whose port answers `preauth` or `unauthorized`. Kill its
pid **only when provably ours**, via `processInfo(pid)` = `LC_ALL=C ps -ww -o uid=,etime=,command= -p <pid>`
(POSIX, locale-free, untruncated; start = now − etime, one-second resolution):
- uid equals the current user's;
- the command line contains `agentd.serve` and the exact argument pair `--workspace-lock <this workspace>`,
  **or** (migration, until the release after next) `agentd.main:app` and `--port <lock.port>`;
- start time ≤ `lock.started_at` + 1 s (both epoch **seconds**; JS converts from ms).

Then SIGTERM, SIGKILL after 5 s. Anything else — a recycled pid, another user's process, `pid 1` — is never
signalled: the lock is unlinked and a new backend spawned. A planted lock therefore cannot make the
extension kill anything, wait more than once, or skip spawning. On Windows no process is killed (§6).

**Stop and restart.** `stop()` awaits the child's exit (10 s, then SIGKILL). `restart()` therefore never
reads the lock of a backend that is still draining.

**Runtime versions.**
- Beyond the two pre-spawn checks: a backend **this `start()` just spawned** that answers `preauth` →
  `RuntimeUpdateRequiredError`. (On the reuse path `preauth` means "reap and spawn", so an old backend left
  on a lock is replaced, not looped on.)
- The error is routed from every start path — activation, crash-respawn and `restart()` — to a modal dialog
  (`{ modal: true }`), "Crucible runtime update required", whose action:
  - normal install: runs the installer, then restarts;
  - **editable dev install** (`crucible_agentd-*.dist-info/direct_url.json` under the venv's
    `site-packages` has `"dir_info": {"editable": true}`): never runs the installer; it tells the developer
    to run `scripts/dev/install-local.sh`.
- `install-local.sh` also builds the indexer (`scripts/stress/_indexer.sh::ensure_indexer_binary`) and
  installs it to `~/.crucible/runtime/bin/crucible-indexer` via a temp file and `mv` (overwriting a running
  binary in place gets it killed on macOS arm64 and fails with `ETXTBSY` on Linux), then asks the developer
  to restart the backend so the watcher picks it up. Its stale header comment about `--reload` goes.
- The optional "Crucible runtime vX is available" prompt stays for non-required upgrades.
- `installer.ts`'s post-install check verifies `import agentd.serve` instead of `import uvicorn`.

### 3.8 Failure surfacing

- One sink, `reportAuthStatus(url, ok, reason?)`, defined in `extension.ts` and closed over by the client
  factory — no `ControllerUI` change; `controller.ts` stays as it is. It drives a status item shared by the
  managed and explicit-URL flows (`RuntimeManager.setAuthError(reason | null)`) reading
  `$(error) Crucible: not authorized`, and one notification per backend URL per session. It clears on the
  next ok report, so a 401 during a restart does not linger.
- Every editor-client call reports through `onAuthStatus` (§3.5), covering user-initiated calls and task
  streams without per-site catches. `GraphPanel`, `ProcessDeps` and `checkBackendHealth` call the same sink.

### 3.9 Launch sites, docs and cleanup

- Launch sites: managed spawn args (`backend-process.ts`); `scripts/stress/start-backend.sh`
  (`./.venv/bin/python -m agentd.serve --port "$PORT" --reload`); `scripts/e2e-scripted.sh` (two sites).
- Docs: CLAUDE.md (incl. the two `CRUCIBLE_PORT` mentions), `README.md`, `services/agentd-py/README.md`, the
  `install-local.sh` header.
- `CRUCIBLE_PORT` is retired: its reader is `main.py`'s lock hook (removed; the lock is written by
  `agentd.serve`), its writer `buildBackendEnv`; update the `runtime_lock.py` docstring.

## 4. Testing

- **`agentd.serve`** (subprocess tests): a second instance on a busy port exits without touching the first's
  files; `--port 0` yields the real port in the handshake, lock and file name; the socket is listening
  before any file is written; the token survives a `--reload` restart and the reload worker does not write
  it; the lock names the serve pid and is written symlink-safe; files are deleted before the socket closes
  and on every failure path (`try/finally`); the socket is not inheritable without `--reload`; stale token
  files with no live owner are removed at start; legacy dirs are migrated before the lock is written; on
  Windows a second bind to the port fails (`SO_EXCLUSIVEADDRUSE`).
- **Middleware** (minimal app + the real `install_auth`; TestClient with
  `base_url="http://127.0.0.1:<port>"` and `client=("127.0.0.1", 50000)`): each check alone and in order;
  missing/duplicate/`localhost` Host → 421; `Origin: null`; non-loopback and non-IP peer → 403; `Bearer`
  case; non-ASCII token → 401; empty `AuthState` → 503; `/health` without a token returns a proof that
  verifies; `/health/`, `/healthz`, `HEAD /health` and a duplicated or malformed nonce are not exempt / get no
  proof; lifespan passes; websocket closed; an SSE route streams its first chunk before the generator ends;
  the middleware is outermost in `main.py`'s app.
- **GET routes:** a reviewed allowlist of every GET route — a new GET route fails the test until someone
  adds it after checking it is read-only. Reviewed today: all read-only; `GET /channels/{channel_id}/stream`
  creates an empty replay entry for an unknown channel (accepted).
- **Token file:** modes; `~/.crucible` created `0700` when missing; symlinked, foreign or writable
  directories refused; atomic replace; reading via `O_NOFOLLOW` + `fstat` in all three readers.
- **Log hygiene:** accepted and rejected requests leave no token in captured logs.
- **Subprocess env:** every site uses `child_env()` (enumerating test) and lacks `CRUCIBLE_LISTEN_PORT`.
- **editor-client:** every public method (SSE included) sends the header, reports auth status and does not
  follow redirects; `BackendAuthError` on 401/403/421; `discardInlineChange` surfaces failure.
- **Extension:** `readBackendToken`; `probeHealth`'s four results incl. a wrong proof, its 2 s timeout, and
  that it sends no token; `localhost` rewritten before the token is attached; the spawn env lacks
  `CRUCIBLE_AUTH_DISABLED` even when `extraEnv` sets it; the port comes from the handshake line and the child
  exiting first fails fast; the pre-spawn import and indexer checks; reaping signals only a verified process
  (recycled pid, foreign uid, `pid 1`, other workspace untouched; none blocks start); the young-lock wait
  happens at most once and is skipped for a dead pid or our previous child; `stop()` awaits exit; a legacy
  `agentd.main:app` backend on the lock is reaped and replaced; `RuntimeUpdateRequiredError` reaches the modal
  from activation, crash-respawn and `restart()`; editable detection.
  Existing tests to update: `test/runtime-backend-process.test.ts` (`buildBackendEnv` without port or
  `CRUCIBLE_PORT`, spawn args, `127.0.0.1` URLs, a stub child that prints the handshake line, the reuse test's
  token via `ProcessDeps.readToken`, the watcher tests stubbing `exec`), `test/runtime-installer.test.ts`
  (the `import agentd.serve` check).
- **indexer-rs:** `read_token(home, port)` with a temp dir; the POST carries the header, goes direct and does
  not follow redirects, picks up a rewritten token, logs a 401 once; `--version` prints `auth=1`.
- **Scripts:** the grep test (§3.5).
- **Live:** `Host: attacker.example` → 421; `Origin: https://evil.example` → 403; no token → 401; the dev
  host works with no user action, including the indexer's self-update (after `install-local.sh` rebuilds
  it) and a crash-respawn; `start-backend.sh --reload` survives a save; a verify script works; the extension
  host's `fetch` (Electron undici incl. VS Code's proxy-patched fetch) sends no Origin and goes direct to
  `127.0.0.1`.

## 5. Rollout

One change across backend, indexer, editor-client, extension and scripts, shipped together. §3.7 handles a
mismatched pair and an old backend left on a lock. A developer with an old extension against new scripts
sees 401s with the actionable message; `CRUCIBLE_AUTH_DISABLED=1` is the stopgap, with its warning.

## 6. Windows

Owner/mode checks and `dir_fd` operations are POSIX-only. On Windows the writer creates the file under
`%USERPROFILE%\.crucible\run` (user-private by default ACL inheritance), retries `os.replace` three times on
`PermissionError`, and sets no explicit ACL in v1. The socket uses `SO_EXCLUSIVEADDRUSE` (§3.1), so no
other account can share the port. The venv's `python.exe` is a launcher that runs the interpreter as a
child; the stdout handshake (§3.7) does not depend on pids, so this is harmless. `child.kill()` skips
shutdown hooks, so stale token files occur and are tidied at the next start (§3.1 step 3). Reaping kills no
process: the lock is unlinked and a new backend spawned. Accepted Windows posture for v1.

## 7. Related, out of scope

- Fixed separately (`8ef6e8e`): a crash-respawn picks a new port, and only activation and the restart
  command used to update the managed URL. `onBackendReady` now updates it.
- Not fixed, recorded for the runtime manager: a **reused** backend is never watched for exit
  (`watchCrash` returns early), so if it dies the client is silently disconnected until a reload; and a task
  session's persisted `backendBaseUrl` (`controller.ts::clientForSession`) is not updated after a respawn.
