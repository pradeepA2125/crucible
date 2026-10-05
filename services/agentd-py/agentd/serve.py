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
import traceback
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


def _run(server: uvicorn.Server, sockets: list[socket.socket] | None) -> None:
    try:
        server.run(sockets=sockets)
    except SystemExit:
        pass  # uvicorn exits on its own startup errors (e.g. an import-string failure)
    except Exception:
        # agentd.main raising at import is a startup failure too: show it, exit 3 below.
        traceback.print_exc()


class _Worker:
    """Reload-worker target: picklable, so ChangeReload can hand it to the spawned child."""

    def __init__(self, config: uvicorn.Config) -> None:
        self.config = config

    def __call__(self, sockets: list[socket.socket] | None = None) -> None:
        for sock in sockets or []:
            sock.set_inheritable(False)
        server = uvicorn.Server(self.config)
        _run(server, sockets)
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
    _run(server, [sock])
    sys.exit(0 if server.started else EXIT_STARTUP_FAILED)


if __name__ == "__main__":
    main()
