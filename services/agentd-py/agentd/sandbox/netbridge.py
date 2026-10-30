"""Inside a bubblewrap network namespace: expose the backend's proxy on 127.0.0.1:<port>.

The namespace has only loopback, so the proxy (outside, on the host) is reachable only
through a Unix socket bound into the sandbox. This forwards each TCP connection on the
port to that socket, runs the command as a child, and exits with its status.

Standard library only, and run as a script (`python netbridge.py SOCKET PORT -- CMD...`):
the backend package is not importable inside the sandbox.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading

_CHUNK = 65536


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(_CHUNK):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve(listener: socket.socket, socket_path: str) -> None:
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.connect(socket_path)
        except OSError:
            client.close()
            continue
        threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        print("usage: netbridge.py SOCKET PORT -- COMMAND...", file=sys.stderr)
        return 2
    socket_path, port, command = argv[0], int(argv[1]), argv[3:]
    if port and os.path.exists(socket_path):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(64)
        threading.Thread(target=_serve, args=(listener, socket_path), daemon=True).start()
    child = subprocess.Popen(command)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: child.send_signal(signum))
    return child.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
