# Backend authentication — design

**Status:** rev 4, 2026-10-05. Three review rounds, each by a security lens and an integration lens:
rev 1 → 2 (27 findings), rev 2 → 3 (29), rev 3 → 4 (26). Separate from the sub-agents v2 spec (decision
E17); it must land before v2 Phase 3 (teams) merges to `main`.

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
cannot use it, and cannot obtain its token by impersonating it. Nothing changes for the user in the normal
flow.

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

A new entrypoint replaces `uvicorn agentd.main:app` everywhere a backend is launched (§3.9 lists the
sites). All its logic sits under `if __name__ == "__main__": main()` — with `--reload`, multiprocessing's
spawn re-imports the module as `__mp_main__` in the worker, and top-level code would bind and write again.
`main()` follows the order uvicorn's own `run()` uses for `--reload` (uvicorn 0.41 `main.py:600-606`), with
the token step inserted:

1. Build `uvicorn.Config("agentd.main:app", host="127.0.0.1", port=<--port>, reload=<--reload>, …)`.
   `agentd.serve` has no `--host` option.
2. **Bind:** `sock = config.bind_socket()`. If it fails, exit — nothing has been written. The real port is
   `sock.getsockname()[1]`, so `--port 0` works. Without `--reload`, `sock.set_inheritable(False)` (uvicorn
   marks it inheritable for the reload handoff; a child spawned without `close_fds` — an MCP server or LSP
   launcher — would otherwise keep the port bound after a crash).
3. **Token:** generate it and write the token file (§3.2), once, in this process.
4. **Lockfile:** with `--workspace-lock <workspace>` (the managed spawn passes it; scripts do not), write
   `<workspace>/.crucible/state/agentd.lock` = `{pid: <this process>, port: <real port>, started_at:
   <epoch seconds>}`. This replaces today's app-startup lock hook driven by `CRUCIBLE_PORT`, which is
   removed: the lock now exists as soon as the port is bound, and names the process that holds it.
5. Export `CRUCIBLE_LISTEN_PORT=<port>` for the app process (§3.3).
6. **Keep the port held while cleaning up:** `held = os.dup(sock.fileno())`.
7. **Serve:** `uvicorn.Server(config).run(sockets=[sock])`, or with `--reload`
   `ChangeReload(config, target=server.run, sockets=[sock]).run()`.
8. **On exit:** delete the token file if its contents are still our token, and the lock if its pid is
   still ours (a successor may have written its own; the `clear_lock` lesson); then `os.close(held)`.
   uvicorn closes `sock` on shutdown (`server.py:277`); holding the duplicate keeps the port bound until our
   files are gone, so no successor can bind it and write its token in between.

Consequences: the token is stable across `--reload` restarts; a backend that cannot bind never touches
another's files; bare `uvicorn agentd.main:app` has no `CRUCIBLE_LISTEN_PORT` and the app refuses to start
(§3.3), with auth enabled or not.

### 3.2 The token file

- `secrets.token_urlsafe(32)` (256 bits), new on every `agentd.serve` start. Never logged, never in a
  response body, never in an environment variable, never in the workspace or the lockfile.
- Location: **`<home>/.crucible/run/agentd-<port>.token`**, with no override variable in Python,
  TypeScript or Rust; each computes it from its platform's home lookup (`Path.home()`, `os.homedir()`,
  `std::env::home_dir()` — correct on Windows from Rust 1.86; the toolchain is 1.93). These read `HOME` on
  POSIX; the managed spawn passes `HOME` through unchanged, so writer and readers agree. Tests point `HOME`
  at a temp directory for the process under test; the Rust reader takes the home path as a parameter
  (`read_token(home, port)`) so its tests need no process-wide `HOME`.
- **Writing, race-free:** require `<home>/.crucible` to be owned by the user and not group/world-writable;
  create `run` with mode `0700` if missing; open it once with `O_DIRECTORY | O_NOFOLLOW`; `fstat` the
  descriptor (refuse if not ours), `fchmod` it to `0700`; `os.open(tmp, O_CREAT | O_EXCL | O_WRONLY |
  O_NOFOLLOW, 0o600, dir_fd=fd)` on a random name, write, `fsync`, then
  `os.replace(tmp, final, src_dir_fd=fd, dst_dir_fd=fd)`. Any refusal or failure exits before serving.
  Deletion (§3.1 step 8) also goes through the directory descriptor.
- **Reading** (app workers, extension, indexer): open with `O_NOFOLLOW`, then `fstat` **the descriptor**
  and refuse a file not owned by the user or readable by group/world — never stat-then-open. (Node:
  `fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW)` + `fs.fstatSync`; Rust:
  `OpenOptionsExt::custom_flags(libc::O_NOFOLLOW)` + `metadata()` on the handle, `libc` added under
  `cfg(unix)`.)

### 3.3 The middleware: `agentd/auth.py`

`agentd/auth.py` holds the middleware and its `AuthState`, with `install_auth(app)`. `main.py` builds the app
with the middleware in the constructor's `middleware=[…]` list, so nothing added later can sit outside it
(`add_middleware` inserts at index 0, i.e. outermost); a test asserts it is the outermost user middleware.
It is a pure ASGI middleware (not `BaseHTTPMiddleware`, which buffers SSE) and sits inside Starlette's
`ServerErrorMiddleware`, which only renders 500s.

A startup hook inserted at **index 0** of `app.router.on_startup` (other hooks are registered at import)
fills `AuthState`: it reads `CRUCIBLE_LISTEN_PORT` — missing → startup aborts naming `agentd.serve` — then
the token file for that port (§3.2 reading rules; missing → abort). While `AuthState` is empty the
middleware answers 503.

By scope type: `lifespan` passes through; `websocket` is closed with code 1008 (none exist; fails closed for
future ones); `http` runs these checks in order:

1. **Peer → 403.** `scope["client"]` must parse as a loopback address (127.0.0.0/8 or ::1). A missing or
   non-IP peer (e.g. TestClient's default `"testclient"`) is a 403, never an exception.
2. **Host → 421.** Exactly one `Host` header equal to **`127.0.0.1:<port>`** (lowercase, exact, port
   required). Missing, duplicate or anything else → 421, with a body saying "connect to 127.0.0.1:<port>".
   `localhost` is not accepted: it may resolve to `::1`, where another user can listen on the same port
   number while the real backend holds 127.0.0.1 — clients never connect by name (§3.5).
3. **Origin → 403.** Any `Origin` header (including `null`). Blocks browser fetch/XHR and form POSTs. Plain
   cross-site GETs (`<img>`, `<script>`, navigation) send no Origin and are stopped by the token. GET routes
   stay free of side effects (§4).
4. **Token → 401**, except on `GET /health` (§3.4) and when `CRUCIBLE_AUTH_DISABLED=1` (§3.6).
   `Authorization: Bearer <token>`, scheme case-insensitive, compared as bytes with `hmac.compare_digest`
   (non-ASCII → 401, not 500). The body names what is missing, never the expected value.

### 3.4 `/health` proves the token without receiving it

`GET /health?nonce=<16–64 hex chars>` is the one route exempt from the token check (peer, Host and Origin
still apply), and clients call it **without** an `Authorization` header. It answers
`{"status": "ok", "proof": hex(hmac_sha256(token, "crucible-health-v1\0" + nonce))}` (no nonce, or a
malformed one → no `proof`). A client that verifies `proof` against the token it read knows the server holds
that token — without having revealed it. Seeing proofs reveals nothing about the key (HMAC is a PRF). A
relaying man-in-the-middle would need to own the port while the real backend is alive, which the
`127.0.0.1`-only rule (§3.3, §3.5) prevents.

Clients use one helper, `probeHealth(port)`:

| Result | Meaning |
|---|---|
| `authed` | 200 with a valid proof |
| `preauth` | 200 with no `proof` field — something that is not an authenticated backend answered |
| `unauthorized` | 403 or 421, or a wrong proof |
| `down` | connection refused / timeout |

### 3.5 Clients

All extension clients (memory panel, settings and MCP admin, setup wizard, agent views, rewind) go through
the one client factory in `extension.ts`; the only raw fetches are `graph-panel.ts`,
`settings.ts::checkBackendHealth` and `ProcessDeps.fetchJson` (verified in review).

- **Addresses.** Every client connects to the literal `127.0.0.1`. The managed URLs switch from
  `localhost:<port>` to `127.0.0.1:<port>` (`backend-process.ts`: health, index build, the watcher's
  `CRUCIBLE_BACKEND_URL`; `start-backend.sh`). The client factory, the indexer and the script helpers rewrite
  a `localhost` host to `127.0.0.1` before connecting, and never attach the token to any other host.
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
    and cannot tell 401 from down), `processInfo(pid) → {uid, command, startedAtSec} | null` and
    `signal(pid, sig)`. `fetchJson` merges the header unconditionally.
  - `GraphPanel` takes a URL resolver and the token reader instead of a captured URL, and checks
    `response.ok`.
- **Rust indexer** (`services/indexer-rs/src/service.rs`, the build POST). Inside the spawned task: rewrite
  `localhost`, take the port from `Url::port_or_known_default`, `read_token(home, port)` per call (§3.2
  reading rules), and send with a client built `Client::builder().no_proxy()` — the default client honours
  `HTTP(S)_PROXY` and would send the token to a proxy. A 401 is logged once per token. New
  `crucible-indexer --version` prints `<version> auth=1` (§3.7).
- **Tool subprocesses** (`tools/shell.py`, `exec_sessions/manager.py`): remove `CRUCIBLE_LISTEN_PORT` from
  the environment they copy, so a nested `uvicorn agentd.main:app` started by an agent dogfooding this repo
  refuses to start instead of inheriting the parent's port.
- **Dev scripts.**
  - Helpers: `scripts/_backend_auth.py` (`backend_url(base) → 127.0.0.1 URL`, `auth_headers(base)`) and
    `scripts/_backend_auth.sh` (`crucible_auth_header <base_url>`), both reading the token per request.
  - Python preamble, independent of the working directory:
    `sys.path.insert(0, str(Path(__file__).resolve().parents[N]))`, where `N` is the script's depth below
    `scripts/` (`scripts/x.py` → 0 … `scripts/a/b.py` → 1).
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

**Spawn.** Arguments exactly `[-m, agentd.serve, --port, 0, --workspace-lock, <workspace>]`. The extension
waits until the lock appears with `pid == child.pid`, takes the port from it, then polls `probeHealth`. The
poll races the child's exit: if the process exits first, start fails at once (no 60 s wait on a dead port).
This replaces `pickFreePort`-then-spawn and its race.

**Reuse** a lock only when `probeHealth(lock.port)` is `authed`. A lock younger than 60 s whose port is
`down` may belong to a spawn in progress (another window): poll until the 60 s mark before deciding.

**Reap** a lock that is not reusable — including one whose port answers `preauth`. Kill its pid **only when
provably ours**, using `processInfo(pid)` = `LC_ALL=C ps -ww -o uid=,etime=,command= -p <pid>` (POSIX,
locale-free, untruncated; start time = now − etime, one-second resolution):
- uid equals the current user's;
- the command line contains `agentd.serve` and `--port` with the lock's port, **or** (migration, until the
  release after next) `agentd.main:app` and `--port <lock.port>`;
- start time ≤ `lock.started_at` + 1 s (both in epoch **seconds**; JS converts from ms).

Then SIGTERM, SIGKILL after 5 s. Anything else — a recycled pid, another user's process, `pid 1` — is never
signalled: the lock is unlinked and a new backend spawned. A lock never blocks start. On Windows no process
is killed (§6).

**Runtime versions — by capability, not version strings.**
- A backend **this `start()` just spawned** that answers `preauth` → `RuntimeUpdateRequiredError`. (On the
  reuse path `preauth` means "reap and spawn", above — so an old backend left on a lock is replaced, not
  looped on.)
- `crucible-indexer --version` without `auth=1` (or failing) → the same error, checked at activation before
  the first spawn.
- The error is routed from every start path — activation, crash-respawn and `restart()` — to a modal dialog
  (`{ modal: true }`), "Crucible runtime update required". Its action:
  - normal install: runs the installer, then restarts;
  - **editable dev install** (agentd's `crucible_agentd-*.dist-info/direct_url.json` has
    `"dir_info": {"editable": true}`, found by glob under the venv's `site-packages`): never runs the
    installer; it tells the developer to run `scripts/dev/install-local.sh`, which now builds and copies the
    indexer too.
- The existing optional "Crucible runtime vX is available" prompt stays for non-required upgrades.
- `installer.ts`'s post-install check verifies `import agentd.serve` instead of `import uvicorn`, so a
  pre-auth wheel fails at install time.

### 3.8 Failure surfacing

- One sink, `reportAuthStatus(url, ok, reason?)`, implemented in `extension.ts`: a status item shared by the
  managed and explicit-URL flows (`RuntimeManager.setAuthError(reason | null)`) reading
  `$(error) Crucible: not authorized`, and one notification per backend URL per session. It clears on the
  next ok report, so a 401 during a restart does not linger.
- Every editor-client call reports through `onAuthStatus` (§3.5), so user-initiated calls (sending a
  message, task streams) are covered without per-site catches. `GraphPanel`, `ProcessDeps` and
  `checkBackendHealth` call the same sink.

### 3.9 Launch sites and docs to update

Managed spawn args (`backend-process.ts`); `scripts/stress/start-backend.sh` (`./.venv/bin/python -m
agentd.serve --port "$PORT" --reload`); `scripts/e2e-scripted.sh` (two sites); CLAUDE.md, `README.md`,
`services/agentd-py/README.md`; the `scripts/dev/install-local.sh` comment; `installer.ts`'s import check.

## 4. Testing

- **`agentd.serve`** (subprocess tests): binds before writing — a second instance on a busy port exits
  without touching the first's files; `--port 0` yields the real port in the lock and the file name; the
  token survives a `--reload` restart and the reload worker does not write it; the lock names the serve pid;
  delete-at-exit is ownership-checked and the port stays bound until the files are gone; the socket is not
  inheritable without `--reload`.
- **Middleware** (minimal app + the real `install_auth`; TestClient with
  `base_url="http://127.0.0.1:<port>"` and `client=("127.0.0.1", 50000)`): each check alone and in order;
  missing/duplicate/`localhost` Host → 421; `Origin: null`; non-loopback and non-IP peer → 403; `Bearer`
  case; non-ASCII token → 401; empty `AuthState` → 503; `/health` works without a token and its proof
  verifies; lifespan passes; websocket closed; an SSE route streams its first chunk before the generator
  ends; the middleware is outermost in `main.py`'s app.
- **GET routes:** a reviewed allowlist of every GET route — a new GET route fails the test until someone
  adds it after checking it is read-only. Reviewed today: all read-only; `GET /channels/{channel_id}/stream`
  creates an empty replay entry for an unknown channel (accepted).
- **Token file:** modes; symlinked, foreign or writable directories refused; `~/.crucible` checks; atomic
  replace; reading via `O_NOFOLLOW` + `fstat` in all three readers.
- **Log hygiene:** accepted and rejected requests leave no token in captured logs.
- **editor-client:** every public method (SSE included) sends the header and reports auth status;
  `BackendAuthError` on 401/403/421; `discardInlineChange` surfaces failure.
- **Extension:** `readBackendToken`; `probeHealth`'s four results incl. a wrong proof, and that it sends no
  token; `localhost` rewritten before the token is attached; the spawn env lacks `CRUCIBLE_AUTH_DISABLED`
  even when `extraEnv` sets it; the spawn waits for the lock and fails fast if the child exits; reaping
  signals only a verified process (recycled pid, foreign uid, `pid 1` untouched; none blocks start); a
  legacy `agentd.main:app` backend on the lock is reaped and replaced; a fresh `preauth` raises
  `RuntimeUpdateRequiredError` from activation, crash-respawn and `restart()`; editable detection.
  Updated existing tests: `test/runtime-backend-process.test.ts` (health stub returns a proof; spawn args),
  `test/controller.test.ts` (`createUi` gains the auth sink).
- **indexer-rs:** `read_token(home, port)` with a temp dir; the POST carries the header, goes direct (no
  proxy), picks up a rewritten token, logs a 401 once; `--version` prints `auth=1`.
- **Tool env:** `run_command` and exec sessions lack `CRUCIBLE_LISTEN_PORT`.
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
`PermissionError`, and sets no explicit ACL in v1. `child.kill()` skips shutdown hooks, so stale token files
are normal and harmless (§3.4's proof exposes a stale one). Reaping kills no process: the lock is unlinked
and a new backend spawned. Accepted Windows posture for v1.

## 7. Related, out of scope

- Fixed separately (`8ef6e8e`): a crash-respawn picks a new port, and only activation and the restart
  command used to update the managed URL. `onBackendReady` now updates it.
- Not fixed, recorded for the runtime manager: a **reused** backend is never watched for exit
  (`watchCrash` returns early), so if it dies the client is silently disconnected until a reload; and a task
  session's persisted `backendBaseUrl` (`controller.ts::clientForSession`) is not updated after a respawn.
