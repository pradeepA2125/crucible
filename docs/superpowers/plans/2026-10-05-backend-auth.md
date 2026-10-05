# Backend Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every request to agentd-py carries a per-start bearer token read from an owner-only file, and every client proves the server holds that token (and, for reuse, is the right backend) before sending it.

**Architecture:** A new light entrypoint `python -m agentd.serve` binds `127.0.0.1`, writes the token and lock, prints a handshake line, then runs uvicorn on the pre-bound socket. A pure ASGI middleware in `agentd/auth.py` checks peer → Host → Origin → token; `GET /health?nonce=` answers with HMAC proofs instead of requiring the token. Clients (editor-client, extension, indexer, dev scripts) read the token file for the port, verify `/health` first, then attach `Authorization: Bearer`.

**Tech Stack:** Python 3.13 / FastAPI / Starlette / uvicorn; TypeScript (Node, vitest); Rust (reqwest 0.12, tokio); bash.

**Spec:** `docs/superpowers/specs/2026-10-04-backend-auth-design.md` (rev 7, frozen). Section references below (§3.4 etc.) point there. Read the spec section a task names before starting it.

## Part index

| Part | Tasks | Area |
|---|---|---|
| A | 1–6 | Backend: token file, middleware + health proof, `agentd.serve`, `main.py` wiring, `child_env`, route/log guards |
| B | 7 | editor-client: auth options, `BackendAuthError`, no redirects |
| C | 8–12 | Extension: token reader + `probeHealth`, verify-before-send fetch + failure sink, spawn/handshake, reuse/reap, stop + runtime versions |
| D | 13 | Rust indexer: token read, health verify, `--version` |
| E | 14–15 | Dev scripts, launch sites, docs |
| F | 16 | Full-suite run + live smoke on the dev host |

Parts depend on A (the backend contract). B before C (the extension consumes editor-client's new options). D and E depend only on A.

## Global Constraints

- Token: `secrets.token_urlsafe(32)`; file holds exactly the 43-character token, no newline; readers reject anything else.
- Token path: `<home>/.crucible/run/agentd-<port>.token`; no override variable in any language.
- Address: always the literal `127.0.0.1`; `localhost` is rewritten by every client before attaching a token, and refused by the backend's Host check (421).
- Health proof: `P = hex(hmac_sha256(token, "crucible-health-v1\0" + nonce))`, `B = hex(hmac_sha256(token, "crucible-health-bound-v1\0" + nonce + "\0" + pid + "\0" + workspace))`; nonce matches `[0-9a-f]{16,64}`.
- `probeHealth` timeout 2 s; results `authed | other-backend | preauth | unauthorized | down | no-token`.
- Middleware order and statuses: peer 403 → Host 421 → Origin 403 → token 401; empty `AuthState` → 503; exemption exactly `GET /health`.
- `CRUCIBLE_AUTH_DISABLED=1` skips only the token check; never inherited by a managed spawn.
- `CRUCIBLE_PORT` is retired. New env vars (backend-internal, set only by `agentd.serve`): `CRUCIBLE_LISTEN_PORT`, `CRUCIBLE_SERVE_PID`; both stripped by `child_env()`.
- `agentd.serve` imports only the standard library and uvicorn at module level.
- Pre-spawn checks: 5 s timeouts. Handshake wait: 10 s. Graceful shutdown: 5 s. `stop()` wait: 10 s then SIGKILL. Reap: SIGTERM, SIGKILL after 5 s.
- Pytest: never `-q`, never piped; `pytest --color=no … > out.txt 2>&1; echo exit=$?`; full runs with `--timeout=120`.
- Commits: `type(scope): short description`, ending with the two attribution lines. Never push.

## Deviations from the spec (deliberate, small)

- `processInfo` uses `LC_ALL=C ps -ww -o uid=,etime=,command=` on every POSIX platform, Linux included (spec: `/proc` on Linux). `ps -ww` prints the untruncated command line there too; one parser instead of two.
- With `--reload`, only a failure of the **first** worker's startup takes the supervisor down (exit 3). A failure after a reload (a typo mid-edit) keeps the supervisor watching, as plain uvicorn does, so a dev server does not die on a syntax error.
- `probeHealth` maps HTTP 503 (the app has not loaded its token yet, spec §3.3) to `down`, so startup polling keeps waiting.
- Failures inside the managed start (`ProcessDeps`) surface through the existing start-failure path (`markFailed` / the runtime-update modal) rather than through `reportAuthStatus`; every request-level client reports to the sink.

## Review Focus

1. **A user whose backend restarted on a port another of their workspaces' backends now holds** — reuse must spawn a fresh backend, never attach to the other workspace's. Pinned in Task 11 (`does not reuse an authed backend for another workspace`).
2. **A save during `start-backend.sh --reload`** — the token must not change and the reload worker must not rewrite it; clients keep working without re-reading. Pinned in Task 3 (`test_reload_keeps_token`).
3. **A `crucible.backendBaseUrl` set to `http://localhost:8000`** (the old default many users have saved) — must rewrite to `127.0.0.1` and work, not 421. Pinned in Task 9 (`rewrites localhost before probing`).
4. **Backend killed while the chat's 1 s `/live` poll keeps running** — no request with a token goes to the dead port until a fresh `authed` probe; the poll does not spam probes (5 s rate limit). Pinned in Task 9 (`re-probes after a connection error, rate-limited`).
5. **A developer with an editable install whose venv predates `agentd.serve`** — gets the modal pointing at `install-local.sh`, not a generic crash loop. Pinned in Task 12 (`editable install message`).

---
# Part A — Backend (`services/agentd-py`)

All commands in Part A run from `services/agentd-py` with `.venv/bin/python` / `.venv/bin/pytest`. No existing test imports `agentd.main` (they build apps from `build_router`), so the middleware changes nothing for them.

### Task 1: Token file module

**Files:**
- Create: `agentd/auth_token.py`
- Test: `tests/test_auth_token.py`

**Interfaces:**
- Produces:
  - `TOKEN_LEN: int = 43`
  - `class TokenFileError(RuntimeError)`
  - `run_dir(home: Path) -> Path` → `home/.crucible/run`
  - `token_path(home: Path, port: int) -> Path`
  - `write_token(home: Path, port: int) -> str` — new token, written atomically under `flock`; returns it
  - `read_token(home: Path, port: int) -> str | None` — `None` when the file is missing; `TokenFileError` when it exists but is unsafe or malformed
  - `touch_token(home: Path, port: int) -> None` — best-effort mtime bump (keep-alive)
  - `tidy_tokens(home: Path, *, max_age_sec: float = 7 * 86400, budget_sec: float = 2.0, now: Callable[[], float] = time.time, port_refuses: Callable[[int], bool] = port_refuses_connection) -> list[int]` — ports whose files it removed
  - `port_refuses_connection(port: int) -> bool` — `True` only on `ECONNREFUSED`

- [ ] **Step 1: Write the failing tests**

```python
"""Token file (spec §3.2): owner-only, atomic, symlink-safe, and a tidy that can never
delete a live backend's file."""
from __future__ import annotations

import fcntl
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from agentd.auth_token import (
    TOKEN_LEN,
    TokenFileError,
    port_refuses_connection,
    read_token,
    run_dir,
    tidy_tokens,
    token_path,
    touch_token,
    write_token,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file semantics")


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    token = write_token(tmp_path, 8123)
    assert len(token) == TOKEN_LEN
    assert read_token(tmp_path, 8123) == token
    assert token_path(tmp_path, 8123).read_bytes() == token.encode("ascii")  # no newline


def test_modes(tmp_path: Path) -> None:
    write_token(tmp_path, 8123)
    assert stat.S_IMODE(os.stat(token_path(tmp_path, 8123)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(run_dir(tmp_path)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / ".crucible").st_mode) == 0o700


def test_existing_run_dir_is_tightened(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir(mode=0o700)
    run_dir(tmp_path).mkdir(mode=0o755)
    os.chmod(run_dir(tmp_path), 0o755)
    write_token(tmp_path, 1)
    assert stat.S_IMODE(os.stat(run_dir(tmp_path)).st_mode) == 0o700


def test_refuses_group_writable_crucible_dir(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir()
    os.chmod(tmp_path / ".crucible", 0o775)
    with pytest.raises(TokenFileError):
        write_token(tmp_path, 1)


def test_refuses_symlinked_run_dir(tmp_path: Path) -> None:
    (tmp_path / ".crucible").mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(elsewhere, run_dir(tmp_path))
    with pytest.raises((TokenFileError, OSError)):
        write_token(tmp_path, 1)
    assert list(elsewhere.iterdir()) == []


def test_write_replaces_an_old_file(tmp_path: Path) -> None:
    first = write_token(tmp_path, 9)
    second = write_token(tmp_path, 9)
    assert first != second
    assert read_token(tmp_path, 9) == second


def test_read_missing_is_none(tmp_path: Path) -> None:
    assert read_token(tmp_path, 4) is None


def test_read_refuses_symlink(tmp_path: Path) -> None:
    write_token(tmp_path, 5)
    real = token_path(tmp_path, 5)
    link = token_path(tmp_path, 6)
    os.symlink(real, link)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 6)


def test_read_refuses_group_readable(tmp_path: Path) -> None:
    write_token(tmp_path, 7)
    os.chmod(token_path(tmp_path, 7), 0o640)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 7)


@pytest.mark.parametrize("content", [b"x" * 43 + b"\n", b"short", b"!" * 43, b""])
def test_read_refuses_malformed(tmp_path: Path, content: bytes) -> None:
    write_token(tmp_path, 8)
    path = token_path(tmp_path, 8)
    path.write_bytes(content)
    os.chmod(path, 0o600)
    with pytest.raises(TokenFileError):
        read_token(tmp_path, 8)


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old), follow_symlinks=False)


def test_tidy_removes_old_file_whose_port_refuses(tmp_path: Path) -> None:
    write_token(tmp_path, 40001)
    _age(token_path(tmp_path, 40001), 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == [40001]
    assert not token_path(tmp_path, 40001).exists()


def test_tidy_keeps_fresh_file(tmp_path: Path) -> None:
    write_token(tmp_path, 40002)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == []
    assert token_path(tmp_path, 40002).exists()


def test_tidy_keeps_file_when_connect_is_not_refused(tmp_path: Path) -> None:
    # A timeout or any other connect error is NOT proof the port is dead.
    write_token(tmp_path, 40003)
    _age(token_path(tmp_path, 40003), 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: False) == []
    assert token_path(tmp_path, 40003).exists()


def test_tidy_keeps_symlink_and_foreign_names(tmp_path: Path) -> None:
    write_token(tmp_path, 40004)
    rd = run_dir(tmp_path)
    os.symlink(token_path(tmp_path, 40004), rd / "agentd-40005.token")
    (rd / "notes.txt").write_text("x")
    for name in ("agentd-40005.token", "notes.txt"):
        _age(rd / name, 8)
    assert tidy_tokens(tmp_path, port_refuses=lambda _p: True) == []
    assert (rd / "agentd-40005.token").is_symlink()
    assert (rd / "notes.txt").exists()


def test_port_refuses_connection_is_false_for_a_listener() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        assert port_refuses_connection(srv.getsockname()[1]) is False


def test_port_refuses_connection_is_true_for_a_closed_port() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port_refuses_connection(port) is True


def test_writer_waits_for_tidy_lock(tmp_path: Path) -> None:
    write_token(tmp_path, 1)  # creates the run dir
    fd = os.open(run_dir(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()
    thread = threading.Thread(target=lambda: (write_token(tmp_path, 2), done.set()))
    thread.start()
    time.sleep(0.2)
    assert not done.is_set()  # blocked behind the lock
    assert not token_path(tmp_path, 2).exists()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    thread.join(5)
    assert done.is_set()
    assert read_token(tmp_path, 2) is not None


def test_touch_bumps_mtime(tmp_path: Path) -> None:
    write_token(tmp_path, 3)
    _age(token_path(tmp_path, 3), 8)
    touch_token(tmp_path, 3)
    assert time.time() - token_path(tmp_path, 3).stat().st_mtime < 60
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_auth_token.py --color=no > /tmp/t1.txt 2>&1; echo exit=$?; tail -5 /tmp/t1.txt`
Expected: exit≠0, `ModuleNotFoundError: No module named 'agentd.auth_token'`.

- [ ] **Step 3: Implement `agentd/auth_token.py`**

```python
"""Backend auth token file (spec §3.2).

<home>/.crucible/run/agentd-<port>.token holds exactly the 43-character token. The
writer and the tidy share an flock on the run directory, so the tidy can never delete
a file a backend binding the same port has just written. Files are never deleted at
shutdown (spec §3.1); a live backend bumps its file's mtime daily, so "older than
7 days" means nothing has owned it for a week.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path

if os.name == "posix":
    import fcntl

TOKEN_LEN = 43
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_NAME_RE = re.compile(r"agentd-(\d+)\.token")
_IS_POSIX = os.name == "posix"
_WINDOWS_REPLACE_RETRIES = 3


class TokenFileError(RuntimeError):
    """The token file or its directory exists but is unsafe or malformed."""


def run_dir(home: Path) -> Path:
    return home / ".crucible" / "run"


def token_path(home: Path, port: int) -> Path:
    return run_dir(home) / f"agentd-{port}.token"


def _require_private(path: Path, st: os.stat_result) -> None:
    if st.st_uid != os.getuid():
        raise TokenFileError(f"{path} is not owned by the current user")
    if st.st_mode & 0o022:
        raise TokenFileError(f"{path} is writable by group or others")


def _open_run_dir(home: Path) -> int:
    """Create and open <home>/.crucible/run, refusing anything not privately ours."""
    crucible = home / ".crucible"
    try:
        os.mkdir(crucible, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(crucible)
    if not stat.S_ISDIR(st.st_mode):
        raise TokenFileError(f"{crucible} is not a directory")
    _require_private(crucible, st)
    rd = run_dir(home)
    try:
        os.mkdir(rd, 0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(rd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise TokenFileError(f"{rd} is a symlink or not a directory") from exc
        raise
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise TokenFileError(f"{rd} is not owned by the current user")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_token(home: Path, port: int) -> str:
    token = secrets.token_urlsafe(32)
    if not _IS_POSIX:
        _write_token_windows(home, port, token)
        return token
    fd = _open_run_dir(home)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = f".agentd-{port}.{secrets.token_hex(8)}.tmp"
        tfd = os.open(
            tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.write(tfd, token.encode("ascii"))
            os.fsync(tfd)
        finally:
            os.close(tfd)
        os.replace(tmp, f"agentd-{port}.token", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)  # releases the flock
    return token


def _write_token_windows(home: Path, port: int, token: str) -> None:
    # Spec §6: user-private by ACL inheritance; os.replace can hit a transient
    # PermissionError while a reader holds the file open.
    rd = run_dir(home)
    rd.mkdir(parents=True, exist_ok=True)
    tmp = rd / f".agentd-{port}.{secrets.token_hex(8)}.tmp"
    tmp.write_bytes(token.encode("ascii"))
    for attempt in range(_WINDOWS_REPLACE_RETRIES):
        try:
            os.replace(tmp, token_path(home, port))
            return
        except PermissionError:
            if attempt == _WINDOWS_REPLACE_RETRIES - 1:
                raise
            time.sleep(0.1)


def read_token(home: Path, port: int) -> str | None:
    path = token_path(home, port)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TokenFileError(f"{path} is a symlink") from exc
        raise
    try:
        if _IS_POSIX:
            st = os.fstat(fd)  # the descriptor, never stat-then-open
            if not stat.S_ISREG(st.st_mode):
                raise TokenFileError(f"{path} is not a regular file")
            if st.st_uid != os.getuid():
                raise TokenFileError(f"{path} is not owned by the current user")
            if st.st_mode & 0o077:
                raise TokenFileError(f"{path} is readable by group or others")
        data = os.read(fd, TOKEN_LEN + 1)
    finally:
        os.close(fd)
    text = data.decode("ascii", errors="replace")
    if not _TOKEN_RE.fullmatch(text):
        raise TokenFileError(f"{path} does not hold a token")
    return text


def touch_token(home: Path, port: int) -> None:
    try:
        os.utime(token_path(home, port), follow_symlinks=False)
    except OSError:
        pass  # best-effort keep-alive


def port_refuses_connection(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except ConnectionRefusedError:
            return True
        except OSError:
            return False  # timeout, backlog full, …: not proof of death
        return False


def tidy_tokens(
    home: Path,
    *,
    max_age_sec: float = 7 * 86400,
    budget_sec: float = 2.0,
    now: Callable[[], float] = time.time,
    port_refuses: Callable[[int], bool] = port_refuses_connection,
) -> list[int]:
    if not _IS_POSIX:
        return []  # spec §6: no dir_fd on Windows
    deadline = time.monotonic() + budget_sec
    removed: list[int] = []
    try:
        fd = _open_run_dir(home)
    except (OSError, TokenFileError):
        return removed
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        for name in os.listdir(fd):
            if time.monotonic() > deadline:
                break
            match = _NAME_RE.fullmatch(name)
            if match is None:
                continue
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or now() - st.st_mtime <= max_age_sec:
                continue
            port = int(match.group(1))
            if port_refuses(port):
                os.unlink(name, dir_fd=fd)
                removed.append(port)
    except OSError:
        pass  # best-effort: never stops startup
    finally:
        os.close(fd)
    return removed
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_auth_token.py --color=no > /tmp/t1.txt 2>&1; echo exit=$?; tail -3 /tmp/t1.txt`
Expected: `exit=0`, all passed.

- [ ] **Step 5: Commit**

```bash
git add agentd/auth_token.py tests/test_auth_token.py
git commit -m "feat(auth): owner-only token file with locked tidy"
```

### Task 2: Middleware and health proof

**Files:**
- Create: `agentd/auth.py`
- Test: `tests/test_auth_middleware.py`

**Interfaces:**
- Consumes: `read_token`, `touch_token` (Task 1).
- Produces:
  - `@dataclass class AuthState: port: int | None; token: bytes | None; serve_pid: int; workspace: str; disabled: bool` (all defaulted; `serve_pid=0`, `workspace=""`, `disabled=False`)
  - `AUTH_STATE: AuthState` — the process singleton `main.py` uses
  - `class AuthStartupError(RuntimeError)`
  - `load_auth_state(state: AuthState, environ: Mapping[str, str], home: Path) -> None`
  - `class AuthMiddleware` (pure ASGI, `__init__(self, app, state: AuthState)`)
  - `auth_middleware(state: AuthState) -> starlette.middleware.Middleware`
  - `install_auth(app: FastAPI, state: AuthState) -> None` — inserts the loader at index 0 of `app.router.on_startup` and registers the keep-alive background task
  - `health_payload(state: AuthState, query_string: bytes) -> dict[str, object]`
  - `health_proof(token: bytes, nonce: str) -> str`, `health_bound(token: bytes, nonce: str, pid: int, workspace: str) -> str` (hex; the strings are UTF-8 encoded)

- [ ] **Step 1: Write the failing tests**

```python
"""Auth middleware (spec §3.3) and the /health proof (§3.4)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from agentd.auth import (
    AuthStartupError,
    AuthState,
    auth_middleware,
    health_bound,
    health_payload,
    health_proof,
    install_auth,
    load_auth_state,
)
from agentd.auth_token import write_token

PORT = 8123
TOKEN = "t" * 43
BASE = f"http://127.0.0.1:{PORT}"
LOOPBACK = ("127.0.0.1", 50000)
NONCE = "0123456789abcdef"


def _app(state: AuthState) -> FastAPI:
    app = FastAPI(middleware=[auth_middleware(state)])

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        return health_payload(state, request.scope["query_string"])

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/sse")
    async def sse() -> StreamingResponse:
        async def gen():
            yield b"data: first\n\n"
            await asyncio.sleep(3600)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def _ready() -> AuthState:
    return AuthState(port=PORT, token=TOKEN.encode(), serve_pid=777, workspace="/w s")


def _client(state: AuthState | None = None, client=LOOPBACK) -> TestClient:
    return TestClient(_app(state or _ready()), base_url=BASE, client=client,
                      follow_redirects=False)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_token_accepted() -> None:
    assert _client().get("/x", headers=AUTH).status_code == 200


def test_bearer_scheme_is_case_insensitive() -> None:
    assert _client().get("/x", headers={"Authorization": f"bEaReR {TOKEN}"}).status_code == 200


def test_missing_token_401_names_no_value() -> None:
    resp = _client().get("/x")
    assert resp.status_code == 401
    assert TOKEN not in resp.text


def test_wrong_and_non_ascii_token_401() -> None:
    assert _client().get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    raw = {"Authorization": "Bearer ".encode() + "é".encode("utf-8") * 10}
    assert _client().get("/x", headers=raw).status_code == 401


@pytest.mark.parametrize("peer", [("10.0.0.5", 1), ("testclient", 50000)])
def test_non_loopback_or_non_ip_peer_403(peer) -> None:
    assert _client(client=peer).get("/x", headers=AUTH).status_code == 403


def test_ipv6_loopback_peer_allowed() -> None:
    assert _client(client=("::1", 1)).get("/x", headers=AUTH).status_code == 200


@pytest.mark.parametrize("host", ["localhost:8123", "127.0.0.1", "127.0.0.1:9999", "evil.example"])
def test_wrong_host_421(host: str) -> None:
    resp = _client().get("/x", headers={**AUTH, "Host": host})
    assert resp.status_code == 421
    assert "127.0.0.1:8123" in resp.text


@pytest.mark.parametrize("origin", ["null", "https://evil.example", "vscode-webview://x"])
def test_any_origin_403(origin: str) -> None:
    assert _client().get("/x", headers={**AUTH, "Origin": origin}).status_code == 403


def test_checks_run_in_order() -> None:
    # Bad peer AND bad host AND origin AND no token → the peer check answers.
    resp = _client(client=("10.0.0.5", 1)).get("/x", headers={"Host": "evil", "Origin": "null"})
    assert resp.status_code == 403
    resp = _client().get("/x", headers={"Host": "evil", "Origin": "null"})
    assert resp.status_code == 421
    resp = _client().get("/x", headers={"Origin": "null"})
    assert resp.status_code == 403


def test_empty_state_503() -> None:
    assert _client(AuthState()).get("/x", headers=AUTH).status_code == 503


def test_health_needs_no_token_and_proves_it() -> None:
    body = _client().get(f"/health?nonce={NONCE}").json()
    assert body["proof"] == hmac.new(
        TOKEN.encode(), b"crucible-health-v1\0" + NONCE.encode(), hashlib.sha256).hexdigest()
    assert body["pid"] == 777
    assert body["bound"] == health_bound(TOKEN.encode(), NONCE, 777, "/w s")
    assert body["bound"] != health_bound(TOKEN.encode(), NONCE, 777, "/w")
    assert body["proof"] == health_proof(TOKEN.encode(), NONCE)


@pytest.mark.parametrize("query", ["", "nonce=short", f"nonce={NONCE}&nonce={NONCE}",
                                   "nonce=" + "G" * 16, "nonce=" + "a" * 65])
def test_health_without_valid_nonce_has_no_proof(query: str) -> None:
    body = _client().get(f"/health?{query}").json()
    assert body == {"status": "ok"}


@pytest.mark.parametrize("method,path", [("GET", "/health/"), ("GET", "/healthz"),
                                         ("HEAD", "/health")])
def test_health_exemption_is_exact(method: str, path: str) -> None:
    assert _client().request(method, path).status_code == 401


def test_health_still_checks_host_and_origin() -> None:
    assert _client().get("/health", headers={"Host": "localhost:8123"}).status_code == 421
    assert _client().get("/health", headers={"Origin": "null"}).status_code == 403


def test_auth_disabled_skips_only_the_token() -> None:
    state = _ready()
    state.disabled = True
    assert _client(state).get("/x").status_code == 200
    assert _client(state).get("/x", headers={"Origin": "null"}).status_code == 403


def test_sse_streams_first_chunk_before_generator_ends() -> None:
    with _client().stream("GET", "/sse", headers=AUTH) as resp:
        assert resp.status_code == 200
        first = next(resp.iter_bytes())
        assert first.startswith(b"data: first")


def test_websocket_closed() -> None:
    app = _app(_ready())

    @app.websocket("/ws")
    async def ws(websocket) -> None:  # pragma: no cover - never reached
        await websocket.accept()

    client = TestClient(app, base_url=BASE, client=LOOPBACK)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_lifespan_passes(tmp_path: Path) -> None:
    write_token(tmp_path, PORT)
    state = AuthState()
    app = _app(state)
    install_auth(app, state)
    env = {"CRUCIBLE_LISTEN_PORT": str(PORT), "CRUCIBLE_SERVE_PID": "42",
           "CRUCIBLE_WORKSPACE_PATH": "/ws"}
    app.state.auth_environ = env
    app.state.auth_home = tmp_path
    with TestClient(app, base_url=BASE, client=LOOPBACK) as client:
        assert state.serve_pid == 42 and state.workspace == "/ws"
        assert client.get("/health").status_code == 200


def test_load_requires_listen_port(tmp_path: Path) -> None:
    with pytest.raises(AuthStartupError, match="agentd.serve"):
        load_auth_state(AuthState(), {}, tmp_path)


def test_load_requires_token_file(tmp_path: Path) -> None:
    with pytest.raises(AuthStartupError):
        load_auth_state(AuthState(), {"CRUCIBLE_LISTEN_PORT": "8123"}, tmp_path)


def test_rejected_and_accepted_requests_log_no_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    _client().get("/x", headers=AUTH)
    _client().get("/x", headers={"Authorization": "Bearer wrong-but-logged?"})
    assert TOKEN not in caplog.text
    assert "wrong-but-logged" not in caplog.text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_auth_middleware.py --color=no > /tmp/t2.txt 2>&1; echo exit=$?; tail -5 /tmp/t2.txt`
Expected: exit≠0, `No module named 'agentd.auth'`.

- [ ] **Step 3: Implement `agentd/auth.py`**

`install_auth` reads its environment and home from `app.state.auth_environ` / `app.state.auth_home` when a test set them, else `os.environ` / `Path.home()` — the only test seam, so no test mutates process-wide `HOME`.

```python
"""Request authentication (spec §3.3) and the /health proof (§3.4).

A pure ASGI middleware — BaseHTTPMiddleware would buffer SSE. Checks, in order:
loopback peer (403) → exact Host 127.0.0.1:<port> (421) → no Origin (403) → bearer
token (401). The only exemption is exactly GET /health, which instead returns HMAC
proofs that the server holds the token without ever receiving it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from agentd.auth_token import TokenFileError, read_token, touch_token
from agentd.startup import in_background

logger = logging.getLogger(__name__)

_NONCE_RE = re.compile(r"[0-9a-f]{16,64}")
_PROOF_PREFIX = b"crucible-health-v1\0"
_BOUND_PREFIX = b"crucible-health-bound-v1\0"
_DISABLED_WARN_EVERY = 60
_KEEPALIVE_SEC = 86400.0


@dataclass
class AuthState:
    port: int | None = None
    token: bytes | None = None
    serve_pid: int = 0
    workspace: str = ""
    disabled: bool = False


AUTH_STATE = AuthState()


class AuthStartupError(RuntimeError):
    """The app was started without what agentd.serve provides."""


def load_auth_state(state: AuthState, environ: Mapping[str, str], home: Path) -> None:
    raw_port = environ.get("CRUCIBLE_LISTEN_PORT", "")
    if not raw_port.isdigit():
        raise AuthStartupError(
            "CRUCIBLE_LISTEN_PORT is not set: start the backend with "
            "`python -m agentd.serve`, not `uvicorn agentd.main:app`")
    port = int(raw_port)
    try:
        token = read_token(home, port)
    except TokenFileError as exc:
        raise AuthStartupError(f"refusing to start: {exc}") from exc
    if token is None:
        raise AuthStartupError(
            f"no token file for port {port}; start the backend with `python -m agentd.serve`")
    raw_pid = environ.get("CRUCIBLE_SERVE_PID", "")
    state.port = port
    state.token = token.encode("ascii")
    state.serve_pid = int(raw_pid) if raw_pid.isdigit() else os.getpid()
    state.workspace = environ.get("CRUCIBLE_WORKSPACE_PATH", "")
    state.disabled = environ.get("CRUCIBLE_AUTH_DISABLED", "") == "1"
    if state.disabled:
        logger.warning(
            "CRUCIBLE_AUTH_DISABLED=1: the token check is OFF. Any local user, and any "
            "web page doing blind GETs, can drive this backend.")


def health_proof(token: bytes, nonce: str) -> str:
    return hmac.new(token, _PROOF_PREFIX + nonce.encode("ascii"), hashlib.sha256).hexdigest()


def health_bound(token: bytes, nonce: str, pid: int, workspace: str) -> str:
    message = _BOUND_PREFIX + f"{nonce}\0{pid}\0{workspace}".encode("utf-8")
    return hmac.new(token, message, hashlib.sha256).hexdigest()


def health_payload(state: AuthState, query_string: bytes) -> dict[str, object]:
    nonces = parse_qs(query_string.decode("latin-1"), keep_blank_values=True).get("nonce", [])
    if state.token is None or len(nonces) != 1 or not _NONCE_RE.fullmatch(nonces[0]):
        return {"status": "ok"}
    nonce = nonces[0]
    return {
        "status": "ok",
        "pid": state.serve_pid,
        "proof": health_proof(state.token, nonce),
        "bound": health_bound(state.token, nonce, state.serve_pid, state.workspace),
    }


def _is_loopback(client: Any) -> bool:
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def _bearer(headers: list[tuple[bytes, bytes]]) -> bytes | None:
    values = [v for k, v in headers if k == b"authorization"]
    if len(values) != 1:
        return None
    scheme, _, credential = values[0].partition(b" ")
    if scheme.lower() != b"bearer" or not credential.strip():
        return None
    return credential.strip()


async def _respond(send: Send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    def __init__(self, app: ASGIApp, state: AuthState) -> None:
        self.app = app
        self.state = state
        self._disabled_requests = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        state = self.state
        if state.port is None or state.token is None:
            await _respond(send, 503, "backend is starting")
            return
        if not _is_loopback(scope.get("client")):
            await _respond(send, 403, "only loopback clients are accepted")
            return
        headers: list[tuple[bytes, bytes]] = scope["headers"]
        hosts = [v for k, v in headers if k == b"host"]
        expected_host = f"127.0.0.1:{state.port}".encode("ascii")
        if len(hosts) != 1 or hosts[0] != expected_host:
            await _respond(send, 421, f"connect to 127.0.0.1:{state.port}")
            return
        if any(k == b"origin" for k, _ in headers):
            await _respond(send, 403, "cross-origin requests are refused")
            return
        is_health = scope["method"] == "GET" and scope["path"] == "/health"
        if not is_health:
            if state.disabled:
                self._disabled_requests += 1
                if self._disabled_requests % _DISABLED_WARN_EVERY == 0:
                    logger.warning("CRUCIBLE_AUTH_DISABLED=1: the token check is still OFF")
            else:
                presented = _bearer(headers)
                if presented is None:
                    await _respond(send, 401, "missing Authorization: Bearer token")
                    return
                if not hmac.compare_digest(presented, state.token):
                    await _respond(send, 401, "invalid token")
                    return
        await self.app(scope, receive, send)


def auth_middleware(state: AuthState) -> Middleware:
    return Middleware(AuthMiddleware, state=state)


def install_auth(app: FastAPI, state: AuthState) -> None:
    def _load() -> None:
        environ = getattr(app.state, "auth_environ", None) or os.environ
        home = getattr(app.state, "auth_home", None) or Path.home()
        load_auth_state(state, environ, home)

    async def _keepalive() -> None:
        home = getattr(app.state, "auth_home", None) or Path.home()
        while True:
            await asyncio.sleep(_KEEPALIVE_SEC)
            if state.port is not None:
                touch_token(home, state.port)

    # Index 0: every other hook was registered at import time and must run with
    # AuthState filled; the middleware answers 503 until this has run.
    app.router.on_startup.insert(0, _load)
    app.router.add_event_handler("startup", in_background(_keepalive, "token-keepalive"))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_auth_middleware.py --color=no > /tmp/t2.txt 2>&1; echo exit=$?; tail -3 /tmp/t2.txt`
Expected: `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add agentd/auth.py tests/test_auth_middleware.py
git commit -m "feat(auth): loopback/host/origin/token middleware and health proof"
```

### Task 3: `python -m agentd.serve` and the atomic lock

**Files:**
- Create: `agentd/serve.py`
- Modify: `agentd/runtime_lock.py` (atomic `write_lock(workspace, *, port, pid, started_at)`; drop `clear_lock`; docstring)
- Modify: `tests/test_runtime_lock.py` (drop the three `clear_lock` tests; add symlink-safety)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `write_token`, `tidy_tokens` (Task 1); `migrate_legacy_dirs` (`agentd/workspace_migration.py`, stdlib-only).
- Produces:
  - CLI: `python -m agentd.serve --port N [--reload] [--workspace-lock <workspace>]` (`--workspace-lock` must be the last two arguments)
  - stdout line `CRUCIBLE_SERVE {"pid": <int>, "port": <int>}` (flushed)
  - env for the app: `CRUCIBLE_LISTEN_PORT`, `CRUCIBLE_SERVE_PID`
  - exit codes: `0` normal stop, `2` bad arguments or bind failure, `3` startup failure
  - `runtime_lock.write_lock(workspace: str | Path, *, port: int, pid: int, started_at: float) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_serve.py` runs the real app with the scripted backend in a temp working directory and a temp `HOME`:

```python
"""agentd.serve (spec §3.1): bind first, then token, lock, handshake, serve."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process semantics")

NONCE = "00112233445566778899aabbccddeeff"


def _env(home: Path, ws: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CRUCIBLE_") and k != "HOME"}
    env.update({
        "HOME": str(home),
        "CRUCIBLE_REASONING_BACKEND": "scripted",
        "CRUCIBLE_WORKSPACE_PATH": str(ws),
        "CRUCIBLE_MEMORY_ENABLED": "0",
    })
    return env


def _start(tmp_path: Path, *args: str, env_extra: dict[str, str] | None = None):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    cwd = tmp_path / "cwd"
    for p in (home, ws, cwd):
        p.mkdir(exist_ok=True)
    env = {**_env(home, ws), **(env_extra or {})}
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentd.serve", *args], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, home, ws


def _handshake(proc: subprocess.Popen[str], timeout: float = 30) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("CRUCIBLE_SERVE "):
            return json.loads(line[len("CRUCIBLE_SERVE "):])
        if not line and proc.poll() is not None:
            raise AssertionError(f"exited {proc.returncode}: {proc.stderr.read()}")
    raise AssertionError("no handshake")


def _get(port: int, path: str, token: str | None = None, timeout: float = 30):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 503 and time.monotonic() < deadline:
                time.sleep(0.2)
                continue
            return exc.code, None
        except urllib.error.URLError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _stop(proc: subprocess.Popen[str]) -> int:
    proc.send_signal(signal.SIGTERM)
    return proc.wait(15)


def test_port_zero_handshake_token_lock_and_proof(tmp_path: Path) -> None:
    proc, home, ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(tmp_path / "ws"))
    try:
        hs = _handshake(proc)
        port = hs["port"]
        assert hs["pid"] == proc.pid
        token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
        lock = json.loads((ws / ".crucible/state/agentd.lock").read_text())
        assert lock["pid"] == proc.pid and lock["port"] == port
        status, body = _get(port, f"/health?nonce={NONCE}")
        assert status == 200 and body["pid"] == proc.pid
        expected = hmac.new(token.encode(), b"crucible-health-v1\0" + NONCE.encode(),
                            hashlib.sha256).hexdigest()
        assert body["proof"] == expected
        assert _get(port, "/v1/config")[0] == 401
        assert _get(port, "/v1/config", token)[0] == 200
    finally:
        assert _stop(proc) == 0
    # Never deleted at shutdown (spec §3.1).
    assert (home / ".crucible/run" / f"agentd-{port}.token").exists()


def test_busy_port_exits_without_touching_files(tmp_path: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        proc, home, ws = _start(tmp_path, "--port", str(port),
                                "--workspace-lock", str(tmp_path / "ws"))
        assert proc.wait(30) == 2
    assert not (home / ".crucible/run" / f"agentd-{port}.token").exists()
    assert not (ws / ".crucible/state/agentd.lock").exists()


def test_startup_failure_exits_3(tmp_path: Path) -> None:
    proc, _home, _ws = _start(tmp_path, "--port", "0",
                              env_extra={"CRUCIBLE_AST_CUTOVER_MODE": "soft"})
    _handshake(proc)
    assert proc.wait(60) == 3


def test_old_token_on_the_port_is_overwritten(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run = tmp_path / "home/.crucible/run"
    run.mkdir(parents=True, mode=0o700)
    os.chmod(tmp_path / "home/.crucible", 0o700)
    stale = run / f"agentd-{port}.token"
    stale.write_text("s" * 43)
    os.chmod(stale, 0o600)
    proc, _home, _ws = _start(tmp_path, "--port", str(port))
    try:
        _handshake(proc)
        assert stale.read_text() != "s" * 43
    finally:
        _stop(proc)


def test_lock_write_does_not_follow_a_symlink(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    state = ws / ".crucible/state"
    state.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("untouched")
    os.symlink(target, state / "agentd.lock")
    proc, _home, _ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(ws))
    try:
        _handshake(proc)
        assert target.read_text() == "untouched"
        assert not (state / "agentd.lock").is_symlink()
    finally:
        _stop(proc)


def test_legacy_dirs_migrated_before_lock(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".ai-editor").mkdir(parents=True)
    (ws / ".ai-editor/marker").write_text("m")
    proc, _home, _ws = _start(tmp_path, "--port", "0", "--workspace-lock", str(ws))
    try:
        _handshake(proc)
        assert (ws / ".crucible/marker").read_text() == "m"
        assert (ws / ".crucible/state/agentd.lock").exists()
    finally:
        _stop(proc)


def test_reload_keeps_token_and_pid(tmp_path: Path) -> None:
    proc, home, ws = _start(tmp_path, "--port", "0", "--reload",
                            "--workspace-lock", str(tmp_path / "ws"))
    try:
        port = _handshake(proc)["port"]
        token_file = home / ".crucible/run" / f"agentd-{port}.token"
        token = token_file.read_text()
        assert _get(port, f"/health?nonce={NONCE}")[1]["pid"] == proc.pid
        (tmp_path / "cwd" / "touch_me.py").write_text("x = 1\n")  # trigger a reload
        time.sleep(4)
        status, body = _get(port, f"/health?nonce={NONCE}")
        assert status == 200 and body["pid"] == proc.pid
        assert token_file.read_text() == token
        assert _get(port, "/v1/config", token)[0] == 200
    finally:
        _stop(proc)


def test_stop_with_open_sse_stream_finishes_quickly(tmp_path: Path) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    port = _handshake(proc)["port"]
    token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
    _get(port, "/v1/config", token)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/channels/c1/stream",
                                 headers={"Authorization": f"Bearer {token}"})
    stream = urllib.request.urlopen(req, timeout=30)
    try:
        started = time.monotonic()
        assert _stop(proc) == 0
        assert time.monotonic() - started < 9
    finally:
        stream.close()


def test_import_creates_no_files_and_stays_light(tmp_path: Path) -> None:
    code = ("import sys, agentd.serve; "
            "assert 'agentd.main' not in sys.modules; "
            "assert 'fastapi' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True,
                   env=_env(tmp_path, tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows socket option")
def test_windows_second_bind_fails() -> None:
    from agentd.serve import _bind

    first = _bind(0)
    try:
        with pytest.raises(OSError):
            _bind(first.getsockname()[1])
    finally:
        first.close()


def test_bare_uvicorn_refuses_to_start(tmp_path: Path) -> None:
    home, ws, cwd = tmp_path / "h", tmp_path / "w", tmp_path / "c"
    for p in (home, ws, cwd):
        p.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "agentd.main:app", "--port", str(port)],
        cwd=cwd, env=_env(home, ws), capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "agentd.serve" in proc.stderr
```

Add to `tests/test_runtime_lock.py` (and delete the three `clear_lock` tests and its import):

```python
def test_write_lock_replaces_a_symlink_without_following_it(tmp_path: Path) -> None:
    state = tmp_path / ".crucible/state"
    state.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("keep")
    os.symlink(target, state / "agentd.lock")
    write_lock(tmp_path, port=1, pid=2, started_at=3.0)
    assert target.read_text() == "keep"
    assert read_lock(tmp_path) == LockInfo(pid=2, port=1, started_at=3.0)
```

Update the existing calls in that file from `write_lock(tmp_path, port=8123)` to `write_lock(tmp_path, port=8123, pid=os.getpid(), started_at=time.time())` (import `time`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_serve.py tests/test_runtime_lock.py --color=no --timeout=120 > /tmp/t3.txt 2>&1; echo exit=$?; tail -8 /tmp/t3.txt`
Expected: exit≠0 (`No module named agentd.serve`; `write_lock() got an unexpected keyword argument`).

- [ ] **Step 3: Rewrite `write_lock` in `agentd/runtime_lock.py`**

Replace the module docstring's last sentence with "Written by `agentd.serve --workspace-lock` (spec §3.1); a hint that reuse verifies (§3.7), never deleted at shutdown." Delete `clear_lock`. Replace `write_lock`:

```python
def write_lock(
    workspace: str | Path, *, port: int, pid: int, started_at: float,
) -> None:
    """Atomic and symlink-safe: a fresh O_EXCL|O_NOFOLLOW temp file, then rename (which
    replaces a planted symlink rather than writing through it)."""
    path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".agentd.lock.{secrets.token_hex(8)}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        os.write(fd, json.dumps(
            {"pid": pid, "port": port, "started_at": started_at}).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)
```

Add `import secrets` at the top.

- [ ] **Step 4: Implement `agentd/serve.py`**

```python
"""`python -m agentd.serve` — the only way to start agentd (spec §3.1).

Binds 127.0.0.1 first, then writes the token and the lock, prints a handshake line and
serves the pre-bound socket. Imports only the standard library and uvicorn: importing
agentd.main creates databases, logs and shadows relative to the working directory.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import uvicorn
from uvicorn.supervisors import ChangeReload

from agentd.auth_token import tidy_tokens, write_token
from agentd.runtime_lock import write_lock
from agentd.workspace_migration import migrate_legacy_dirs

APP = "agentd.main:app"
EXIT_BIND_FAILED = 2
EXIT_STARTUP_FAILED = 3
_GRACEFUL_SHUTDOWN_SEC = 5
_RELOADED_ENV = "CRUCIBLE_SERVE_RELOADED"


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m agentd.serve")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workspace-lock", default=None)
    args = parser.parse_args(argv)
    if args.workspace_lock is not None and argv[-2:] != ["--workspace-lock", args.workspace_lock]:
        parser.error("--workspace-lock <workspace> must be the last two arguments")
    return args


def _bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(2048)  # at once: see spec §3.1 on SO_REUSEADDR co-binding
    return sock


class _Worker:
    """Reload-worker target: picklable, so ChangeReload can hand it to the spawned child."""

    def __init__(self, config: uvicorn.Config) -> None:
        self.config = config

    def __call__(self, sockets: list[socket.socket] | None = None) -> None:
        for sock in sockets or []:
            sock.set_inheritable(False)
        server = uvicorn.Server(self.config)
        try:
            server.run(sockets=sockets)
        except SystemExit:
            pass
        if not server.started:
            # The first worker failing means nothing will ever serve this socket: take
            # the supervisor down too. A failure after a reload (a typo mid-edit) keeps
            # the supervisor watching, as plain uvicorn does.
            if os.environ.get(_RELOADED_ENV) != "1":
                os.kill(os.getppid(), signal.SIGTERM)
            sys.exit(EXIT_STARTUP_FAILED)


class _Reloader(ChangeReload):
    def restart(self) -> None:
        os.environ[_RELOADED_ENV] = "1"  # inherited by every later worker
        super().restart()


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.workspace_lock is not None:
        migrate_legacy_dirs(Path(args.workspace_lock))
    try:
        sock = _bind(args.port)
    except OSError as exc:
        print(f"agentd.serve: cannot bind 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        sys.exit(EXIT_BIND_FAILED)
    port = sock.getsockname()[1]
    write_token(Path.home(), port)
    if args.workspace_lock is not None:
        write_lock(args.workspace_lock, port=port, pid=os.getpid(), started_at=time.time())
    os.environ["CRUCIBLE_LISTEN_PORT"] = str(port)
    os.environ["CRUCIBLE_SERVE_PID"] = str(os.getpid())
    tidy_tokens(Path.home())
    print(f"CRUCIBLE_SERVE {json.dumps({'pid': os.getpid(), 'port': port})}", flush=True)

    config = uvicorn.Config(
        APP, host="127.0.0.1", port=port, reload=args.reload,
        timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SEC)
    if args.reload:
        reloader = _Reloader(config, target=_Worker(config), sockets=[sock])
        reloader.run()
        code = reloader.process.exitcode if reloader.process is not None else 0
        sys.exit(EXIT_STARTUP_FAILED if code == EXIT_STARTUP_FAILED else 0)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    except SystemExit:
        pass
    sys.exit(0 if server.started else EXIT_STARTUP_FAILED)


if __name__ == "__main__":
    main()
```

Note `agentd.auth_token`, `agentd.runtime_lock` and `agentd.workspace_migration` are standard-library-only modules, so the import stays light (the `test_import_creates_no_files_and_stays_light` test guards it).

- [ ] **Step 5: Run to verify pass**

`test_bare_uvicorn_refuses_to_start` stays red until Task 4. Run everything else:
`.venv/bin/pytest tests/test_serve.py tests/test_runtime_lock.py --color=no --timeout=120 --deselect tests/test_serve.py::test_bare_uvicorn_refuses_to_start --deselect tests/test_serve.py::test_port_zero_handshake_token_lock_and_proof > /tmp/t3.txt 2>&1; echo exit=$?; tail -8 /tmp/t3.txt`
Expected: `exit=0`. (`test_port_zero…` also asserts the 401 that Task 4 adds.)

- [ ] **Step 6: Commit**

```bash
git add agentd/serve.py agentd/runtime_lock.py tests/test_serve.py tests/test_runtime_lock.py
git commit -m "feat(auth): agentd.serve binds first, writes token and lock, handshakes"
```

### Task 4: Wire the app (`main.py`)

**Files:**
- Modify: `agentd/main.py` (constructor middleware, `install_auth`, `/health`, delete the `CRUCIBLE_PORT` lock block at lines ~324–339)
- Test: `tests/test_main_auth_wiring.py`

**Interfaces:**
- Consumes: `AUTH_STATE`, `auth_middleware`, `install_auth`, `health_payload` (Task 2).

- [ ] **Step 1: Write the failing test**

The only test that imports `agentd.main`, so it runs in a subprocess with a temp working directory:

```python
"""main.py wiring: the auth middleware is the outermost user middleware (spec §3.3)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CHECK = """
from agentd.auth import AuthMiddleware
from agentd.main import app
assert app.user_middleware, "no user middleware"
assert app.user_middleware[0].cls is AuthMiddleware, app.user_middleware
assert app.router.on_startup[0].__name__ == "_load", app.router.on_startup
print("ok")
"""


def test_auth_is_outermost_and_loads_first(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRUCIBLE_")}
    env.update({"CRUCIBLE_REASONING_BACKEND": "scripted", "CRUCIBLE_MEMORY_ENABLED": "0",
                "CRUCIBLE_WORKSPACE_PATH": str(tmp_path), "HOME": str(tmp_path)})
    out = subprocess.run([sys.executable, "-c", CHECK], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_main_auth_wiring.py --color=no --timeout=120 > /tmp/t4.txt 2>&1; echo exit=$?; tail -5 /tmp/t4.txt`
Expected: exit≠0, `no user middleware`.

- [ ] **Step 3: Edit `agentd/main.py`**

1. Imports (with the other `agentd` imports, below `from fastapi import FastAPI`): `from fastapi import FastAPI, Request` and `from agentd.auth import AUTH_STATE, auth_middleware, health_payload, install_auth`.
2. Replace `app = FastAPI(title="crucible agentd-py", version="0.1.0")` with:

```python
# Auth runs outermost (spec §3.3). Passed in the constructor: a later add_middleware
# would insert OUTSIDE it, which tests/test_main_auth_wiring.py guards against.
app = FastAPI(
    title="crucible agentd-py", version="0.1.0", middleware=[auth_middleware(AUTH_STATE)])
```

3. Delete the whole `# Managed-spawn lockfile: …` block (the `_lock_port_raw` / `_write_runtime_lock` / `_clear_runtime_lock` code). `agentd.serve` writes the lock now and nothing deletes it.
4. Immediately after the `app.include_router(...)` call add `install_auth(app, AUTH_STATE)` (after every other hook is registered, so the index-0 insert really is first).
5. Replace the health route:

```python
@app.get("/health")
async def healthcheck(request: Request) -> dict[str, object]:
    # No token required (the middleware exempts exactly GET /health); with a nonce it
    # proves the server holds the token and names its pid (spec §3.4).
    return health_payload(AUTH_STATE, request.scope["query_string"])
```

- [ ] **Step 4: Run Tasks 2–4 tests**

Run: `.venv/bin/pytest tests/test_main_auth_wiring.py tests/test_serve.py tests/test_auth_middleware.py --color=no --timeout=120 > /tmp/t4.txt 2>&1; echo exit=$?; tail -5 /tmp/t4.txt`
Expected: `exit=0` (including `test_bare_uvicorn_refuses_to_start` and `test_port_zero…`).

- [ ] **Step 5: Commit**

```bash
git add agentd/main.py tests/test_main_auth_wiring.py
git commit -m "feat(auth): install the auth middleware and health proof in main.py"
```

### Task 5: `child_env()` at every subprocess site

**Files:**
- Create: `agentd/child_env.py`
- Modify (each site's `env`): `agentd/tools/shell.py`, `agentd/tools/env.py` (4 calls), `agentd/tools/search.py`, `agentd/tools/post_patch/checker.py`, `agentd/exec_sessions/manager.py`, `agentd/exec_sessions/registry_file.py`, `agentd/env/probe.py`, `agentd/retrieval/artifact_client.py`, `agentd/validation/command_validator.py` (2), `agentd/orchestrator/engine.py` (`_build_test_env` and the exec site)
- Test: `tests/test_child_env.py`

**Interfaces:**
- Produces: `child_env(base: Mapping[str, str] | None = None) -> dict[str, str]` — a copy of `base` (default `os.environ`) without `CRUCIBLE_LISTEN_PORT` and `CRUCIBLE_SERVE_PID`.

- [ ] **Step 1: Write the failing tests**

```python
"""Every backend subprocess gets child_env() (spec §3.5): a nested app started by an
agent or validator must refuse to start, not inherit the parent's port."""
from __future__ import annotations

import ast
from pathlib import Path

from agentd.child_env import child_env

ROOT = Path(__file__).resolve().parents[1] / "agentd"
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}
_ASYNC_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}
# The MCP SDK builds stdio env from its own allowlist; pty_process receives the env its
# only caller (exec_sessions/manager.py) built with child_env().
_EXEMPT = {"mcp/client.py", "exec_sessions/pty_process.py"}


def test_child_env_strips_listen_port_and_pid() -> None:
    env = child_env({"CRUCIBLE_LISTEN_PORT": "1", "CRUCIBLE_SERVE_PID": "2", "PATH": "/b"})
    assert env == {"PATH": "/b"}


def _references(tree: ast.AST) -> list[ast.Attribute]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "subprocess" and node.attr in _SUBPROCESS_ATTRS:
                out.append(node)
            if node.value.id == "asyncio" and node.attr in _ASYNC_ATTRS:
                out.append(node)
    return out


def test_every_subprocess_reference_uses_child_env() -> None:
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)  # one tree: the identity check below needs it
        refs = _references(tree)
        if not refs:
            continue
        if "child_env(" not in source:
            offenders.append(f"{rel}: references subprocess but never calls child_env()")
        direct_calls = [n for n in ast.walk(tree)
                        if isinstance(n, ast.Call) and any(n.func is r for r in refs)]
        for call in direct_calls:
            if not any(kw.arg == "env" for kw in call.keywords):
                offenders.append(f"{rel}:{call.lineno}: subprocess call without env=")
    assert offenders == []
```

`post_patch/checker.py` passes `subprocess.run` as a value to `asyncio.to_thread(...)`; the "references subprocess but never calls `child_env()`" rule covers it — give that `to_thread` call an `env=child_env()` keyword (which `to_thread` forwards to `subprocess.run`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_child_env.py --color=no > /tmp/t5.txt 2>&1; echo exit=$?; tail -15 /tmp/t5.txt`
Expected: exit≠0 (`No module named agentd.child_env`).

- [ ] **Step 3: Create `agentd/child_env.py`**

```python
"""Environment for every subprocess the backend starts (spec §3.5)."""
from __future__ import annotations

import os
from collections.abc import Mapping

# Set by agentd.serve for this process only. A child that inherited them could start a
# nested app that believes it owns the parent's port and token.
_SERVE_ONLY = ("CRUCIBLE_LISTEN_PORT", "CRUCIBLE_SERVE_PID")


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    return {k: v for k, v in source.items() if k not in _SERVE_ONLY}
```

- [ ] **Step 4: Apply at every site**

Rule: where a site builds `env = os.environ.copy()` (or `dict(os.environ)`), replace that expression with `child_env()`; where it builds env from another function's result, wrap that result: `env=child_env(<expr>)`; where it passes no `env`, add `env=child_env()`. Import `from agentd.child_env import child_env` at each module's top. Concretely:

- `tools/shell.py`: the `env = os.environ.copy()` above line ~150 → `env = child_env()`.
- `tools/env.py`: the four `create_subprocess_exec` calls (lines ~99, ~131, ~362, ~506): the first three pass no `env` → add `env=child_env()`; the fourth builds `env` from `os.environ.copy()` → `child_env()`.
- `tools/search.py:59`: add `env=child_env()`.
- `tools/post_patch/checker.py:43`: add `env=child_env()` to the `asyncio.to_thread(subprocess.run, …)` call.
- `exec_sessions/manager.py:178`: `env = os.environ.copy()` → `env = child_env()`.
- `exec_sessions/registry_file.py:72`: add `env=child_env()`.
- `env/probe.py:155`: add `env=child_env()`.
- `retrieval/artifact_client.py:312`: add `env=child_env()` (or wrap its existing `env`).
- `validation/command_validator.py`: `_run_process_exec` add `env=child_env()`; `_run_command` → `prepend_pythonpath(child_env(), …)`.
- `orchestrator/engine.py`: inside `_build_test_env`, its `os.environ.copy()` → `child_env()`.

- [ ] **Step 5: Run the test and the touched modules' suites**

Run: `.venv/bin/pytest tests/test_child_env.py tests/test_shell_tool.py tests/test_exec_sessions_manager.py tests/test_command_validator.py --color=no --timeout=120 > /tmp/t5.txt 2>&1; echo exit=$?; tail -5 /tmp/t5.txt`
(If a listed test file doesn't exist, find the module's tests with `grep -l "<module name>" tests/*.py` and run those.)
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add agentd/child_env.py agentd/tools agentd/exec_sessions agentd/env agentd/retrieval agentd/validation agentd/orchestrator/engine.py tests/test_child_env.py
git commit -m "feat(auth): strip serve-only env from every backend subprocess"
```

### Task 6: GET-route allowlist

**Files:**
- Test: `tests/test_get_routes_read_only.py`

Spec §4: the Origin check plus the token stop cross-site writes; a plain cross-site GET still reaches the token check, so GET routes must stay free of side effects. The test pins today's reviewed set so a new GET route fails until someone reviews it.

- [ ] **Step 1: Generate the current list**

Run (prints the set to paste into the test):

```bash
.venv/bin/python - <<'EOF'
from agentd.chat.app_factory import build_app
app = build_app()
print(sorted({r.path for r in app.routes if "GET" in getattr(r, "methods", set())}))
EOF
```

If `build_app` needs arguments, read `agentd/chat/app_factory.py` and pass the minimal ones (it is the test-only factory using `ScriptedReasoningEngine`). Read each listed route handler in `agentd/api/routes.py` and confirm it only reads. Known exception, accepted: `GET /v1/channels/{channel_id}/stream` creates an empty replay entry for an unknown channel.

- [ ] **Step 2: Write the test with that list**

```python
"""Every GET route is reviewed read-only (spec §4). A cross-site <img>/<script> GET
carries no Origin and is stopped only by the token — so a GET must never write.
A new GET route fails this test until it is reviewed and added."""
from __future__ import annotations

from agentd.chat.app_factory import build_app

REVIEWED_READ_ONLY_GET_ROUTES = frozenset({
    # paste the list printed in Step 1 here, one path per line
})


def test_get_routes_are_reviewed() -> None:
    app = build_app()
    found = {r.path for r in app.routes if "GET" in getattr(r, "methods", set())}
    assert found - REVIEWED_READ_ONLY_GET_ROUTES == set(), "review these GET routes"
```

- [ ] **Step 3: Run**

Run: `.venv/bin/pytest tests/test_get_routes_read_only.py --color=no > /tmp/t6.txt 2>&1; echo exit=$?; tail -3 /tmp/t6.txt`
Expected: `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_get_routes_read_only.py
git commit -m "test(auth): pin the reviewed read-only GET routes"
```

---
# Part B — editor-client (`apps/editor-client`)

### Task 7: Auth options, `BackendAuthError`, no redirects

**Files:**
- Modify: `src/client/http-backend-client.ts` (options interface ~line 72, constructor ~108, `discardInlineChange` ~982)
- Test: `test/auth.test.ts`

**Interfaces:**
- Produces (exported from the package index via `client/http-backend-client.ts`):
  - `HttpBackendClientOptions` gains `authToken?: () => string | undefined` and `onAuthStatus?: (ok: boolean, reason?: string) => void`
  - `export class BackendAuthError extends Error { readonly status: number | null }` — `constructor(message: string, status: number | null)`
  - every request goes through the wrapped fetch: header `authorization: Bearer <token>` when `authToken()` returns one; `redirect: "manual"`; 401/403/421 → `onAuthStatus(false, reason)` then throw `BackendAuthError`; any other response (including a mock with no `status`) → `onAuthStatus(true)`.

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it, vi } from "vitest";
import { BackendAuthError, HttpBackendClient } from "../src/client/http-backend-client";

const okJson = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("auth options", () => {
  it("sends the bearer header and never follows redirects", async () => {
    const fetchFn = vi.fn().mockResolvedValue(okJson({ status: "ok" }));
    const c = new HttpBackendClient({ baseUrl: "http://127.0.0.1:9", fetchFn,
      authToken: () => "T".repeat(43) });
    await c.getConfig().catch(() => undefined);
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${"T".repeat(43)}`);
    expect(init.redirect).toBe("manual");
  });

  it("omits the header when authToken returns undefined or is absent", async () => {
    for (const authToken of [() => undefined, undefined]) {
      const fetchFn = vi.fn().mockResolvedValue(okJson({}));
      const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn,
        ...(authToken ? { authToken } : {}) });
      await c.getConfig().catch(() => undefined);
      const headers = (fetchFn.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
      expect(headers.authorization).toBeUndefined();
    }
  });

  it.each([401, 403, 421])("%i raises BackendAuthError and reports not ok", async (status) => {
    const onAuthStatus = vi.fn();
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status, statusText: "no",
      json: async () => ({}), text: async () => "" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, onAuthStatus });
    const err = await c.getConfig().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(BackendAuthError);
    expect((err as BackendAuthError).status).toBe(status);
    expect(onAuthStatus).toHaveBeenCalledWith(false, expect.stringContaining(String(status)));
  });

  it("reports ok on a non-auth response, including a mock with no status", async () => {
    const onAuthStatus = vi.fn();
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, onAuthStatus });
    await c.getConfig().catch(() => undefined);
    expect(onAuthStatus).toHaveBeenCalledWith(true);
  });

  it("covers raw call sites: the chat channel stream carries the header", async () => {
    const reader = { read: vi.fn().mockResolvedValue({ done: true }), cancel: vi.fn() };
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, status: 200,
      body: { getReader: () => reader } });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, authToken: () => "k" });
    for await (const _ of c.streamChannel("chat:t")) { /* empty */ }
    const headers = (fetchFn.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer k");
    expect(headers.accept).toBe("text/event-stream");
  });

  it("discardInlineChange surfaces a failed response", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "boom" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    await expect(c.discardInlineChange("i1")).rejects.toThrow(/500/);
  });
});
```

Read `streamChannel`'s current signature first (`grep -n "streamChannel" src/client/http-backend-client.ts`); adjust the call in the test if it takes more arguments.

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w @crucible/editor-client test -- test/auth.test.ts`
Expected: FAIL (`BackendAuthError` is not exported).

- [ ] **Step 3: Implement**

In `http-backend-client.ts`:

```ts
interface HttpBackendClientOptions {
  baseUrl: string;
  fetchFn?: FetchLike;
  /** Bearer token for this backend; called per request (a restart rewrites it). */
  authToken?: () => string | undefined;
  /** Every response reports here: ok on any non-auth status, not ok on 401/403/421. */
  onAuthStatus?: (ok: boolean, reason?: string) => void;
}

const AUTH_FAILURE_STATUSES = new Set([401, 403, 421]);

/** The backend refused this client (spec §3.5): wrong/missing token, wrong host, or
 * a check that failed before the request was sent. Not retried. */
export class BackendAuthError extends Error {
  constructor(message: string, readonly status: number | null) {
    super(message);
    this.name = "BackendAuthError";
  }
}

function withAuthHeader(init: RequestInit | undefined, token: string | undefined): RequestInit {
  const headers = { ...((init?.headers as Record<string, string> | undefined) ?? {}) };
  if (token) headers.authorization = `Bearer ${token}`;
  return { ...init, headers, redirect: "manual" };
}
```

Constructor:

```ts
  constructor(private readonly options: HttpBackendClientOptions) {
    const raw = options.fetchFn ?? fetch;
    // Wrapped once here so every call — fetchJson and the raw stream/decision sites —
    // carries the token, never follows a redirect, and reports auth status.
    this.fetchFn = async (input, init) => {
      const response = await raw(input, withAuthHeader(init, options.authToken?.()));
      const status = (response as { status?: number }).status;
      if (status !== undefined && AUTH_FAILURE_STATUSES.has(status)) {
        const reason = `backend refused the request (${status})`;
        options.onAuthStatus?.(false, reason);
        throw new BackendAuthError(`${reason} for ${input}`, status);
      }
      options.onAuthStatus?.(true);
      return response;
    };
  }
```

`discardInlineChange`:

```ts
  async discardInlineChange(inlineTaskId: string): Promise<void> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/inline-changes/${encodeURIComponent(inlineTaskId)}`,
      { method: "DELETE", headers: { "content-type": "application/json" } }
    );
    if (!response.ok) {
      throw new Error(
        `Backend request failed (${response.status} ${response.statusText}) for discardInlineChange`);
    }
  }
```

Any raw call site passing `headers` as something other than a plain object literal must be changed to a plain object (check with `grep -n "headers:" src/client/http-backend-client.ts`).

- [ ] **Step 4: Run the package's whole suite**

Run: `npm run -w @crucible/editor-client test`
Expected: PASS. An existing test that asserts the exact `init` object with `toHaveBeenCalledWith(url, {...})` (not `objectContaining`) now sees `redirect` and `headers`: switch that assertion to `expect.objectContaining({...})`, keeping its fields.

- [ ] **Step 5: Build (the extension types off `dist`) and commit**

```bash
npm run -w @crucible/editor-client build
git add apps/editor-client/src apps/editor-client/test
git commit -m "feat(editor-client): bearer token, auth status reporting, no redirects"
```

---

# Part C — VS Code extension (`apps/vscode-extension`)

Run tests with `npm run -w crucible-vscode-extension test -- <file>` and the typecheck with `npm run -w crucible-vscode-extension typecheck`.

### Task 8: Token reader and `probeHealth`

**Files:**
- Create: `src/runtime/backend-token.ts`, `src/runtime/probe-health.ts`
- Test: `test/backend-token.test.ts`, `test/probe-health.test.ts`

**Interfaces:**
- Produces:
  - `backend-token.ts`: `runDir(home?: string): string`; `tokenPath(port: number, home?: string): string`; `readBackendToken(port: number, home?: string): string | undefined` (cached by `(path, mtimeMs)`; a missing file is never cached; an unsafe or malformed file → `undefined` plus no cache).
  - `probe-health.ts`:
    - `type ProbeResult = "authed" | "other-backend" | "preauth" | "unauthorized" | "down" | "no-token"`
    - `interface ProbeDeps { fetchRaw(url: string, init: { signal: AbortSignal }): Promise<{ status: number; body: string }>; readToken(port: number): string | undefined; nonce?: () => string }`
    - `probeHealth(port: number, deps: ProbeDeps, expect?: { pid: number; workspace: string }): Promise<ProbeResult>`
    - `healthProof(token: string, nonce: string): string`, `healthBound(token: string, nonce: string, pid: number, workspace: string): string`
    - `PROBE_TIMEOUT_MS = 2000`

Classification order: connection error, timeout or HTTP 503 (the app is still loading its token, spec §3.3) → `down`; no readable token → `no-token`; 403/421 → `unauthorized`; 200 without `proof` → `preauth`; wrong `proof` → `unauthorized`; with `expect`, a `pid` ≠ `expect.pid` or a wrong `bound` → `other-backend`; otherwise `authed`. The probe never sends a token.

- [ ] **Step 1: Write the failing tests**

`test/backend-token.test.ts`:

```ts
import { chmodSync, mkdirSync, mkdtempSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { readBackendToken, tokenPath } from "../src/runtime/backend-token.js";

const TOKEN = "a".repeat(43);

function home(): string {
  const h = mkdtempSync(join(tmpdir(), "home-"));
  mkdirSync(join(h, ".crucible", "run"), { recursive: true, mode: 0o700 });
  return h;
}

function put(h: string, port: number, content: string, mode = 0o600): void {
  writeFileSync(tokenPath(port, h), content);
  chmodSync(tokenPath(port, h), mode);
}

describe.skipIf(process.platform === "win32")("readBackendToken", () => {
  it("reads a well-formed owner-only file", () => {
    const h = home();
    put(h, 1, TOKEN);
    expect(readBackendToken(1, h)).toBe(TOKEN);
  });
  it("is undefined for a missing file, and does not cache the miss", () => {
    const h = home();
    expect(readBackendToken(2, h)).toBeUndefined();
    put(h, 2, TOKEN);
    expect(readBackendToken(2, h)).toBe(TOKEN);
  });
  it("refuses group-readable, symlinked and malformed files", () => {
    const h = home();
    put(h, 3, TOKEN, 0o640);
    expect(readBackendToken(3, h)).toBeUndefined();
    put(h, 4, TOKEN);
    symlinkSync(tokenPath(4, h), tokenPath(5, h));
    expect(readBackendToken(5, h)).toBeUndefined();
    put(h, 6, `${TOKEN}\n`);
    expect(readBackendToken(6, h)).toBeUndefined();
  });
  it("re-reads after a restart rewrites the file", () => {
    const h = home();
    put(h, 7, TOKEN);
    expect(readBackendToken(7, h)).toBe(TOKEN);
    put(h, 7, "b".repeat(43));
    const later = new Date(Date.now() + 5000);
    utimesSync(tokenPath(7, h), later, later);
    expect(readBackendToken(7, h)).toBe("b".repeat(43));
  });
});
```

`test/probe-health.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { healthBound, healthProof, probeHealth, type ProbeDeps } from "../src/runtime/probe-health.js";

const TOKEN = "k".repeat(43);
const NONCE = "00112233445566778899aabbccddeeff";

function deps(respond: (url: string) => { status: number; body: string } | Error,
              token: string | undefined = TOKEN): ProbeDeps & { urls: string[] } {
  const urls: string[] = [];
  return {
    urls,
    nonce: () => NONCE,
    readToken: () => token,
    fetchRaw: vi.fn(async (url: string) => {
      urls.push(url);
      const r = respond(url);
      if (r instanceof Error) throw r;
      return r;
    }),
  };
}

const authedBody = (pid = 7, ws = "/w") => JSON.stringify({
  status: "ok", pid, proof: healthProof(TOKEN, NONCE), bound: healthBound(TOKEN, NONCE, pid, ws) });

describe("probeHealth", () => {
  it("is authed for a valid proof and probes 127.0.0.1 without a token", async () => {
    const d = deps(() => ({ status: 200, body: authedBody() }));
    expect(await probeHealth(9, d)).toBe("authed");
    expect(d.urls[0]).toBe(`http://127.0.0.1:9/health?nonce=${NONCE}`);
    expect((d.fetchRaw as ReturnType<typeof vi.fn>).mock.calls[0][1]).not.toHaveProperty("headers");
  });
  it("checks identity when expect is given", async () => {
    const d = deps(() => ({ status: 200, body: authedBody(7, "/w") }));
    expect(await probeHealth(9, d, { pid: 7, workspace: "/w" })).toBe("authed");
    expect(await probeHealth(9, d, { pid: 8, workspace: "/w" })).toBe("other-backend");
    expect(await probeHealth(9, d, { pid: 7, workspace: "/other" })).toBe("other-backend");
  });
  it("classifies the rest", async () => {
    expect(await probeHealth(9, deps(() => ({ status: 200, body: '{"status":"ok"}' })))).toBe("preauth");
    expect(await probeHealth(9, deps(() => ({ status: 421, body: "" })))).toBe("unauthorized");
    expect(await probeHealth(9, deps(() => ({ status: 403, body: "" })))).toBe("unauthorized");
    const wrong = JSON.stringify({ status: "ok", pid: 7, proof: "00", bound: "00" });
    expect(await probeHealth(9, deps(() => ({ status: 200, body: wrong })))).toBe("unauthorized");
    expect(await probeHealth(9, deps(() => new TypeError("fetch failed")))).toBe("down");
    expect(await probeHealth(9, deps(() => ({ status: 503, body: "" })))).toBe("down");
    expect(await probeHealth(9, deps(() => ({ status: 200, body: authedBody() }), undefined)))
      .toBe("no-token");
  });
  it("gives up after 2 s", async () => {
    vi.useFakeTimers();
    const d: ProbeDeps = { readToken: () => TOKEN, nonce: () => NONCE,
      fetchRaw: (_url, init) => new Promise((_res, rej) =>
        init.signal.addEventListener("abort", () => rej(new Error("aborted")))) };
    const pending = probeHealth(9, d);
    await vi.advanceTimersByTimeAsync(2001);
    expect(await pending).toBe("down");
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/backend-token.test.ts test/probe-health.test.ts`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `src/runtime/backend-token.ts`**

```ts
// vscode-free. Reads <home>/.crucible/run/agentd-<port>.token (spec §3.2): O_NOFOLLOW,
// then fstat THE DESCRIPTOR — never stat-then-open.
import { closeSync, constants, fstatSync, openSync, readSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const cache = new Map<string, { mtimeMs: number; token: string }>();

export function runDir(home: string = homedir()): string {
  return join(home, ".crucible", "run");
}

export function tokenPath(port: number, home: string = homedir()): string {
  return join(runDir(home), `agentd-${port}.token`);
}

export function readBackendToken(port: number, home: string = homedir()): string | undefined {
  const path = tokenPath(port, home);
  let fd: number;
  try {
    fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  } catch {
    cache.delete(path);
    return undefined; // missing, or a symlink (ELOOP)
  }
  try {
    const st = fstatSync(fd);
    if (process.platform !== "win32") {
      const uid = process.getuid?.();
      if (!st.isFile() || (uid !== undefined && st.uid !== uid) || (st.mode & 0o077) !== 0) {
        cache.delete(path);
        return undefined;
      }
    }
    const hit = cache.get(path);
    if (hit && hit.mtimeMs === st.mtimeMs) return hit.token;
    const buf = Buffer.alloc(44);
    const n = readSync(fd, buf, 0, 44, 0);
    const text = buf.subarray(0, n).toString("ascii");
    if (n !== 43 || !TOKEN_RE.test(text)) {
      cache.delete(path);
      return undefined;
    }
    cache.set(path, { mtimeMs: st.mtimeMs, token: text });
    return text;
  } finally {
    closeSync(fd);
  }
}
```

- [ ] **Step 4: Implement `src/runtime/probe-health.ts`**

```ts
// vscode-free. GET /health?nonce= and verify the HMAC proofs (spec §3.4). Never sends
// the token: a client learns the server holds it without revealing it.
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export type ProbeResult =
  | "authed" | "other-backend" | "preauth" | "unauthorized" | "down" | "no-token";

export interface ProbeDeps {
  fetchRaw(url: string, init: { signal: AbortSignal }): Promise<{ status: number; body: string }>;
  readToken(port: number): string | undefined;
  nonce?: () => string;
}

export const PROBE_TIMEOUT_MS = 2000;

export function healthProof(token: string, nonce: string): string {
  return createHmac("sha256", token).update(`crucible-health-v1\0${nonce}`, "utf8").digest("hex");
}

export function healthBound(token: string, nonce: string, pid: number, workspace: string): string {
  return createHmac("sha256", token)
    .update(`crucible-health-bound-v1\0${nonce}\0${pid}\0${workspace}`, "utf8")
    .digest("hex");
}

function sameHex(a: unknown, b: string): boolean {
  if (typeof a !== "string" || a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a), Buffer.from(b));
}

export async function probeHealth(
  port: number, deps: ProbeDeps, expect?: { pid: number; workspace: string },
): Promise<ProbeResult> {
  const nonce = deps.nonce?.() ?? randomBytes(16).toString("hex");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  let response: { status: number; body: string };
  try {
    response = await deps.fetchRaw(
      `http://127.0.0.1:${port}/health?nonce=${nonce}`, { signal: controller.signal });
  } catch {
    return "down";
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 503) return "down";
  const token = deps.readToken(port);
  if (token === undefined) return "no-token";
  if (response.status === 403 || response.status === 421) return "unauthorized";
  let body: { proof?: unknown; bound?: unknown; pid?: unknown };
  try {
    body = JSON.parse(response.body) as typeof body;
  } catch {
    return "preauth";
  }
  if (response.status !== 200 || body.proof === undefined) return "preauth";
  if (!sameHex(body.proof, healthProof(token, nonce))) return "unauthorized";
  if (expect) {
    if (body.pid !== expect.pid) return "other-backend";
    if (!sameHex(body.bound, healthBound(token, nonce, expect.pid, expect.workspace))) {
      return "other-backend";
    }
  }
  return "authed";
}
```

- [ ] **Step 5: Run to verify pass, then commit**

Run: `npm run -w crucible-vscode-extension test -- test/backend-token.test.ts test/probe-health.test.ts`
Expected: PASS.

```bash
git add apps/vscode-extension/src/runtime/backend-token.ts apps/vscode-extension/src/runtime/probe-health.ts apps/vscode-extension/test/backend-token.test.ts apps/vscode-extension/test/probe-health.test.ts
git commit -m "feat(extension): backend token reader and health-proof probe"
```

### Task 9: Verify-before-send gate, failure sink, client wiring

**Files:**
- Create: `src/backend-auth/backend-gate.ts`, `src/backend-auth/auth-status-sink.ts`
- Modify: `src/extension.ts` (client factory ~line 421, `GraphPanel` construction ~587, health check ~784), `src/graph-panel.ts` (constructor + `buildIndex`), `src/settings.ts` (`checkBackendHealth`), `src/setup-panel.ts:67` (fallback URL), `src/runtime/vscode-runtime.ts` (`setAuthError`, `backendUrl`)
- Test: `test/backend-gate.test.ts`, `test/auth-status-sink.test.ts`

**Interfaces:**
- Consumes: `probeHealth`, `ProbeResult`, `readBackendToken` (Task 8); `BackendAuthError` (Task 7).
- Produces:
  - `normalizeBackendUrl(url: string): { base: string; port: number; isLoopback: boolean }` — rewrites a `localhost` host to `127.0.0.1`, strips a trailing slash; `isLoopback` is `true` only for `127.0.0.1`.
  - `interface GateDeps { fetch: (input: string, init?: RequestInit) => Promise<Response>; probe: (port: number) => Promise<ProbeResult>; readToken: (port: number) => string | undefined; report: (base: string, ok: boolean, reason?: string) => void; now: () => number }`
  - `class BackendGate { constructor(deps: GateDeps); fetchFor(base: string): (input: string, init?: RequestInit) => Promise<Response>; tokenFor(base: string): () => string | undefined; authedFetchFor(base: string): (input: string, init?: RequestInit) => Promise<Response> }`
  - `REPROBE_INTERVAL_MS = 5000`
  - `class AuthStatusSink { constructor(ui: { setError(reason: string | null): void; notify(base: string, reason: string): void }); report(base: string, ok: boolean, reason?: string): void }`
  - `RuntimeManager.setAuthError(reason: string | null): void`

Gate rules (spec §3.5 "Verify before sending"):
- A base starts unverified. `fetchFor(base)` probes first (shared in-flight probe; at most one probe per base per `REPROBE_INTERVAL_MS` while the last result was not `authed`).
- `authed` → forward the request. Any other result → never forward. `down` → throw `TypeError("fetch failed: backend at <base> is not reachable")` and report nothing (a down backend is not an auth problem). `unauthorized` / `preauth` / `no-token` / `other-backend` → `report(base, false, reason)` and throw `BackendAuthError(reason, null)`.
- A forwarded request that rejects (connection error) marks the base unverified again.
- A non-loopback base never gets a token: `tokenFor` returns `undefined` and `fetchFor` throws `BackendAuthError("the backend must be at 127.0.0.1", null)` after reporting.

- [ ] **Step 1: Write the failing tests**

`test/backend-gate.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { BackendAuthError } from "@crucible/editor-client";
import { BackendGate, normalizeBackendUrl, type GateDeps } from "../src/backend-auth/backend-gate.js";
import type { ProbeResult } from "../src/runtime/probe-health.js";

function gate(results: ProbeResult[], over: Partial<GateDeps> = {}) {
  let t = 0;
  const probe = vi.fn(async () => results.shift() ?? "authed");
  const fetch = vi.fn(async () => new Response("{}", { status: 200 }));
  const report = vi.fn();
  const g = new BackendGate({ fetch, probe, readToken: () => "T", report,
    now: () => t, ...over });
  return { g, probe, fetch, report, advance: (ms: number) => { t += ms; } };
}

describe("normalizeBackendUrl", () => {
  it("rewrites localhost before probing", () => {
    expect(normalizeBackendUrl("http://localhost:8000/")).toEqual(
      { base: "http://127.0.0.1:8000", port: 8000, isLoopback: true });
    expect(normalizeBackendUrl("http://example.com:8000").isLoopback).toBe(false);
  });
});

describe("BackendGate", () => {
  it("probes before the first request and forwards once authed", async () => {
    const { g, probe, fetch } = gate(["authed"]);
    await g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/config");
    await g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/config");
    expect(probe).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each(["unauthorized", "preauth", "no-token", "other-backend"] as const)(
    "%s: sends nothing, reports, throws BackendAuthError", async (result) => {
      const { g, fetch, report } = gate([result]);
      await expect(g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/x"))
        .rejects.toBeInstanceOf(BackendAuthError);
      expect(fetch).not.toHaveBeenCalled();
      expect(report).toHaveBeenCalledWith("http://127.0.0.1:9", false, expect.any(String));
    });

  it("down: sends nothing and reports nothing", async () => {
    const { g, fetch, report } = gate(["down"]);
    await expect(g.fetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/x")).rejects.toThrow(TypeError);
    expect(fetch).not.toHaveBeenCalled();
    expect(report).not.toHaveBeenCalled();
  });

  it("re-probes after a connection error, rate-limited", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    const { g, probe, advance } = gate(["authed", "down", "down", "authed"], { fetch });
    const f = g.fetchFor("http://127.0.0.1:9");
    await f("http://127.0.0.1:9/a");
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // connection error
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // probe → down
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // within 5 s: no probe
    expect(probe).toHaveBeenCalledTimes(2);
    advance(5001);
    await expect(f("http://127.0.0.1:9/a")).rejects.toThrow(); // probe → down
    advance(5001);
    await f("http://127.0.0.1:9/a");                           // probe → authed
    expect(probe).toHaveBeenCalledTimes(4);
  });

  it("shares one in-flight probe between concurrent requests", async () => {
    const { g, probe } = gate(["authed"]);
    const f = g.fetchFor("http://127.0.0.1:9");
    await Promise.all([f("http://127.0.0.1:9/a"), f("http://127.0.0.1:9/b")]);
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("never offers a token for a non-loopback base", async () => {
    const { g, fetch } = gate(["authed"]);
    expect(g.tokenFor("http://example.com:9")()).toBeUndefined();
    await expect(g.fetchFor("http://example.com:9")("http://example.com:9/x"))
      .rejects.toBeInstanceOf(BackendAuthError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("authedFetchFor attaches the header after verifying", async () => {
    const { g, fetch } = gate(["authed"]);
    await g.authedFetchFor("http://127.0.0.1:9")("http://127.0.0.1:9/v1/index/build", { method: "POST" });
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer T");
    expect(init.redirect).toBe("manual");
  });
});
```

`test/auth-status-sink.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { AuthStatusSink } from "../src/backend-auth/auth-status-sink.js";

describe("AuthStatusSink", () => {
  it("sets the error, notifies once per base, clears on the next ok", () => {
    const ui = { setError: vi.fn(), notify: vi.fn() };
    const sink = new AuthStatusSink(ui);
    sink.report("http://127.0.0.1:9", false, "backend refused the request (401)");
    sink.report("http://127.0.0.1:9", false, "backend refused the request (401)");
    expect(ui.notify).toHaveBeenCalledTimes(1);
    expect(ui.setError).toHaveBeenLastCalledWith("backend refused the request (401)");
    sink.report("http://127.0.0.1:9", true);
    expect(ui.setError).toHaveBeenLastCalledWith(null);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/backend-gate.test.ts test/auth-status-sink.test.ts`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `src/backend-auth/backend-gate.ts`**

```ts
// vscode-free. No request — and no token — goes to a backend URL until /health has
// proven the server there holds the token (spec §3.5 "Verify before sending").
import { BackendAuthError } from "@crucible/editor-client";
import type { ProbeResult } from "../runtime/probe-health.js";

export const REPROBE_INTERVAL_MS = 5000;

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface GateDeps {
  fetch: FetchLike;
  probe: (port: number) => Promise<ProbeResult>;
  readToken: (port: number) => string | undefined;
  report: (base: string, ok: boolean, reason?: string) => void;
  now: () => number;
}

export function normalizeBackendUrl(url: string): { base: string; port: number; isLoopback: boolean } {
  const parsed = new URL(url.trim());
  if (parsed.hostname === "localhost") parsed.hostname = "127.0.0.1";
  const port = parsed.port ? Number(parsed.port) : parsed.protocol === "https:" ? 443 : 80;
  const base = `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, "");
  return { base, port, isLoopback: parsed.hostname === "127.0.0.1" };
}

interface BaseState {
  verified: boolean;
  lastProbeAt: number;
  lastResult: ProbeResult | null;
  inFlight: Promise<ProbeResult> | null;
}

const REASONS: Record<Exclude<ProbeResult, "authed" | "down">, string> = {
  unauthorized: "the backend did not prove it holds this window's token",
  preauth: "the backend answered without a token proof (an old or foreign server)",
  "no-token": "no readable token file for this backend's port",
  "other-backend": "a different backend now holds this port",
};

export class BackendGate {
  private readonly states = new Map<string, BaseState>();

  constructor(private readonly deps: GateDeps) {}

  tokenFor(base: string): () => string | undefined {
    const { port, isLoopback } = normalizeBackendUrl(base);
    return () => (isLoopback ? this.deps.readToken(port) : undefined);
  }

  fetchFor(base: string): FetchLike {
    const target = normalizeBackendUrl(base);
    return async (input, init) => {
      if (!target.isLoopback) {
        const reason = "the backend must be at 127.0.0.1";
        this.deps.report(target.base, false, reason);
        throw new BackendAuthError(reason, null);
      }
      const result = await this.verify(target.base, target.port);
      if (result === "down") {
        throw new TypeError(`fetch failed: backend at ${target.base} is not reachable`);
      }
      if (result !== "authed") {
        this.deps.report(target.base, false, REASONS[result]);
        throw new BackendAuthError(REASONS[result], null);
      }
      try {
        return await this.deps.fetch(input, init);
      } catch (err) {
        // A respawn may hand the port to someone else: forget the old verdict so the
        // next request probes again instead of reusing a cached "authed".
        const st = this.state(target.base);
        st.verified = false;
        st.lastResult = null;
        throw err;
      }
    };
  }

  authedFetchFor(base: string): FetchLike {
    const fetchVerified = this.fetchFor(base);
    const token = this.tokenFor(base);
    return (input, init) => {
      const headers = { ...((init?.headers as Record<string, string> | undefined) ?? {}) };
      const value = token();
      if (value) headers.authorization = `Bearer ${value}`;
      return fetchVerified(input, { ...init, headers, redirect: "manual" });
    };
  }

  private state(base: string): BaseState {
    let st = this.states.get(base);
    if (!st) {
      st = { verified: false, lastProbeAt: -Infinity, lastResult: null, inFlight: null };
      this.states.set(base, st);
    }
    return st;
  }

  private async verify(base: string, port: number): Promise<ProbeResult> {
    const st = this.state(base);
    if (st.verified) return "authed";
    if (st.inFlight) return st.inFlight;
    if (st.lastResult !== null && this.deps.now() - st.lastProbeAt < REPROBE_INTERVAL_MS) {
      return st.lastResult;
    }
    st.lastProbeAt = this.deps.now();
    st.inFlight = this.deps.probe(port).finally(() => { st.inFlight = null; });
    const result = await st.inFlight;
    st.lastResult = result;
    st.verified = result === "authed";
    return result;
  }
}
```

The repeated `fetch failed` rejection inside the 5 s window must not reach `report` — only auth results do (test `down: … reports nothing`).

- [ ] **Step 4: Implement `src/backend-auth/auth-status-sink.ts`**

```ts
// vscode-free. The one sink every client reports auth status to (spec §3.8).
export class AuthStatusSink {
  private readonly notified = new Set<string>();

  constructor(private readonly ui: {
    setError(reason: string | null): void;
    notify(base: string, reason: string): void;
  }) {}

  report(base: string, ok: boolean, reason?: string): void {
    if (ok) {
      this.ui.setError(null);
      return;
    }
    const why = reason ?? "not authorized";
    this.ui.setError(why);
    if (!this.notified.has(base)) {
      this.notified.add(base);
      this.ui.notify(base, why);
    }
  }
}
```

- [ ] **Step 5: Wire `extension.ts`, `graph-panel.ts`, `settings.ts`, `setup-panel.ts`, `vscode-runtime.ts`**

`vscode-runtime.ts`:
- `backendUrl()` returns `` `http://127.0.0.1:${port}` ``.
- Add a second status bar item for auth errors:

```ts
  private readonly authStatus: vscode.StatusBarItem;
  // in the constructor, after this.statusBar:
  this.authStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 49);
  this.authStatus.text = "$(error) Crucible: not authorized";
  this.authStatus.command = "crucible.openSettingsPanel";
  context.subscriptions.push(this.authStatus);

  setAuthError(reason: string | null): void {
    if (reason === null) {
      this.authStatus.hide();
      return;
    }
    this.authStatus.tooltip = reason;
    this.authStatus.show();
  }
```

`extension.ts`, replacing `const clientFactory: BackendClientFactory = (baseUrl) => new HttpBackendClient({ baseUrl });`:

```ts
  // Spec §3.5/§3.8: one gate per window. No request leaves before /health proves the
  // server holds the token, and every client reports auth status to one sink.
  const authSink = new AuthStatusSink({
    setError: (reason) => runtimeManager.setAuthError(reason),
    notify: (base, reason) => {
      void vscode.window.showErrorMessage(
        `Crucible: the backend at ${base} was not authorized (${reason}).`, "Open logs")
        .then((choice: string | undefined) => { if (choice === "Open logs") output.show(); });
    },
  });
  const probeDeps = {
    fetchRaw: async (url: string, init: { signal: AbortSignal }) => {
      const res = await fetch(url, { signal: init.signal, redirect: "manual" });
      return { status: res.status, body: await res.text() };
    },
    readToken: (port: number) => readBackendToken(port),
  };
  const backendGate = new BackendGate({
    fetch: (input, init) => fetch(input, init),
    probe: (port) => probeHealth(port, probeDeps),
    readToken: (port) => readBackendToken(port),
    report: (base, ok, reason) => authSink.report(base, ok, reason),
    now: () => Date.now(),
  });
  const clientFactory: BackendClientFactory = (baseUrl) => {
    const { base } = normalizeBackendUrl(baseUrl);
    return new HttpBackendClient({
      baseUrl: base,
      fetchFn: backendGate.fetchFor(base),
      authToken: backendGate.tokenFor(base),
      onAuthStatus: (ok, reason) => authSink.report(base, ok, reason),
    });
  };
```

(`output` is the extension's existing `OutputChannel` variable — use whatever name `activate` already gives it; `grep -n "createOutputChannel" src/extension.ts`.) Add the imports at the top: `BackendGate`, `normalizeBackendUrl` from `./backend-auth/backend-gate.js`; `AuthStatusSink` from `./backend-auth/auth-status-sink.js`; `probeHealth` from `./runtime/probe-health.js`; `readBackendToken` from `./runtime/backend-token.js`.

`checkBackendHealth` (`settings.ts`) becomes:

```ts
export async function checkBackendHealth(
  baseUrl: string, probe: (port: number) => Promise<ProbeResult>,
): Promise<ProbeResult> {
  return probe(normalizeBackendUrl(baseUrl).port);
}
```

and its caller in `extension.ts` (~784):

```ts
  const health = managedBackendStarted
    ? "authed"
    : await checkBackendHealth(backendBaseUrl, (port) => probeHealth(port, probeDeps));
  if (health !== "authed") {
    if (health !== "down") authSink.report(normalizeBackendUrl(backendBaseUrl).base, false, health);
    void vscode.window.showWarningMessage(/* the existing two messages, unchanged */);
  }
```

`GraphPanel`: replace the constructor's `backendBaseUrl: string` with `private readonly resolveFetch: () => { base: string; fetch: (input: string, init?: RequestInit) => Promise<Response> }` and make `buildIndex`:

```ts
      buildIndex: async () => {
        const { base, fetch: authedFetch } = this.resolveFetch();
        // The route 422s without a JSON body — workspace_path is required.
        const res = await authedFetch(`${base}/v1/index/build`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace_path: this.workspacePath }),
        });
        if (!res.ok) throw new Error(`index build failed (${res.status})`);
```

(keep the lines after it — the watcher re-arm — as they are). In `extension.ts` construct it with
`new GraphPanel(context.extensionUri, ws, () => { const { base } = normalizeBackendUrl(settings.getBackendBaseUrl()); return { base, fetch: backendGate.authedFetchFor(base) }; }).open();`

`setup-panel.ts:67`: the fallback becomes `` `http://127.0.0.1:${port}` ``.

- [ ] **Step 6: Run tests and typecheck**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS. If the vscode shim (`src/vscode-shim.d.ts`) lacks `StatusBarItem.tooltip`, add it there.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/src apps/vscode-extension/test
git commit -m "feat(extension): verify the backend before sending a token; one auth-status sink"
```

### Task 10: Spawn via `agentd.serve`, stdout handshake, `127.0.0.1`, token on internal calls

**Files:**
- Modify: `src/runtime/backend-process.ts` (interfaces, `buildBackendEnv`, `start`, `spawnWatcher`, `healthy`)
- Modify: `src/runtime/vscode-runtime.ts` (`processDeps()`; delete `pickFreePort` and its `createServer` import)
- Modify: `test/runtime-backend-process.test.ts`
- Test: `test/stdout-lines.test.ts`

**Interfaces:**
- Consumes: `probeHealth`, `ProbeDeps` (Task 8); `readBackendToken` (Task 8).
- Produces (all exported from `backend-process.ts`):
  - `interface ChildHandle { pid: number; kill(signal?: NodeJS.Signals): void; onExit(cb: (code: number | null) => void): void; onStdoutLine(cb: (line: string) => void): void; exited: Promise<number | null> }`
  - `interface ExecOutcome { code: number | null; stdout: string; stderr: string; timedOut: boolean }`
  - `interface ProcessInfo { uid: number; command: string; startedAtSec: number }`
  - `ProcessDeps` — removes `pickPort`; adds `fetchRaw(url: string, init: { signal: AbortSignal }): Promise<{ status: number; body: string }>`, `readToken(port: number): string | undefined`, `processInfo(pid: number): Promise<ProcessInfo | null>`, `signal(pid: number, sig: NodeJS.Signals): void`, `exec(cmd: string, args: string[], timeoutMs: number): Promise<ExecOutcome>`, `now(): number` (ms), `uid?: number`. `fetchJson` now attaches the bearer token for the URL's port.
  - `class StdoutLines { push(chunk: string): void; subscribe(cb: (line: string) => void): void }` — splits on `\n`, buffers complete lines until the first subscriber, then replays them.
  - `parseHandshake(line: string): { pid: number; port: number } | null`
  - `finalSpawnEnv(...parts: Array<Record<string, string | undefined>>): Record<string, string>` — merges left to right, drops `undefined`, **removes `CRUCIBLE_AUTH_DISABLED`**.
  - `buildBackendEnv(workspace, settings, runtimeDir, platform?)` — `port` parameter and `CRUCIBLE_PORT` removed.
  - `class RuntimeUpdateRequiredError extends Error { readonly component: "agentd" | "indexer" | "backend" }`
  - `HANDSHAKE_TIMEOUT_MS = 10_000`

- [ ] **Step 1: Write the failing tests**

`test/stdout-lines.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseHandshake, StdoutLines } from "../src/runtime/backend-process.js";

describe("StdoutLines", () => {
  it("joins a line split across chunks and buffers until subscribed", () => {
    const lines = new StdoutLines();
    lines.push("INFO: hello\nCRUCIBLE_SER");
    lines.push('VE {"pid": 5, "port": 9}\nmore');
    const seen: string[] = [];
    lines.subscribe((l) => seen.push(l));
    expect(seen).toEqual(["INFO: hello", 'CRUCIBLE_SERVE {"pid": 5, "port": 9}']);
    lines.push(" output\n");
    expect(seen.at(-1)).toBe("more output");
  });
});

describe("parseHandshake", () => {
  it("parses the line and rejects anything else", () => {
    expect(parseHandshake('CRUCIBLE_SERVE {"pid": 5, "port": 9}')).toEqual({ pid: 5, port: 9 });
    expect(parseHandshake('CRUCIBLE_SERVE {"pid": "5", "port": 9}')).toBeNull();
    expect(parseHandshake("INFO: Uvicorn running")).toBeNull();
  });
});
```

In `test/runtime-backend-process.test.ts`, replace the `deps()` helper and update the tests as follows (the watcher/LSP/jdtls tests keep their bodies; they only need the new helper):

```ts
import { healthBound, healthProof } from "../src/runtime/probe-health.js";
import { BackendProcess, buildBackendEnv, finalSpawnEnv, RuntimeUpdateRequiredError,
  type ChildHandle, type ProcessDeps } from "../src/runtime/backend-process.js";

const TOKEN = "k".repeat(43);

function stubChild(pid: number, handshake: { pid: number; port: number } | null): ChildHandle & { killed: string[] } {
  let resolveExit: (code: number | null) => void = () => {};
  const exited = new Promise<number | null>((r) => { resolveExit = r; });
  const killed: string[] = [];
  return {
    pid, killed, exited,
    kill: (sig) => { killed.push(sig ?? "SIGTERM"); resolveExit(0); },
    onExit: (cb) => { void exited.then(cb); },
    onStdoutLine: (cb) => {
      if (handshake) cb(`CRUCIBLE_SERVE ${JSON.stringify(handshake)}`);
    },
  };
}

function deps(overrides: Partial<ProcessDeps> = {}, child = stubChild(4242, { pid: 4242, port: 8123 })) {
  const spawned: { cmd: string; args: string[]; env: Record<string, string>; cwd?: string }[] = [];
  const d: ProcessDeps & { spawned: typeof spawned } = {
    runtimeDir: mkdtempSync(join(tmpdir(), "rt-")),
    spawn: (cmd, args, opts) => {
      spawned.push({ cmd, args, env: opts.env, cwd: opts.cwd });
      return spawned.length === 1 ? child : stubChild(5000 + spawned.length, null);
    },
    fetchJson: async () => ({ status: "ok", building: false }),
    // A /health that proves the token for whichever pid/workspace the caller expects.
    fetchRaw: async (url) => {
      const nonce = new URL(url).searchParams.get("nonce") ?? "";
      return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
        proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
    },
    readToken: () => TOKEN,
    processInfo: async () => null,
    signal: () => {},
    exec: async () => ({ code: 0, stdout: "0.1.0 auth=1", stderr: "", timedOut: false }),
    now: () => Date.now(),
    sleep: async () => {},
    isPidAlive: () => false,
    log: () => {},
    platform: "darwin-arm64",
    uid: 501,
    spawned,
    ...overrides,
  };
  return d;
}

let currentWs = "";
function ws(): string {
  currentWs = mkdtempSync(join(tmpdir(), "ws-"));
  return currentWs;
}
```

Change the existing tests:
- `buildBackendEnv(...)` calls drop the port argument (`buildBackendEnv("/ws", SETTINGS, "/rt", "darwin-arm64")`), and the first test asserts `expect(env.CRUCIBLE_PORT).toBeUndefined()` instead of `"8123"`.
- `"reaps a stale lock and spawns backend + watcher"` → rename to `"spawns agentd.serve, takes the port from the handshake, starts the watcher"` and assert:

```ts
    const d = deps();
    const w = ws();
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");  // keep the file the old test wrote
    const r = await new BackendProcess(d).start(w, SETTINGS);
    expect(r).toEqual({ port: 8123, reused: false });
    expect(d.spawned[0].args).toEqual(["-m", "agentd.serve", "--port", "0", "--workspace-lock", w]);
    expect(d.spawned[1].env.CRUCIBLE_BACKEND_URL).toBe("http://127.0.0.1:8123");
```

  (keep the old test's directory setup for `bin/` — copy whatever `mkdirSync` it does.)
- `"throws when health never comes up"`: `fetchRaw: async () => { throw new TypeError("fetch failed"); }` and `expect(child.killed).toContain("SIGTERM")`.
- Delete `"reuses a live locked backend without spawning"` here; Task 11 rewrites it.

New tests in the same file:

```ts
describe("spawn handshake", () => {
  it("fails fast when the child exits before printing the handshake", async () => {
    const child = stubChild(4242, null);
    const d = deps({}, child);
    // Exited before start() subscribes: the stub's instant sleep would otherwise
    // fire the 10 s handshake timeout first.
    child.kill("SIGTERM");
    await expect(new BackendProcess(d).start(ws(), SETTINGS)).rejects.toThrow(/exited/);
  });

  it("strips CRUCIBLE_AUTH_DISABLED from the env actually spawned, even from extraEnv", async () => {
    process.env.CRUCIBLE_AUTH_DISABLED = "1";
    try {
      const d = deps();
      await new BackendProcess(d).start(ws(), { ...SETTINGS,
        extraEnv: { CRUCIBLE_AUTH_DISABLED: "1" } });
      for (const s of d.spawned) expect(s.env.CRUCIBLE_AUTH_DISABLED).toBeUndefined();
    } finally {
      delete process.env.CRUCIBLE_AUTH_DISABLED;
    }
  });

  it("a backend it just spawned that answers without a proof needs a runtime update", async () => {
    const d = deps({ fetchRaw: async () => ({ status: 200, body: '{"status":"ok"}' }) });
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toBeInstanceOf(RuntimeUpdateRequiredError);
  });
});

describe("finalSpawnEnv", () => {
  it("drops undefined and CRUCIBLE_AUTH_DISABLED", () => {
    expect(finalSpawnEnv({ A: "1", B: undefined }, { CRUCIBLE_AUTH_DISABLED: "1", C: "3" }))
      .toEqual({ A: "1", C: "3" });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/stdout-lines.test.ts test/runtime-backend-process.test.ts`
Expected: FAIL (missing exports).

- [ ] **Step 3: Implement in `backend-process.ts`**

Interfaces as listed above, plus:

```ts
const HEALTH_ATTEMPTS = 60;
export const HANDSHAKE_TIMEOUT_MS = 10_000;
const HANDSHAKE_RE = /^CRUCIBLE_SERVE (\{.*\})\s*$/;

export class RuntimeUpdateRequiredError extends Error {
  constructor(readonly component: "agentd" | "indexer" | "backend", detail: string) {
    super(`Crucible runtime update required (${component}): ${detail}`);
    this.name = "RuntimeUpdateRequiredError";
  }
}

export class StdoutLines {
  private partial = "";
  private readonly buffered: string[] = [];
  private subscriber: ((line: string) => void) | null = null;

  push(chunk: string): void {
    const parts = (this.partial + chunk).split("\n");
    this.partial = parts.pop() ?? "";
    for (const line of parts) this.emit(line.replace(/\r$/, ""));
  }

  subscribe(cb: (line: string) => void): void {
    this.subscriber = cb;
    for (const line of this.buffered.splice(0)) cb(line);
  }

  private emit(line: string): void {
    if (this.subscriber) this.subscriber(line);
    else this.buffered.push(line);
  }
}

export function parseHandshake(line: string): { pid: number; port: number } | null {
  const match = HANDSHAKE_RE.exec(line);
  if (!match) return null;
  try {
    const raw = JSON.parse(match[1]) as { pid?: unknown; port?: unknown };
    return Number.isInteger(raw.pid) && Number.isInteger(raw.port)
      ? { pid: raw.pid as number, port: raw.port as number }
      : null;
  } catch {
    return null;
  }
}

export function finalSpawnEnv(
  ...parts: Array<Record<string, string | undefined>>
): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const part of parts) {
    for (const [k, v] of Object.entries(part)) if (v !== undefined) merged[k] = v;
  }
  // Spec §3.6: a managed spawn never runs with the token check off, whoever set it.
  delete merged.CRUCIBLE_AUTH_DISABLED;
  return merged;
}
```

In `buildBackendEnv`: delete the `port` parameter and the `CRUCIBLE_PORT` line.

In `BackendProcess`: add `private lastChildPid: number | undefined;` and

```ts
  private probeDeps(): ProbeDeps {
    return { fetchRaw: (url, init) => this.deps.fetchRaw(url, init),
             readToken: (port) => this.deps.readToken(port) };
  }

  private waitForHandshake(child: ChildHandle): Promise<{ pid: number; port: number }> {
    return new Promise((resolve, reject) => {
      let settled = false;
      const settle = (fn: () => void) => { if (!settled) { settled = true; fn(); } };
      child.onStdoutLine((line) => {
        const hs = parseHandshake(line);
        if (hs) settle(() => resolve(hs));
      });
      void child.exited.then((code) => settle(() => reject(new Error(
        `backend exited (code=${code}) before it started — see the Crucible output`))));
      void this.deps.sleep(HANDSHAKE_TIMEOUT_MS).then(() => settle(() => reject(new Error(
        "backend printed no CRUCIBLE_SERVE line within 10s — see the Crucible output"))));
    });
  }
```

Replace step 2–3 of `start()` (spawn + health poll) with:

```ts
    // 2. Spawn agentd.serve on any free port; it binds first, then reports the port.
    const env = finalSpawnEnv(
      process.env as Record<string, string | undefined>,
      buildBackendEnv(workspace, settings, this.deps.runtimeDir, this.platform),
    );
    const child = this.deps.spawn(
      venvPython(this.deps.runtimeDir, this.platform),
      ["-m", "agentd.serve", "--port", "0", "--workspace-lock", workspace],
      { env, cwd: workspace },
    );
    this.backend = child;
    this.lastChildPid = child.pid;
    let handshake: { pid: number; port: number };
    try {
      handshake = await this.waitForHandshake(child);
    } catch (err) {
      await this.stop();
      throw err;
    }
    const port = handshake.port;
    this._port = port;

    // 3. Health: the proof must name this child's pid and this workspace.
    let up = false;
    for (let i = 0; i < HEALTH_ATTEMPTS; i++) {
      const result = await probeHealth(port, this.probeDeps(), { pid: handshake.pid, workspace });
      if (result === "authed") { up = true; break; }
      if (result === "preauth") {
        await this.stop();
        throw new RuntimeUpdateRequiredError("backend", "the backend answered without a token proof");
      }
      await this.deps.sleep(1000);
    }
    if (!up) {
      await this.stop();
      throw new Error("backend did not become healthy within 60s — see logs");
    }
```

The index pre-warm URLs become `` `http://127.0.0.1:${port}/v1/index/...` ``. The reuse block at the top of `start()` changes only this much for now (Task 11 replaces it): `this.deps.isPidAlive(lock.pid) && await this.healthy(lock.port)` → `(await probeHealth(lock.port, this.probeDeps(), { pid: lock.pid, workspace })) === "authed"`. Delete `healthy()`.

`spawnWatcher`: `const env = finalSpawnEnv(process.env as Record<string, string | undefined>, { CRUCIBLE_BACKEND_URL: `http://127.0.0.1:${port}`, … })` (all the existing keys move into the second object unchanged).

`stop()` for now: `this.backend?.kill("SIGTERM")` (Task 12 adds the wait).

- [ ] **Step 4: Implement the real deps in `vscode-runtime.ts::processDeps()`**

```ts
      spawn: (cmd, args, opts) => {
        const child = spawn(cmd, args, { env: opts.env, cwd: opts.cwd, stdio: ["ignore", "pipe", "pipe"] });
        const lines = new StdoutLines();
        const exited = new Promise<number | null>((resolve) => {
          child.once("exit", (code) => resolve(code));
          child.once("error", () => resolve(null)); // spawn failure (ENOENT…)
        });
        child.stdout?.on("data", (chunk: Buffer) => {
          const text = chunk.toString();
          this.output.append(text);
          lines.push(text);
        });
        child.stderr?.on("data", (chunk: Buffer) => this.output.append(chunk.toString()));
        return {
          pid: child.pid ?? -1,
          kill: (sig) => { child.kill(sig); },
          onExit: (cb) => { void exited.then(cb); },
          onStdoutLine: (cb) => lines.subscribe(cb),
          exited,
        };
      },
      fetchJson: async (url, init) => {
        const token = readBackendToken(Number(new URL(url).port));
        const headers: Record<string, string> = {};
        if (init?.body) headers["content-type"] = "application/json";
        if (token) headers.authorization = `Bearer ${token}`;
        const res = await fetch(url, { ...(init?.method ? { method: init.method } : {}),
          ...(init?.body ? { body: init.body } : {}), headers, redirect: "manual" });
        if (!res.ok) throw new Error(`request failed (${res.status}) for ${url}`);
        return res.json();
      },
      fetchRaw: async (url, init) => {
        const res = await fetch(url, { signal: init.signal, redirect: "manual" });
        return { status: res.status, body: await res.text() };
      },
      readToken: (port) => readBackendToken(port),
      processInfo: (pid) => readProcessInfo(pid, (cmd, args, ms) => execWithTimeout(cmd, args, ms)),
      signal: (pid, sig) => { try { process.kill(pid, sig); } catch { /* gone */ } },
      exec: (cmd, args, timeoutMs) => execWithTimeout(cmd, args, timeoutMs),
      now: () => Date.now(),
      uid: process.getuid?.(),
```

and a module-level helper:

```ts
function execWithTimeout(cmd: string, args: string[], timeoutMs: number): Promise<ExecOutcome> {
  return new Promise((resolve) => {
    // execFile's `timeout` sends SIGTERM at expiry; killSignal makes it SIGKILL, since an
    // old indexer treats --version as "start watching" and may ignore SIGTERM.
    execFile(cmd, args, { timeout: timeoutMs, killSignal: "SIGKILL", maxBuffer: 1024 * 1024 },
      (err, stdout, stderr) => {
        const e = err as (NodeJS.ErrnoException & { killed?: boolean; code?: number | string }) | null;
        resolve({
          code: e ? (typeof e.code === "number" ? e.code : null) : 0,
          stdout: String(stdout ?? ""),
          stderr: String(stderr ?? ""),
          timedOut: Boolean(e?.killed),
        });
      });
  });
}
```

`readProcessInfo` is written in Task 11; until then use `processInfo: async () => null`. Delete `pickFreePort`, the `pickPort:` entry and the `createServer` import. Imports: `StdoutLines`, `type ExecOutcome` from `./backend-process.js`; `readBackendToken` from `./backend-token.js`.

- [ ] **Step 5: Run tests and typecheck**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/runtime apps/vscode-extension/test
git commit -m "feat(extension): spawn agentd.serve, port from the stdout handshake, 127.0.0.1 only"
```

### Task 11: Reuse only the right backend; reap only a verified process

**Files:**
- Modify: `src/runtime/backend-process.ts` (lock reading, reuse, reap)
- Create: `src/runtime/process-info.ts`
- Modify: `src/runtime/vscode-runtime.ts` (`processInfo:` uses `readProcessInfo`)
- Test: `test/runtime-backend-process.test.ts`, `test/process-info.test.ts`

**Interfaces:**
- Consumes: `ProcessDeps`, `probeHealth` (Tasks 8, 10).
- Produces:
  - `readOwnedLock(workspace: string, uid: number | undefined): LockInfo | null` — `O_NOFOLLOW` open + `fstat`; a lock not owned by `uid` or with `nlink !== 1` (or unparsable) is unlinked and `null` is returned.
  - `isOurBackend(info: ProcessInfo, lock: LockInfo, workspace: string, uid: number | undefined): boolean`
  - `process-info.ts`: `parseEtime(etime: string): number | null` (seconds), `parsePsLine(line: string, nowSec: number): ProcessInfo | null`, `readProcessInfo(pid: number, exec: (cmd: string, args: string[], timeoutMs: number) => Promise<ExecOutcome>): Promise<ProcessInfo | null>`
  - `LOCK_YOUNG_SEC = 60`, `REAP_GRACE_MS = 5000`

Rules (spec §3.7):
- Reuse only when `probeHealth(lock.port, deps, { pid: lock.pid, workspace })` is `authed`.
- `down` + lock younger than 60 s + lock pid alive + lock pid is not this process's own previous child → wait up to the lock's 60 s mark, re-probing every second, once per `start()`.
- Otherwise reap: unlink the lock; signal only when `isOurBackend(...)` holds (never on Windows, never pid ≤ 1): SIGTERM, then SIGKILL if still alive after 5 s.
- `isOurBackend`: uid matches; `startedAtSec <= Math.floor(lock.started_at) + 1`; and either the command contains `agentd.serve` and ends with `` ` --workspace-lock ${workspace}` ``, or (migration) it contains `agentd.main:app` and `--port <lock.port>` as whole tokens.

`readProcessInfo` uses `LC_ALL=C ps -ww -o uid=,etime=,command= -p <pid>` on every POSIX platform (on Linux `ps -ww` prints the full command line too, so the spec's `/proc` path is unnecessary); it returns `null` on Windows, on a non-zero exit, or on unparsable output. It must pass `LC_ALL=C` via `env`: give `ExecOutcome`'s exec a wrapper that runs `/usr/bin/env` with args `["LC_ALL=C", "ps", "-ww", "-o", "uid=,etime=,command=", "-p", String(pid)]`.

- [ ] **Step 1: Write the failing tests**

`test/process-info.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseEtime, parsePsLine } from "../src/runtime/process-info.js";

describe("ps parsing", () => {
  it.each([["05", null], ["01:05", 65], ["02:01:05", 7265], ["3-02:01:05", 266465]])(
    "etime %s", (raw, sec) => expect(parseEtime(raw)).toBe(sec));
  it("parses uid, etime and a command with spaces", () => {
    expect(parsePsLine("  501   01:05 /v/python -m agentd.serve --port 0 --workspace-lock /a/proj 2", 1000))
      .toEqual({ uid: 501, startedAtSec: 935,
        command: "/v/python -m agentd.serve --port 0 --workspace-lock /a/proj 2" });
    expect(parsePsLine("garbage", 1000)).toBeNull();
  });
});
```

Added to `test/runtime-backend-process.test.ts`:

```ts
import { isOurBackend, readOwnedLock } from "../src/runtime/backend-process.js";
import { linkSync } from "node:fs";

function writeLock(w: string, lock: { pid: number; port: number; started_at: number }): string {
  mkdirSync(join(w, ".crucible/state"), { recursive: true });
  const p = join(w, ".crucible/state", "agentd.lock");
  writeFileSync(p, JSON.stringify(lock));
  return p;
}

describe("reuse", () => {
  it("reuses the backend the lock names, for this workspace", async () => {
    const w = ws();
    writeLock(w, { pid: 4242, port: 9001, started_at: Date.now() / 1000 - 600 });
    const d = deps();
    expect(await new BackendProcess(d).start(w, SETTINGS)).toEqual({ port: 9001, reused: true });
    expect(d.spawned).toHaveLength(0);
  });

  it("does not reuse an authed backend for another workspace", async () => {
    const w = ws();
    writeLock(w, { pid: 4242, port: 9001, started_at: Date.now() / 1000 - 600 });
    const d = deps({
      fetchRaw: async (url) => {
        const nonce = new URL(url).searchParams.get("nonce") ?? "";
        // Port 9001 is held by another workspace's backend; the new spawn (8123) is ours.
        const owner = url.includes(":9001/") ? "/some/other/ws" : currentWs;
        return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
          proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, owner) }) };
      },
    });
    const r = await new BackendProcess(d).start(w, SETTINGS);
    expect(r.reused).toBe(false);
    expect(d.spawned[0].args).toContain("agentd.serve");
  });

  it("ignores and unlinks a lock with nlink > 1", async () => {
    const w = ws();
    const p = writeLock(w, { pid: 4242, port: 9001, started_at: Date.now() / 1000 });
    linkSync(p, join(w, "hardlink"));
    expect(readOwnedLock(w, process.getuid?.())).toBeNull();
    expect(existsSync(p)).toBe(false);
  });

  it("waits at most once for a young lock whose port is down", async () => {
    const w = ws();
    writeLock(w, { pid: 777, port: 9001, started_at: Date.now() / 1000 - 58 });
    let probes9001 = 0;
    const d = deps({
      isPidAlive: (pid) => pid === 777,
      fetchRaw: async (url) => {
        if (url.includes(":9001/")) { probes9001++; throw new TypeError("fetch failed"); }
        const nonce = new URL(url).searchParams.get("nonce") ?? "";
        return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
          proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
      },
    });
    await new BackendProcess(d).start(w, SETTINGS);
    expect(probes9001).toBeLessThanOrEqual(3); // initial + ≤2 s of re-probes, then spawn
  });

  it("does not wait when the lock's pid is dead", async () => {
    const w = ws();
    writeLock(w, { pid: 777, port: 9001, started_at: Date.now() / 1000 - 5 });
    let probes9001 = 0;
    const d = deps({ fetchRaw: async (url) => {
      if (url.includes(":9001/")) { probes9001++; throw new TypeError("fetch failed"); }
      const nonce = new URL(url).searchParams.get("nonce") ?? "";
      return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
        proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
    } });
    await new BackendProcess(d).start(w, SETTINGS);
    expect(probes9001).toBe(1);
  });
});

describe("reap", () => {
  const lock = { pid: 777, port: 8000, started_at: 1000 };
  const ours = (command: string, over: Partial<{ uid: number; startedAtSec: number }> = {}) =>
    ({ uid: 501, startedAtSec: 999, command, ...over });

  it("matches only this workspace's agentd.serve, started before the lock", () => {
    expect(isOurBackend(ours("py -m agentd.serve --port 0 --workspace-lock /a/proj"), lock, "/a/proj", 501)).toBe(true);
    expect(isOurBackend(ours("py -m agentd.serve --port 0 --workspace-lock /a/proj 2"), lock, "/a/proj", 501)).toBe(false);
    expect(isOurBackend(ours("py -m agentd.serve --port 0 --workspace-lock /a/proj", { uid: 0 }), lock, "/a/proj", 501)).toBe(false);
    expect(isOurBackend(ours("py -m agentd.serve --port 0 --workspace-lock /a/proj", { startedAtSec: 1005 }), lock, "/a/proj", 501)).toBe(false);
  });

  it("matches a legacy agentd.main:app backend on the lock's exact port", () => {
    expect(isOurBackend(ours("py -m uvicorn agentd.main:app --port 8000"), lock, "/a/proj", 501)).toBe(true);
    expect(isOurBackend(ours("py -m uvicorn agentd.main:app --port 80"), lock, "/a/proj", 501)).toBe(false);
    expect(isOurBackend(ours("py -m uvicorn agentd.main:app --port 80001"), lock, "/a/proj", 501)).toBe(false);
  });

  it("signals a verified stale backend, escalating to SIGKILL", async () => {
    const w = ws();
    writeLock(w, { pid: 777, port: 9001, started_at: Date.now() / 1000 - 600 });
    const signals: string[] = [];
    const d = deps({
      isPidAlive: (pid) => pid === 777,
      processInfo: async () => ({ uid: 501, startedAtSec: Date.now() / 1000 - 700,
        command: `py -m agentd.serve --port 0 --workspace-lock ${currentWs}` }),
      signal: (_pid, sig) => { signals.push(sig); },
      fetchRaw: async (url) => {
        if (url.includes(":9001/")) return { status: 200, body: '{"status":"ok"}' }; // preauth
        const nonce = new URL(url).searchParams.get("nonce") ?? "";
        return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
          proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
      },
    });
    await new BackendProcess(d).start(w, SETTINGS);
    expect(signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(d.spawned).not.toHaveLength(0);
  });

  it.each([
    ["a recycled pid", { uid: 501, startedAtSec: Date.now() / 1000 + 100, command: "py -m agentd.serve --workspace-lock X" }],
    ["another user's process", { uid: 0, startedAtSec: 0, command: "py -m agentd.serve --workspace-lock X" }],
    ["an unrelated process", { uid: 501, startedAtSec: 0, command: "/usr/bin/vim" }],
  ])("never signals %s, and still spawns", async (_name, info) => {
    const w = ws();
    writeLock(w, { pid: 777, port: 9001, started_at: Date.now() / 1000 - 600 });
    const signals: string[] = [];
    const d = deps({ processInfo: async () => info, signal: (_p, s) => { signals.push(s); },
      fetchRaw: async (url) => {
        if (url.includes(":9001/")) return { status: 421, body: "" };
        const nonce = new URL(url).searchParams.get("nonce") ?? "";
        return { status: 200, body: JSON.stringify({ status: "ok", pid: 4242,
          proof: healthProof(TOKEN, nonce), bound: healthBound(TOKEN, nonce, 4242, currentWs) }) };
      } });
    await new BackendProcess(d).start(w, SETTINGS);
    expect(signals).toEqual([]);
    expect(d.spawned).not.toHaveLength(0);
  });

  it("never signals pid 1", async () => {
    const w = ws();
    writeLock(w, { pid: 1, port: 9001, started_at: Date.now() / 1000 - 600 });
    const signals: string[] = [];
    const d = deps({ signal: (_p, s) => { signals.push(s); },
      processInfo: async () => ({ uid: 501, startedAtSec: 0,
        command: `x agentd.serve --workspace-lock ${currentWs}` }) });
    await new BackendProcess(d).start(w, SETTINGS);
    expect(signals).toEqual([]);
  });
});
```

(`existsSync`, `mkdirSync` must be in the file's `node:fs` import.)

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/process-info.test.ts test/runtime-backend-process.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `src/runtime/process-info.ts`**

```ts
// vscode-free. Who owns a pid, what it runs, and when it started (spec §3.7) — the
// facts that let the extension kill a stale backend and nothing else.
import type { ExecOutcome, ProcessInfo } from "./backend-process.js";

export function parseEtime(etime: string): number | null {
  const match = /^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$/.exec(etime.trim());
  if (!match) return null;
  const [, days, hours, minutes, seconds] = match;
  return Number(days ?? 0) * 86400 + Number(hours ?? 0) * 3600
    + Number(minutes) * 60 + Number(seconds);
}

export function parsePsLine(line: string, nowSec: number): ProcessInfo | null {
  const match = /^\s*(\d+)\s+(\S+)\s+(.+?)\s*$/.exec(line);
  if (!match) return null;
  const elapsed = parseEtime(match[2]);
  if (elapsed === null) return null;
  return { uid: Number(match[1]), startedAtSec: nowSec - elapsed, command: match[3] };
}

export async function readProcessInfo(
  pid: number,
  exec: (cmd: string, args: string[], timeoutMs: number) => Promise<ExecOutcome>,
): Promise<ProcessInfo | null> {
  if (process.platform === "win32") return null;
  const out = await exec("/usr/bin/env",
    ["LC_ALL=C", "ps", "-ww", "-o", "uid=,etime=,command=", "-p", String(pid)], 5000);
  if (out.code !== 0) return null;
  const line = out.stdout.split("\n").find((l) => l.trim() !== "");
  return line ? parsePsLine(line, Math.floor(Date.now() / 1000)) : null;
}
```

- [ ] **Step 4: Implement lock reading, reuse and reap in `backend-process.ts`**

Replace the old `readLock` with:

```ts
export const LOCK_YOUNG_SEC = 60;
export const REAP_GRACE_MS = 5000;

function lockPath(workspace: string): string {
  return join(workspace, ".crucible/state", "agentd.lock");
}

function unlinkLock(workspace: string): void {
  try { unlinkSync(lockPath(workspace)); } catch { /* gone already */ }
}

export function readOwnedLock(workspace: string, uid: number | undefined): LockInfo | null {
  let fd: number;
  try {
    fd = openSync(lockPath(workspace), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  } catch {
    return null; // missing — or a planted symlink, which the spawn's rename replaces
  }
  let raw: string;
  try {
    const st = fstatSync(fd);
    // A lock planted by another user in a shared workspace (or a hard link to
    // someone else's file) is ignored and removed: no wait, no signal (spec §3.7).
    if (!st.isFile() || st.nlink !== 1 || (uid !== undefined && st.uid !== uid)) {
      unlinkLock(workspace);
      return null;
    }
    raw = readFileSync(fd, "utf8");
  } finally {
    closeSync(fd);
  }
  try {
    const lock = JSON.parse(raw) as Partial<LockInfo>;
    if (typeof lock.pid === "number" && typeof lock.port === "number"
        && typeof lock.started_at === "number") {
      return lock as LockInfo;
    }
  } catch { /* fall through */ }
  unlinkLock(workspace);
  return null;
}

export function isOurBackend(
  info: ProcessInfo, lock: LockInfo, workspace: string, uid: number | undefined,
): boolean {
  if (uid === undefined || info.uid !== uid) return false;
  if (info.startedAtSec > Math.floor(lock.started_at) + 1) return false; // recycled pid
  const cmd = info.command;
  if (cmd.includes("agentd.serve") && cmd.endsWith(` --workspace-lock ${workspace}`)) return true;
  const tokens = cmd.split(/\s+/);
  const portIdx = tokens.indexOf("--port");
  return tokens.includes("agentd.main:app") && portIdx >= 0
    && tokens[portIdx + 1] === String(lock.port);
}
```

(`openSync`, `fstatSync`, `closeSync`, `constants`, `readFileSync`, `unlinkSync` from `node:fs`.)

Replace step 1 of `start()`:

```ts
    // 1. Reuse only the backend the lock names, serving this workspace (spec §3.7).
    const lock = readOwnedLock(workspace, this.deps.uid);
    if (lock) {
      const expect = { pid: lock.pid, workspace };
      let result = await probeHealth(lock.port, this.probeDeps(), expect);
      const ageSec = this.deps.now() / 1000 - lock.started_at;
      if (result === "down" && ageSec < LOCK_YOUNG_SEC && this.deps.isPidAlive(lock.pid)
          && lock.pid !== this.lastChildPid) {
        // Its spawn may still be starting: wait once, up to the lock's 60 s mark.
        for (let i = 0; i < Math.ceil(LOCK_YOUNG_SEC - ageSec) && result === "down"; i++) {
          await this.deps.sleep(1000);
          result = await probeHealth(lock.port, this.probeDeps(), expect);
        }
      }
      if (result === "authed") {
        this._port = lock.port;
        this.deps.log(`[runtime] reusing live backend pid=${lock.pid} port=${lock.port}`);
        return { port: lock.port, reused: true };
      }
      await this.reap(lock, workspace);
    }
```

and add:

```ts
  private async reap(lock: LockInfo, workspace: string): Promise<void> {
    unlinkLock(workspace);
    this.deps.log(`[runtime] reaped stale lock (pid=${lock.pid})`);
    if (this.platform === "win32-x64" || lock.pid <= 1) return;
    const info = await this.deps.processInfo(lock.pid);
    if (!info || !isOurBackend(info, lock, workspace, this.deps.uid)) return;
    this.deps.signal(lock.pid, "SIGTERM");
    for (let waited = 0; waited < REAP_GRACE_MS && this.deps.isPidAlive(lock.pid); waited += 1000) {
      await this.deps.sleep(1000);
    }
    if (this.deps.isPidAlive(lock.pid)) this.deps.signal(lock.pid, "SIGKILL");
  }
```

In `vscode-runtime.ts`: `processInfo: (pid) => readProcessInfo(pid, execWithTimeout),` (import `readProcessInfo` from `./process-info.js`).

- [ ] **Step 5: Run tests and typecheck**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/src/runtime apps/vscode-extension/test
git commit -m "feat(extension): reuse only the backend the lock names; reap only verified processes"
```

### Task 12: Stop that waits, runtime-version checks, update modal, installer

**Files:**
- Modify: `src/runtime/backend-process.ts` (`stop`, `preSpawnChecks`)
- Modify: `src/runtime/vscode-runtime.ts` (`startForWorkspace` error routing, `promptRuntimeUpdate`)
- Modify: `src/runtime/installer.ts` (rename-based binary writes, `import agentd.serve` check, `isEditableInstall`)
- Test: `test/runtime-backend-process.test.ts`, `test/runtime-installer.test.ts`

**Interfaces:**
- Consumes: `RuntimeUpdateRequiredError`, `ProcessDeps.exec` (Task 10).
- Produces:
  - `STOP_WAIT_MS = 10_000`, `PRESPAWN_TIMEOUT_MS = 5000`
  - `BackendProcess.stop(): Promise<void>` — SIGTERM the watcher and the backend, await `exited` up to 10 s, then SIGKILL and await again (bounded the same way).
  - `isEditableInstall(runtimeDir: string, platform: PlatformKey, fs?: { readdir(p: string): string[]; readFile(p: string): string }): boolean`
  - `RuntimeManager` routes `RuntimeUpdateRequiredError` from every start path to one modal at a time.

- [ ] **Step 1: Write the failing tests**

In `test/runtime-backend-process.test.ts`:

```ts
describe("pre-spawn checks", () => {
  it("an old venv without agentd.serve needs a runtime update", async () => {
    const d = deps({ exec: async (_cmd, args) => args.includes("import agentd.serve")
      ? { code: 1, stdout: "", stderr: "No module named agentd.serve", timedOut: false }
      : { code: 0, stdout: "0.1.0 auth=1", stderr: "", timedOut: false } });
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toMatchObject({ component: "agentd" });
    expect(d.spawned).toHaveLength(0);
  });

  it.each([
    [{ code: 0, stdout: "0.1.0", stderr: "", timedOut: false }],
    [{ code: null, stdout: "", stderr: "", timedOut: true }],
  ])("an indexer without auth=1, or one that times out, needs an update", async (outcome) => {
    const d = deps({ exec: async (_cmd, args) => args[0] === "--version" ? outcome
      : { code: 0, stdout: "", stderr: "", timedOut: false } });
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await expect(new BackendProcess(d).start(ws(), SETTINGS))
      .rejects.toMatchObject({ component: "indexer" });
  });

  it("passes the 5 s timeout to every pre-spawn exec", async () => {
    const timeouts: number[] = [];
    const d = deps({ exec: async (_c, _a, ms) => { timeouts.push(ms);
      return { code: 0, stdout: "x auth=1", stderr: "", timedOut: false }; } });
    mkdirSync(join(d.runtimeDir, "bin"), { recursive: true });
    writeFileSync(join(d.runtimeDir, "bin", "crucible-indexer"), "");
    await new BackendProcess(d).start(ws(), SETTINGS);
    expect(timeouts).toEqual([5000, 5000]);
  });
});

describe("stop", () => {
  it("sends SIGTERM and waits for exit", async () => {
    const child = stubChild(4242, { pid: 4242, port: 8123 });
    const d = deps({}, child);
    const p = new BackendProcess(d);
    await p.start(ws(), SETTINGS);
    await p.stop();
    expect(child.killed).toEqual(["SIGTERM"]);
  });

  it("escalates to SIGKILL when the backend does not exit in time", async () => {
    let resolveExit: (c: number | null) => void = () => {};
    const killed: string[] = [];
    const child: ChildHandle = {
      pid: 4242, exited: new Promise((r) => { resolveExit = r; }),
      kill: (sig) => { killed.push(sig ?? "SIGTERM"); if (sig === "SIGKILL") resolveExit(null); },
      onExit: () => {},
      onStdoutLine: (cb) => cb('CRUCIBLE_SERVE {"pid": 4242, "port": 8123}'),
    };
    const p = new BackendProcess(deps({}, child as never));
    await p.start(ws(), SETTINGS);
    await p.stop();
    expect(killed).toEqual(["SIGTERM", "SIGKILL"]);
  });
});
```

(the stub's `sleep` resolves at once, so `stop()`'s 10 s wait elapses immediately when `exited` has not resolved.)

In `test/runtime-installer.test.ts` (read the file's existing harness first and reuse its `deps` builder):

```ts
  it("checks `import agentd.serve` for an installed agentd", async () => {
    // Arrange as the existing "already installed" test does, then:
    expect(execCalls.some((c) => c.args.join(" ") === "-c import agentd.serve")).toBe(true);
    expect(execCalls.some((c) => c.args.join(" ") === "-c import uvicorn")).toBe(false);
  });

  it("writes binaries through a temp file and rename", async () => {
    // Arrange a binary component install as the existing indexer test does; then:
    const bin = join(runtimeDir, "bin");
    expect(readdirSync(bin).filter((n) => n.includes(".tmp"))).toEqual([]);
    expect(statSync(join(bin, "crucible-indexer")).mode & 0o111).not.toBe(0);
  });
```

and

```ts
describe("isEditableInstall", () => {
  const fsFor = (files: Record<string, string>, dirs: Record<string, string[]>) => ({
    readdir: (p: string) => dirs[p] ?? (() => { throw new Error("ENOENT"); })(),
    readFile: (p: string) => files[p] ?? (() => { throw new Error("ENOENT"); })(),
  });
  it("detects an editable crucible_agentd install", () => {
    const sp = join("/rt", "venv", "lib", "python3.13", "site-packages");
    const fs = fsFor(
      { [join(sp, "crucible_agentd-0.1.0.dist-info", "direct_url.json")]:
          JSON.stringify({ url: "file:///src", dir_info: { editable: true } }) },
      { [join("/rt", "venv", "lib")]: ["python3.13"], [sp]: ["crucible_agentd-0.1.0.dist-info"] });
    expect(isEditableInstall("/rt", "darwin-arm64", fs)).toBe(true);
  });
  it("is false for a wheel install or a missing venv", () => {
    expect(isEditableInstall("/rt", "darwin-arm64", fsFor({}, {}))).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm run -w crucible-vscode-extension test -- test/runtime-backend-process.test.ts test/runtime-installer.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `stop()` and `preSpawnChecks()`**

```ts
export const STOP_WAIT_MS = 10_000;
export const PRESPAWN_TIMEOUT_MS = 5000;

  private async preSpawnChecks(): Promise<void> {
    const py = venvPython(this.deps.runtimeDir, this.platform);
    const serve = await this.deps.exec(py, ["-c", "import agentd.serve"], PRESPAWN_TIMEOUT_MS);
    if (serve.code !== 0) {
      throw new RuntimeUpdateRequiredError("agentd", serve.timedOut
        ? "`import agentd.serve` timed out" : "this runtime predates agentd.serve");
    }
    const indexer = binPath(this.deps.runtimeDir, "crucible-indexer", this.platform);
    if (!existsSync(indexer)) return; // the watcher is skipped, as before
    // An old indexer has no --version arm and starts a full watching indexer instead:
    // the timeout (which kills it) is what makes this check safe.
    const version = await this.deps.exec(indexer, ["--version"], PRESPAWN_TIMEOUT_MS);
    if (version.code !== 0 || version.timedOut || !/\bauth=1\b/.test(version.stdout)) {
      throw new RuntimeUpdateRequiredError("indexer", "the indexer predates backend authentication");
    }
  }

  async stop(): Promise<void> {
    const watcher = this.watcher;
    const backend = this.backend;
    this.watcher = undefined;
    this.backend = undefined;
    this._port = undefined;
    // Watcher first so it doesn't observe the backend vanishing mid-write.
    try { watcher?.kill("SIGTERM"); } catch { /* already dead */ }
    if (!backend) return;
    try { backend.kill("SIGTERM"); } catch { /* already dead */ }
    // agentd.serve's 5 s graceful shutdown finishes lifespan teardown inside this wait,
    // so restart() never reads the lock of a backend that is still draining.
    if (await this.exitsWithin(backend, STOP_WAIT_MS)) return;
    try { backend.kill("SIGKILL"); } catch { /* already dead */ }
    await this.exitsWithin(backend, STOP_WAIT_MS);
  }

  private async exitsWithin(child: ChildHandle, ms: number): Promise<boolean> {
    let exited = false;
    await Promise.race([
      child.exited.then(() => { exited = true; }),
      this.deps.sleep(ms),
    ]);
    return exited;
  }
```

Call `await this.preSpawnChecks();` in `start()` right before step 2 (after the reuse block — a reused backend needs no local check).

- [ ] **Step 4: Installer**

In `installer.ts`, the binary write becomes:

```ts
    const dest = binPath(this.deps.runtimeDir, BIN_NAME[id]!, this.platform);
    // Temp file + rename: overwriting a RUNNING binary in place gets it killed on macOS
    // arm64 and fails with ETXTBSY on Linux (spec §3.7).
    const tmp = `${dest}.tmp-${process.pid}-${Date.now()}`;
    writeFileSync(tmp, data);
    if (this.platform !== "win32-x64") chmodSync(tmp, 0o755);
    renameSync(tmp, dest);
```

The installed-agentd check: `this.deps.exec(py, ["-c", "import agentd.serve"])`, with the comment's last line changed to `"No module named agentd.serve"`. Add:

```ts
export function isEditableInstall(
  runtimeDir: string, platform: PlatformKey,
  fs: { readdir(p: string): string[]; readFile(p: string): string } =
    { readdir: (p) => readdirSync(p), readFile: (p) => readFileSync(p, "utf8") },
): boolean {
  const venv = join(runtimeDir, "venv");
  let sitePackages: string[];
  try {
    sitePackages = platform === "win32-x64"
      ? [join(venv, "Lib", "site-packages")]
      : fs.readdir(join(venv, "lib")).filter((d) => d.startsWith("python3."))
          .map((d) => join(venv, "lib", d, "site-packages"));
  } catch {
    return false;
  }
  for (const sp of sitePackages) {
    let entries: string[];
    try { entries = fs.readdir(sp); } catch { continue; }
    for (const entry of entries.filter((e) => /^crucible_agentd-.*\.dist-info$/.test(e))) {
      try {
        const direct = JSON.parse(fs.readFile(join(sp, entry, "direct_url.json"))) as
          { dir_info?: { editable?: boolean } };
        if (direct.dir_info?.editable === true) return true;
      } catch { /* not a direct-url install */ }
    }
  }
  return false;
}
```

(`readdirSync`, `renameSync` added to the `node:fs` import.)

- [ ] **Step 5: Route the error to a modal (`vscode-runtime.ts`)**

In `startForWorkspace`'s `catch`:

```ts
    } catch (err) {
      if (err instanceof RuntimeUpdateRequiredError) {
        this.statusBar.text = "$(error) Crucible: runtime update required";
        this.statusBar.show();
        void this.promptRuntimeUpdate(workspace, err);
      } else {
        this.markFailed(err instanceof Error ? err.message : String(err));
      }
      throw err;
    }
```

Activation, crash-respawn (`watchCrash` → `startForWorkspace`) and `restart()` (→ `startForWorkspace`) all pass through here. Add:

```ts
  private updatePromptOpen = false;

  private async promptRuntimeUpdate(workspace: string, err: RuntimeUpdateRequiredError): Promise<void> {
    if (this.updatePromptOpen) return; // one modal, however many start paths fail
    this.updatePromptOpen = true;
    try {
      if (isEditableInstall(this.runtimeDir, platformKey())) {
        await vscode.window.showErrorMessage(
          `Crucible runtime update required. ${err.message}. This is an editable development install: run scripts/dev/install-local.sh, then restart the backend.`,
          { modal: true });
        return;
      }
      const choice = await vscode.window.showErrorMessage(
        `Crucible runtime update required. ${err.message}.`, { modal: true }, "Update runtime");
      if (choice !== "Update runtime") return;
      this.intentionalStops.add(workspace);
      try {
        await this.processes.get(workspace)?.stop();
      } finally {
        this.intentionalStops.delete(workspace);
      }
      await this.install((p) => this.output.appendLine(`[install] ${p.id}: ${p.status}`));
      this.restartAttempts.delete(workspace);
      await this.startForWorkspace(workspace);
    } finally {
      this.updatePromptOpen = false;
    }
  }
```

(imports: `RuntimeUpdateRequiredError` from `./backend-process.js`, `isEditableInstall` from `./installer.js`, `platformKey` from `./manifest.js`; add `showErrorMessage(message, options: { modal: boolean }, ...items)` to `src/vscode-shim.d.ts` if the overload is missing.)

Add a test for the editable message to the extension's runtime-manager tests if one exists (`grep -ln "RuntimeManager" test/`); otherwise the `isEditableInstall` unit tests plus the live smoke (Task 16, step "editable install") cover it.

- [ ] **Step 6: Run tests and typecheck**

Run: `npm run -w crucible-vscode-extension test && npm run -w crucible-vscode-extension typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/vscode-extension/src apps/vscode-extension/test
git commit -m "feat(extension): wait for stop, require auth-aware runtime, update modal"
```

---
# Part D — Rust indexer (`services/indexer-rs`)

### Task 13: Token, health verification, direct POST, `--version`

**Files:**
- Create: `src/backend_auth.rs`
- Modify: `src/lib.rs` (`pub mod backend_auth;`), `src/service.rs` (`IndexerService` field + `persist_snapshot` POST), `src/main.rs` (`--version` arm), `Cargo.toml`
- Test: `tests/backend_auth_tests.rs`

**Interfaces:**
- Produces (`crucible_indexer::backend_auth`):
  - `pub fn token_path(home: &Path, port: u16) -> PathBuf`
  - `pub fn read_token(home: &Path, port: u16) -> Option<String>` — `O_NOFOLLOW` open, metadata of the handle: regular file, owned by the user, no group/other bits; exactly 43 `[A-Za-z0-9_-]` bytes.
  - `pub fn health_proof(token: &str, nonce: &str) -> String` (lowercase hex)
  - `pub fn normalize_backend_url(raw: &str) -> Option<(String, u16)>` — `localhost` → `127.0.0.1`; `None` unless the host is then `127.0.0.1`; returns `(base_without_trailing_slash, port)`.
  - `pub struct BackendNotifier` with `pub fn new(home: PathBuf) -> anyhow::Result<Self>` and `pub async fn notify_index_build(&self, backend_url: &str, workspace: &str)`
- Version line: `crucible-indexer --version` prints `<CARGO_PKG_VERSION> auth=1` and exits 0.

Behaviour of `notify_index_build`: normalize the URL (non-loopback → warn once, return); read the token per call (none → debug log, return); if this token is not the last verified one, `GET <base>/health?nonce=<32 hex>` and compare `proof` — a mismatch logs a warning and returns without POSTing; then POST `{"workspace_path": …}` with `Authorization: Bearer <token>`. A 401 is logged once per token. The client is built once with `.no_proxy()`, `redirect::Policy::none()` and a 10 s timeout.

- [ ] **Step 1: Add dependencies**

`Cargo.toml`:

```toml
[dependencies]
# …existing…
hmac = "0.12"
sha2 = "0.10"
hex = "0.4"

[target.'cfg(unix)'.dependencies]
libc = "0.2"

[dev-dependencies]
pretty_assertions = "1.4"
tempfile = "3"
tokio = { version = "1.44", features = ["rt-multi-thread", "macros", "net", "io-util"] }
```

- [ ] **Step 2: Write the failing tests** (`tests/backend_auth_tests.rs`)

```rust
use crucible_indexer::backend_auth::{
    health_proof, normalize_backend_url, read_token, token_path, BackendNotifier,
};
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

const TOKEN: &str = "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"; // 43 × 'k'

fn put_token(home: &std::path::Path, port: u16, content: &str) {
    let path = token_path(home, port);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, content).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
}

#[test]
fn proof_matches_the_backend() {
    assert_eq!(
        health_proof(TOKEN, "00112233445566778899aabbccddeeff"),
        "3d0b0b66aa8ba0eaac39f9a4c8bc9a8b2fe21fff8db87b61c0ea75eb183f5f15"
    );
}

#[test]
fn reads_only_a_well_formed_private_file() {
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), 1, TOKEN);
    assert_eq!(read_token(home.path(), 1).as_deref(), Some(TOKEN));
    put_token(home.path(), 2, &format!("{TOKEN}\n"));
    assert_eq!(read_token(home.path(), 2), None);
    assert_eq!(read_token(home.path(), 3), None);
    #[cfg(unix)]
    {
        put_token(home.path(), 4, TOKEN);
        fs::set_permissions(token_path(home.path(), 4), fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(read_token(home.path(), 4), None);
        std::os::unix::fs::symlink(token_path(home.path(), 1), token_path(home.path(), 5)).unwrap();
        assert_eq!(read_token(home.path(), 5), None);
    }
}

#[test]
fn normalizes_only_loopback() {
    assert_eq!(
        normalize_backend_url("http://localhost:8123/"),
        Some(("http://127.0.0.1:8123".to_string(), 8123))
    );
    assert_eq!(normalize_backend_url("http://example.com:8123"), None);
}

/// A one-connection-at-a-time HTTP stub: answers /health with a proof for `proof_token`
/// and records every request's raw text.
async fn stub_backend(proof_token: &'static str, post_status: u16) -> (u16, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let seen2 = seen.clone();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 8192];
            let n = sock.read(&mut buf).await.unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            seen2.lock().unwrap().push(req.clone());
            let (status, body) = if req.starts_with("GET /health?nonce=") {
                let nonce = req.split("nonce=").nth(1).unwrap().split_whitespace().next().unwrap();
                (200, format!("{{\"status\":\"ok\",\"proof\":\"{}\"}}", health_proof(proof_token, nonce)))
            } else {
                (post_status, "{}".to_string())
            };
            // A 307 carries a real location, so the redirect test proves the policy.
            let extra = if status == 307 { "location: /elsewhere\r\n" } else { "" };
            let resp = format!(
                "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\n{extra}content-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len());
            sock.write_all(resp.as_bytes()).await.unwrap();
        }
    });
    (port, seen)
}

#[tokio::test]
async fn posts_with_the_token_after_a_valid_proof() {
    let (port, seen) = stub_backend(TOKEN, 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://localhost:{port}"), "/ws").await;
    let seen = seen.lock().unwrap().clone();
    assert!(seen[0].starts_with("GET /health?nonce="));
    assert!(!seen[0].to_lowercase().contains("authorization"));
    assert!(seen[1].starts_with("POST /v1/index/build"));
    assert!(seen[1].contains(&format!("authorization: Bearer {TOKEN}")));
    assert!(seen[1].to_lowercase().contains(&format!("host: 127.0.0.1:{port}")));
}

#[tokio::test]
async fn does_not_post_when_the_proof_is_wrong() {
    let (port, seen) = stub_backend("wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww", 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://127.0.0.1:{port}"), "/ws").await;
    let seen = seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 1);
}

#[tokio::test]
async fn verifies_once_per_token_and_picks_up_a_rewritten_token() {
    let (port, seen) = stub_backend(TOKEN, 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    let base = format!("http://127.0.0.1:{port}");
    notifier.notify_index_build(&base, "/ws").await;
    notifier.notify_index_build(&base, "/ws").await;
    let health_calls = seen.lock().unwrap().iter().filter(|r| r.starts_with("GET /health")).count();
    assert_eq!(health_calls, 1);
}

#[tokio::test]
async fn does_not_follow_redirects() {
    // A 307 to /elsewhere must not be followed with the token.
    let (port, seen) = stub_backend(TOKEN, 307).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://127.0.0.1:{port}"), "/ws").await;
    assert_eq!(seen.lock().unwrap().len(), 2); // health + the one POST, no follow-up
}
```

- [ ] **Step 3: Run to verify failure**

Run: `cargo test --test backend_auth_tests 2>&1 | tail -5`
Expected: compile error (`backend_auth` not found).

- [ ] **Step 4: Implement `src/backend_auth.rs`**

```rust
//! Talking to an authenticated agentd (spec §3.2, §3.4, §3.5): read the per-port token,
//! check the backend proves it holds that token, then POST with it — directly (no proxy),
//! never following a redirect, only ever to 127.0.0.1.

use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::hash_map::RandomState;
use std::fs::OpenOptions;
use std::hash::{BuildHasher, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

const TOKEN_LEN: usize = 43;

pub fn token_path(home: &Path, port: u16) -> PathBuf {
    home.join(".crucible").join("run").join(format!("agentd-{port}.token"))
}

pub fn read_token(home: &Path, port: u16) -> Option<String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    let file = options.open(token_path(home, port)).ok()?;
    #[cfg(unix)]
    {
        let meta = file.metadata().ok()?; // the handle, never stat-then-open
        // SAFETY: getuid has no preconditions and cannot fail.
        let uid = unsafe { libc::getuid() };
        if !meta.is_file() || meta.uid() != uid || meta.mode() & 0o077 != 0 {
            return None;
        }
    }
    let mut text = String::new();
    file.take((TOKEN_LEN + 1) as u64).read_to_string(&mut text).ok()?;
    let valid = text.len() == TOKEN_LEN
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    valid.then_some(text)
}

pub fn health_proof(token: &str, nonce: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("any key length");
    mac.update(b"crucible-health-v1\0");
    mac.update(nonce.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

pub fn normalize_backend_url(raw: &str) -> Option<(String, u16)> {
    let mut url = reqwest::Url::parse(raw.trim()).ok()?;
    if url.host_str() == Some("localhost") {
        url.set_host(Some("127.0.0.1")).ok()?;
    }
    if url.host_str() != Some("127.0.0.1") {
        return None;
    }
    let port = url.port_or_known_default()?;
    Some((url.as_str().trim_end_matches('/').to_string(), port))
}

fn nonce() -> String {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let mut out = String::new();
    for salt in 0..2u64 {
        let mut h = RandomState::new().build_hasher();
        h.write_u128(nanos);
        h.write_u64(salt);
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out // 32 hex chars; uniqueness, not secrecy, is what a nonce needs here
}

pub struct BackendNotifier {
    home: PathBuf,
    client: reqwest::Client,
    verified_token: Arc<Mutex<Option<String>>>,
    logged_401_for: Arc<Mutex<Option<String>>>,
}

impl BackendNotifier {
    pub fn new(home: PathBuf) -> anyhow::Result<Self> {
        let client = reqwest::Client::builder()
            .no_proxy() // the default honours HTTP(S)_PROXY and would hand the token to a proxy
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(10))
            .build()?;
        Ok(Self {
            home,
            client,
            verified_token: Arc::new(Mutex::new(None)),
            logged_401_for: Arc::new(Mutex::new(None)),
        })
    }

    pub async fn notify_index_build(&self, backend_url: &str, workspace: &str) {
        let Some((base, port)) = normalize_backend_url(backend_url) else {
            tracing::warn!(url = %backend_url, "backend URL is not 127.0.0.1; index-build notification skipped");
            return;
        };
        let Some(token) = read_token(&self.home, port) else {
            tracing::debug!(port, "no readable backend token; index-build notification skipped");
            return;
        };
        let already = self.verified_token.lock().unwrap().as_deref() == Some(token.as_str());
        if !already {
            if !self.verify(&base, &token).await {
                return;
            }
            *self.verified_token.lock().unwrap() = Some(token.clone());
        }
        let result = self
            .client
            .post(format!("{base}/v1/index/build"))
            .bearer_auth(&token)
            .json(&serde_json::json!({ "workspace_path": workspace }))
            .send()
            .await;
        match result {
            Ok(resp) if resp.status().is_success() => {
                tracing::debug!(url = %base, "notified backend: index build accepted");
            }
            Ok(resp) if resp.status() == reqwest::StatusCode::UNAUTHORIZED => {
                let mut logged = self.logged_401_for.lock().unwrap();
                if logged.as_deref() != Some(token.as_str()) {
                    tracing::warn!(url = %base, "backend rejected the index-build token (401)");
                    *logged = Some(token.clone());
                }
                *self.verified_token.lock().unwrap() = None;
            }
            Ok(resp) => {
                tracing::warn!(url = %base, status = %resp.status(), "backend index-build notification returned non-2xx");
            }
            Err(err) => {
                *self.verified_token.lock().unwrap() = None;
                tracing::warn!(url = %base, error = %err, "backend index-build notification failed (backend may not be running)");
            }
        }
    }

    async fn verify(&self, base: &str, token: &str) -> bool {
        let nonce = nonce();
        let response = match self.client.get(format!("{base}/health?nonce={nonce}")).send().await {
            Ok(r) => r,
            Err(err) => {
                tracing::debug!(error = %err, "backend health probe failed");
                return false;
            }
        };
        let body: serde_json::Value = match response.json().await {
            Ok(v) => v,
            Err(_) => return false,
        };
        let ok = body.get("proof").and_then(|p| p.as_str()) == Some(health_proof(token, &nonce).as_str());
        if !ok {
            tracing::warn!(url = %base, "backend did not prove it holds the token; not sending it");
        }
        ok
    }
}
```

- [ ] **Step 5: Wire it in**

`src/lib.rs`: add `pub mod backend_auth;`.

`src/service.rs`: add the field `notifier: Option<std::sync::Arc<crate::backend_auth::BackendNotifier>>,` to `IndexerService`; in `new()` before `Ok(Self {`:

```rust
        #[allow(deprecated)] // home_dir is correct on Windows from Rust 1.86; toolchain is 1.93
        let notifier = match (&config.backend_url, std::env::home_dir()) {
            (Some(_), Some(home)) => Some(std::sync::Arc::new(
                crate::backend_auth::BackendNotifier::new(home)?)),
            _ => None,
        };
```

and `notifier,` in the struct literal. Replace the `if let Some(backend_url) = &self.config.backend_url { … }` block in `persist_snapshot` with:

```rust
        if let (Some(notifier), Some(backend_url)) = (&self.notifier, &self.config.backend_url) {
            let notifier = notifier.clone();
            let backend_url = backend_url.clone();
            let workspace = self.config.workspace_root.display().to_string();
            tokio::spawn(async move {
                notifier.notify_index_build(&backend_url, &workspace).await;
            });
        }
```

The test-only `IndexerConfig` literals in `src/lsp.rs` (lines ~1688, ~1760) set `backend_url: None` and need no change.

`src/main.rs`, first arm of the `match`:

```rust
        Some("--version") => {
            // `auth=1` tells the extension this indexer authenticates to agentd (spec §3.7).
            println!("{} auth=1", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
```

- [ ] **Step 6: Run the indexer suite**

Run: `cargo test 2>&1 | tail -15; cargo run -q -- --version`
Expected: all tests pass; `0.1.0 auth=1`.

- [ ] **Step 7: Commit**

```bash
git add services/indexer-rs
git commit -m "feat(indexer): verify the backend, send the token directly, --version auth=1"
```

---

# Part E — Dev scripts, launch sites, docs

### Task 14: Script helpers and every script that calls the backend

**Files:**
- Create: `scripts/_backend_auth.py`, `scripts/_backend_auth.sh`
- Modify: `scripts/drive_clarify_live.py`, `scripts/e2e-scripted.sh`, `scripts/eval/skill_trigger_eval.py`, `scripts/stress/e2e-stress-test.py`, `scripts/stress/run-constrained-task.sh`, `scripts/stress/verify-task.sh`, `scripts/verify/{01_create_task,02_feedback,03_finalize,04_resume,controller_ux_smoke,env_profile_e2e}.py` (`scripts/stress/start-backend.sh` is Task 15)
- Test: `services/agentd-py/tests/test_scripts_use_auth_helper.py`, `services/agentd-py/tests/test_backend_auth_script_helper.py`

**Interfaces:**
- Produces:
  - `scripts/_backend_auth.py`: `backend_url(base: str) -> str` (localhost → 127.0.0.1, no trailing slash); `auth_headers(base: str) -> dict[str, str]` (verifies `/health`'s proof first — once per token — then returns `{"Authorization": "Bearer …"}`; raises `BackendAuthError` with an actionable message otherwise); CLI `python3 scripts/_backend_auth.py header <base>` prints `Authorization: Bearer <token>`, `… url <base>` prints the normalized URL; exit 1 with the message on failure.
  - `scripts/_backend_auth.sh`: `crucible_auth_header <base>` and `crucible_backend_url <base>` (thin wrappers over the Python CLI, so the verification logic exists once).

- [ ] **Step 1: Write the failing tests**

`services/agentd-py/tests/test_scripts_use_auth_helper.py`:

```python
"""Every dev script that talks to agentd uses the auth helper (spec §3.5)."""
from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
EXCLUDED = {"_backend_auth.py", "_backend_auth.sh", "smoke_clarify_gate.py"}


def test_scripts_use_the_auth_helper() -> None:
    offenders = []
    for path in sorted(SCRIPTS.rglob("*")):
        if not path.is_file() or "node_modules" in path.parts or path.name in EXCLUDED:
            continue
        if path.suffix not in {".py", ".sh", ".mjs", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ("/v1/" in text or "/health" in text) and "_backend_auth" not in text:
            offenders.append(str(path.relative_to(SCRIPTS)))
    assert offenders == []
```

`services/agentd-py/tests/test_backend_auth_script_helper.py` — loads the helper by path and checks it against a live `agentd.serve` started the same way as `tests/test_serve.py` (import that module's `_start`, `_handshake`, `_stop`):

```python
"""scripts/_backend_auth.py against a real agentd.serve."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tests.test_serve import _handshake, _start, _stop

HELPER = Path(__file__).resolve().parents[3] / "scripts" / "_backend_auth.py"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX")


def _load():
    spec = importlib.util.spec_from_file_location("_backend_auth", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_url_rewrites_localhost() -> None:
    assert _load().backend_url("http://localhost:8000/") == "http://127.0.0.1:8000"


def test_auth_headers_verify_then_return_the_token(tmp_path: Path, monkeypatch) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    try:
        port = _handshake(proc)["port"]
        monkeypatch.setenv("HOME", str(home))
        helper = _load()
        headers = helper.auth_headers(f"http://localhost:{port}")
        token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
        assert headers == {"Authorization": f"Bearer {token}"}
    finally:
        _stop(proc)


def test_auth_headers_refuse_a_wrong_proof(tmp_path: Path, monkeypatch) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    try:
        port = _handshake(proc)["port"]
        fake_home = tmp_path / "fake"
        (fake_home / ".crucible/run").mkdir(parents=True, mode=0o700)
        os.chmod(fake_home / ".crucible", 0o700)
        bogus = fake_home / ".crucible/run" / f"agentd-{port}.token"
        bogus.write_text("z" * 43)
        os.chmod(bogus, 0o600)
        monkeypatch.setenv("HOME", str(fake_home))
        helper = _load()
        with pytest.raises(helper.BackendAuthError):
            helper.auth_headers(f"http://127.0.0.1:{port}")
    finally:
        _stop(proc)
```

- [ ] **Step 2: Run to verify failure**

Run (from `services/agentd-py`): `.venv/bin/pytest tests/test_scripts_use_auth_helper.py tests/test_backend_auth_script_helper.py --color=no --timeout=120 > /tmp/t14.txt 2>&1; echo exit=$?; tail -15 /tmp/t14.txt`
Expected: exit≠0, offenders listed; helper missing.

- [ ] **Step 3: Write `scripts/_backend_auth.py`**

```python
"""Dev-script helper for an authenticated agentd (spec §3.5).

Reads ~/.crucible/run/agentd-<port>.token, checks GET /health?nonce= proves the server
holds it, and only then hands out the Authorization header. Standard library only.

    from _backend_auth import auth_headers, backend_url
    python3 scripts/_backend_auth.py header http://127.0.0.1:8000
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIED: dict[str, str] = {}


class BackendAuthError(RuntimeError):
    """The backend could not be verified, or no usable token file exists."""


def backend_url(base: str) -> str:
    parts = urllib.parse.urlsplit(base.strip())
    host = "127.0.0.1" if parts.hostname == "localhost" else (parts.hostname or "")
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")).rstrip("/")


def _port(base: str) -> int:
    parts = urllib.parse.urlsplit(base)
    return parts.port or (443 if parts.scheme == "https" else 80)


def _read_token(port: int) -> str:
    path = Path.home() / ".crucible" / "run" / f"agentd-{port}.token"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BackendAuthError(
            f"no readable token at {path} — is a backend running on port {port} "
            "(started with `python -m agentd.serve`)?") from exc
    try:
        st = os.fstat(fd)
        if os.name == "posix" and (
                not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise BackendAuthError(f"{path} is not a private regular file owned by you")
        text = os.read(fd, 44).decode("ascii", errors="replace")
    finally:
        os.close(fd)
    if not _TOKEN_RE.fullmatch(text):
        raise BackendAuthError(f"{path} does not hold a token")
    return text


def _verify(base: str, token: str) -> None:
    nonce = secrets.token_hex(16)
    try:
        with urllib.request.urlopen(f"{base}/health?nonce={nonce}", timeout=5) as resp:
            body = json.loads(resp.read() or b"{}")
    except OSError as exc:
        raise BackendAuthError(f"backend at {base} is not reachable: {exc}") from exc
    expected = hmac.new(token.encode("ascii"), b"crucible-health-v1\0" + nonce.encode("ascii"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(body.get("proof", "")), expected):
        raise BackendAuthError(
            f"the server at {base} did not prove it holds the token in ~/.crucible/run; "
            "refusing to send it")


def auth_headers(base: str) -> dict[str, str]:
    url = backend_url(base)
    if urllib.parse.urlsplit(url).hostname != "127.0.0.1":
        raise BackendAuthError(f"{url}: the backend must be at 127.0.0.1")
    token = _read_token(_port(url))
    if _VERIFIED.get(url) != token:  # a restart rewrites the token: verify again
        _verify(url, token)
        _VERIFIED[url] = token
    return {"Authorization": f"Bearer {token}"}


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"header", "url"}:
        print("usage: _backend_auth.py header|url <base_url>", file=sys.stderr)
        return 2
    try:
        if argv[0] == "url":
            print(backend_url(argv[1]))
        else:
            (name, value), = auth_headers(argv[1]).items()
            print(f"{name}: {value}")
    except BackendAuthError as exc:
        print(f"crucible auth: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
```

- [ ] **Step 4: Write `scripts/_backend_auth.sh`**

```bash
# shellcheck shell=bash
# Source me: crucible_auth_header <base_url>, crucible_backend_url <base_url> (spec §3.5).
# Thin wrappers so the token-verification logic lives once, in _backend_auth.py.
_CRUCIBLE_AUTH_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_backend_auth.py"

crucible_auth_header() {
  python3 "$_CRUCIBLE_AUTH_PY" header "$1"
}

crucible_backend_url() {
  python3 "$_CRUCIBLE_AUTH_PY" url "$1"
}
```

- [ ] **Step 5: Convert each script**

Python scripts — at the top, before other local imports, with `N` = depth below `scripts/` (`scripts/x.py` → 0, `scripts/a/b.py` → 1):

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[N]))
from _backend_auth import auth_headers, backend_url  # noqa: E402
```

then wrap the script's base URL once (`BASE = backend_url(BASE)`) and pass `headers=auth_headers(BASE)` (merged with any existing headers) to every request — `urllib.request.Request(..., headers={**auth_headers(BASE), ...})`, `requests.get(..., headers=auth_headers(BASE))`, `httpx.Client(headers=auth_headers(BASE))`, whichever the script uses. Worked example (`scripts/verify/01_create_task.py`, depth 1):

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _backend_auth import auth_headers, backend_url  # noqa: E402

BASE = backend_url(os.environ.get("CRUCIBLE_BACKEND_URL", "http://127.0.0.1:8000"))
# …
req = urllib.request.Request(f"{BASE}/v1/tasks", data=payload,
                             headers={**auth_headers(BASE), "content-type": "application/json"})
```

Shell scripts — source the helper next to the script's other setup, then add the header to every `curl` that reaches agentd:

```bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_backend_auth.sh"   # adjust ../ to the depth
BASE="$(crucible_backend_url "${BASE:-http://127.0.0.1:8000}")"
curl -sf -H "$(crucible_auth_header "$BASE")" "$BASE/v1/tasks/$TASK_ID"
```

A `curl` aimed at a different service (Ollama, TurboQuant) stays unchanged. Call `crucible_auth_header` per request in loops (it reads the token each time, so a backend restart mid-script is picked up).

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_scripts_use_auth_helper.py tests/test_backend_auth_script_helper.py --color=no --timeout=120 > /tmp/t14.txt 2>&1; echo exit=$?; tail -5 /tmp/t14.txt` and `for f in scripts/verify/*.py scripts/stress/e2e-stress-test.py scripts/drive_clarify_live.py scripts/eval/skill_trigger_eval.py; do python3 -m py_compile "$f" || echo "BROKEN $f"; done; bash -n scripts/e2e-scripted.sh scripts/stress/*.sh`
Expected: `exit=0`; `start-backend.sh` may still be listed as an offender until Task 15 — if so, run Task 15 before re-running this test. No `BROKEN`, no bash syntax errors.

- [ ] **Step 7: Commit**

```bash
git add scripts services/agentd-py/tests/test_scripts_use_auth_helper.py services/agentd-py/tests/test_backend_auth_script_helper.py
git commit -m "feat(scripts): auth helper; every backend-calling script verifies and sends the token"
```

### Task 15: Launch sites, `install-local.sh`, docs

**Files:**
- Modify: `scripts/stress/start-backend.sh`, `scripts/e2e-scripted.sh`, `scripts/dev/install-local.sh`, `CLAUDE.md`, `README.md`, `services/agentd-py/README.md`

- [ ] **Step 1: `start-backend.sh`**

- Source the helper near the top: `source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_backend_auth.sh"`.
- The launch line (~469) becomes `./.venv/bin/python -m agentd.serve --port "$PORT" --reload 2>&1 | tee "$LOG_FILE"`.
- Every agentd URL uses `127.0.0.1`: `_health_url`, `_build_url`, `_status_url`, and the watcher's `CRUCIBLE_BACKEND_URL` (~559).
- The readiness loop (~474–482) re-reads per iteration and accepts only a verified backend:

```bash
_base="http://127.0.0.1:${PORT}"
for _ in $(seq 1 60); do
  # The token file appears just after bind; the helper verifies /health's proof.
  if crucible_auth_header "$_base" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! crucible_auth_header "$_base" >/dev/null; then
  echo "==> backend did not become ready (see $LOG_FILE)" >&2
  exit 1
fi
```

- The two pre-warm `curl`s gain `-H "$(crucible_auth_header "$_base")"`.
- Delete the stale header comment line about `--reload` if it still describes `uvicorn`.

- [ ] **Step 2: `e2e-scripted.sh`**

Both launch sites become `python -m agentd.serve --port "$PORT"` (keep their existing flags other than the uvicorn app string; drop `--host` if present — `agentd.serve` always binds `127.0.0.1`), and any agentd `curl` uses the helper (Task 14's rule). Find them with `grep -n "uvicorn\|curl" scripts/e2e-scripted.sh`.

- [ ] **Step 3: `install-local.sh` builds and installs the indexer**

After the backend install block, outside the `DO_BACKEND` condition:

```bash
echo "==> building the indexer"
# shellcheck source=../stress/_indexer.sh
source "$REPO/scripts/stress/_indexer.sh"
_indexer_bin="$(ensure_indexer_binary "$REPO/services/indexer-rs")"
_dest="$HOME/.crucible/runtime/bin/crucible-indexer"
mkdir -p "$(dirname "$_dest")"
# Temp file + mv: overwriting a running binary in place gets it killed on macOS arm64
# and fails with ETXTBSY on Linux.
cp "$_indexer_bin" "$_dest.tmp.$$"
chmod 755 "$_dest.tmp.$$"
mv -f "$_dest.tmp.$$" "$_dest"
echo "==> indexer installed; restart the backend (Crucible: Restart Backend) so the watcher picks it up"
```

Update the usage header: the backend line becomes "backend   crucible.devSourcePath -> `uv pip install -e services/agentd-py` in ~/.crucible/runtime/venv (restart the backend to pick up edits)" and add "indexer   built from services/indexer-rs and installed to ~/.crucible/runtime/bin".

- [ ] **Step 4: Docs**

- `CLAUDE.md`:
  - The `CRUCIBLE_PORT` entry under "Key Configuration → Core" is replaced by: "`CRUCIBLE_LISTEN_PORT` / `CRUCIBLE_SERVE_PID` — set by `python -m agentd.serve` for the app process only (stripped from every subprocess by `child_env()`); the app refuses to start without them. `CRUCIBLE_AUTH_DISABLED=1` skips only the token check — any local user can then drive the backend."
  - The P4 "Startup lockfile" bullet: the lock is written by `agentd.serve --workspace-lock` (atomic, symlink-safe), never deleted at shutdown, and reused only after `/health` proves the pid and workspace.
  - "Starting the backend for local testing" and every `uvicorn agentd.main:app` mention → `python -m agentd.serve --port 8000 [--reload]`.
  - "Inspecting a task mid-flight" / "Watching the SSE stream": every `curl` gets `-H "Authorization: Bearer $(cat ~/.crucible/run/agentd-8000.token)"` and `127.0.0.1`.
  - A new "Backend authentication" subsection under "Architecture Details" summarizing spec §3: token file location and rules, middleware order (peer → Host → Origin → token), the `/health` proof, verify-before-send in every client, and the `CRUCIBLE_AUTH_DISABLED` escape hatch — pointing to the spec for detail.
- `README.md` and `services/agentd-py/README.md`: replace the `uvicorn agentd.main:app` run line with `python -m agentd.serve --port 8000 --reload` and add the curl header line.

Find every mention: `grep -rn "uvicorn agentd.main\|CRUCIBLE_PORT\|localhost:8000" CLAUDE.md README.md services/agentd-py/README.md`.

- [ ] **Step 5: Check**

Run: `bash -n scripts/stress/start-backend.sh scripts/e2e-scripted.sh scripts/dev/install-local.sh && grep -rn "uvicorn agentd.main\|CRUCIBLE_PORT" CLAUDE.md README.md services/agentd-py/README.md scripts --include='*' | grep -v node_modules`
Expected: no syntax errors; the grep prints nothing (the only remaining `agentd.main:app` mentions are the reap migration code and `agentd/serve.py`'s `APP`, outside these paths). Then re-run Task 14's grep test: `cd services/agentd-py && .venv/bin/pytest tests/test_scripts_use_auth_helper.py --color=no > /tmp/t15.txt 2>&1; echo exit=$?` → `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add scripts CLAUDE.md README.md services/agentd-py/README.md
git commit -m "chore(auth): launch via agentd.serve everywhere; install-local builds the indexer; docs"
```

---

# Part F — Verification

### Task 16: Full suites and live smoke

- [ ] **Step 1: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -3 /tmp/py.txt
cd ../indexer-rs && cargo test 2>&1 | tail -5
cd ../.. && npm run build && npm run test && npm run typecheck
```

Expected: all green. `tests/test_command_only_step.py::test_command_only_step_runs_command_and_verifies` is a known intermittent full-suite flake that passes in isolation (seen before this work on 2026-10-05); re-run it alone before attributing a failure to this change.

- [ ] **Step 2: Reinstall the dev runtime**

`scripts/dev/install-local.sh` (rebuilds the extension, the editable backend and the indexer). Never quit the user's main VS Code: the dev host is the second instance on CDP port 9335.

- [ ] **Step 3: Live checks against the managed backend** (port from the status bar)

```bash
P=<port>; T="$(cat ~/.crucible/run/agentd-$P.token)"
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: attacker.example" http://127.0.0.1:$P/v1/config          # 421
curl -s -o /dev/null -w "%{http_code}\n" -H "Origin: https://evil.example" -H "Authorization: Bearer $T" http://127.0.0.1:$P/v1/config  # 403
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:$P/v1/config                                      # 401
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $T" http://127.0.0.1:$P/v1/config        # 200
curl -s "http://127.0.0.1:$P/health?nonce=00112233445566778899aabbccddeeff"                                 # pid + proof + bound
```

- [ ] **Step 4: Live behaviour**

1. The dev host chats with no user action (one full turn with an edit).
2. Edit a workspace file; the indexer's watcher log shows the index-build notification accepted (no 401).
3. Kill the backend (`kill <pid>`): the crash-respawn comes up on a new port, the chat keeps working, and nothing sent a token to the old port (agentd log of the new backend shows no 401s; the extension output shows the re-probe).
4. Reload the dev-host window: the new extension host reuses the running backend (output: `reusing live backend`).
5. Start a second workspace's dev host after stopping the first workspace's backend so the kernel may reuse its port; reopening the first workspace never attaches to the second's backend (Review Focus 1).
6. `scripts/stress/start-backend.sh --reload` in a scratch workspace: save a backend file; `scripts/verify/01_create_task.py` still works without re-reading anything.
7. Editable install with an old venv: temporarily `mv ~/.crucible/runtime/venv/lib/python3.*/site-packages/agentd/serve.py{,.bak}`, restart the backend → the modal names `install-local.sh`; restore the file.
8. Set `crucible.backendBaseUrl` to `http://localhost:<port>` in the dev host: the chat works (rewritten to `127.0.0.1`).

Record anything that fails as a finding, fix it with a regression test, and re-run the relevant step.
