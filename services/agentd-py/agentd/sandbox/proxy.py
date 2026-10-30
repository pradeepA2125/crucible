"""The sandbox's only route out: an HTTP CONNECT / forward proxy that checks host names.

Each sandboxed command gets a lease whose token rides in its proxy URL
(`http://<token>:x@127.0.0.1:<port>`), so the proxy knows which command asked, applies
that command's allowed hosts, and records the hosts it refused for the blocked-command
annotation. TLS is tunnelled, never inspected. A host that resolves only to internal
addresses (loopback, link-local, cloud metadata) is refused unless the workspace or the
command listed it explicitly; the proxy connects to the address it checked, so a second
DNS answer cannot redirect it.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agentd.sandbox.hosts import DEFAULT_HOSTS, normalize_host

logger = logging.getLogger(__name__)

Resolver = Callable[[str], Awaitable[list[str]]]
_HEAD_LIMIT = 64 * 1024
_CHUNK = 65536
_METADATA = frozenset({"169.254.169.254", "fd00:ec2::254"})
_UNRESOLVED = ""


@dataclass
class ProxyLease:
    token: str
    url: str
    extra_hosts: frozenset[str]
    refused: list[str] = field(default_factory=list)
    allow_any: bool = False
    workspace_hosts: frozenset[str] = frozenset()


def is_internal_address(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return (ip in _METADATA or address.is_loopback or address.is_link_local
            or address.is_unspecified)


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _short_socket_path() -> Path:
    # Unix socket paths are limited to ~104 characters; macOS's TMPDIR is too long.
    directory = Path("/tmp") if sys.platform == "darwin" else Path(tempfile.gettempdir())
    return directory / f"crucible-proxy-{os.getpid()}-{secrets.token_hex(4)}.sock"


class SandboxProxy:
    def __init__(self, *, resolver: Resolver = _system_resolve,
                 is_internal: Callable[[str], bool] = is_internal_address,
                 unix_socket: bool = sys.platform != "darwin") -> None:
        self._resolve = resolver
        self._is_internal = is_internal
        self._want_unix = unix_socket
        self._leases: dict[str, ProxyLease] = {}
        self._writers: dict[str, set[asyncio.StreamWriter]] = {}
        self._servers: list[asyncio.AbstractServer] = []
        self.port = 0
        self.socket_path: Path | None = None

    async def start(self) -> None:
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self._servers.append(server)
        self.port = server.sockets[0].getsockname()[1]
        if self._want_unix:
            path = _short_socket_path()
            self._servers.append(await asyncio.start_unix_server(self._handle, path=str(path)))
            self.socket_path = path

    async def close(self) -> None:
        for server in self._servers:
            server.close()
        for writers in self._writers.values():
            for writer in writers:
                writer.close()
        self._servers.clear()
        if self.socket_path is not None:
            self.socket_path.unlink(missing_ok=True)

    def lease(self, workspace_hosts: Iterable[str], extra_hosts: Iterable[str] = (),
              allow_any: bool = False) -> ProxyLease:
        token = secrets.token_hex(16)
        lease = ProxyLease(
            token=token, url=f"http://{token}:x@127.0.0.1:{self.port}",
            extra_hosts=frozenset(normalize_host(h) for h in extra_hosts),
            allow_any=allow_any,
            workspace_hosts=frozenset(normalize_host(h) for h in workspace_hosts))
        self._leases[token] = lease
        return lease

    def release(self, lease: ProxyLease) -> None:
        self._leases.pop(lease.token, None)
        for writer in self._writers.pop(lease.token, set()):
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        if len(head) > _HEAD_LIMIT:
            await self._reply(writer, 431, "Request Header Fields Too Large")
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            await self._reply(writer, 400, "Bad Request")
            return
        headers = [line for line in lines[1:] if line]
        lease = self._lease_for(headers)
        if lease is None:
            await self._reply(writer, 407, "Proxy Authentication Required",
                              'Proxy-Authenticate: Basic realm="crucible-sandbox"\r\n')
            return
        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            first = b""
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                await self._reply(writer, 400, "Bad Request")
                return
            host, port_text = parts.hostname, str(parts.port or 80)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            kept = [h for h in headers
                    if not h.lower().startswith(("proxy-authorization:", "proxy-connection:"))]
            first = "\r\n".join([f"{method} {path} {version}", *kept, "", ""]).encode("latin-1")
        try:
            host = normalize_host(host.strip("[]"))
            port = int(port_text)
        except ValueError:
            await self._reply(writer, 400, "Bad Request")
            return
        address = await self._check(lease, host)
        if address is None:
            await self._reply(writer, 403, "Forbidden")
            return
        if address == _UNRESOLVED:
            await self._reply(writer, 502, "Bad Gateway")
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(address, port)
        except OSError:
            await self._reply(writer, 502, "Bad Gateway")
            return
        if first:
            up_writer.write(first)
        else:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        tracked = self._writers.setdefault(lease.token, set())
        tracked.update((writer, up_writer))
        await asyncio.gather(self._pipe(reader, up_writer), self._pipe(up_reader, writer))
        tracked.difference_update((writer, up_writer))

    def _lease_for(self, headers: list[str]) -> ProxyLease | None:
        for line in headers:
            name, _, value = line.partition(":")
            if name.strip().lower() != "proxy-authorization":
                continue
            scheme, _, encoded = value.strip().partition(" ")
            if scheme.lower() != "basic":
                return None
            try:
                user = base64.b64decode(encoded).decode().partition(":")[0]
            except ValueError:
                return None
            return self._leases.get(user)
        return None

    async def _check(self, lease: ProxyLease, host: str) -> str | None:
        """The address to connect to, None when the host is refused (recorded), or
        _UNRESOLVED for an allowed host with no address — an ordinary failure, not a
        sandbox block."""
        listed = host in lease.workspace_hosts or host in lease.extra_hosts
        allowed = lease.allow_any or listed or host in DEFAULT_HOSTS
        address: str | None = None
        if allowed:
            try:
                addresses = await self._resolve(host)
            except OSError:
                addresses = []
            usable = [a for a in addresses
                      if lease.allow_any or listed or not self._is_internal(a)]
            address = usable[0] if usable else None
            if address is None and addresses:
                allowed = False
            elif address is None:
                return _UNRESOLVED
        if not allowed:
            if host not in lease.refused:
                lease.refused.append(host)
            logger.info("[sandbox] proxy refused %s", host)
            return None
        return address

    async def _pipe(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        try:
            while data := await source.read(_CHUNK):
                sink.write(data)
                await sink.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                sink.close()
            except RuntimeError:
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, reason: str,
                     extra: str = "") -> None:
        writer.write(f"HTTP/1.1 {status} {reason}\r\n{extra}Content-Length: 0\r\n"
                     f"Connection: close\r\n\r\n".encode())
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()
