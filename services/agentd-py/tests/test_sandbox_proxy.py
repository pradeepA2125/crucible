"""The sandbox's host-checking proxy, offline: fake host names resolve to local servers."""

from __future__ import annotations

import asyncio
import base64

import pytest

from agentd.sandbox.proxy import SandboxProxy

METADATA = "169.254.169.254"


async def _echo_server() -> tuple[asyncio.AbstractServer, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(1024)
        if data.startswith(b"GET "):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        else:
            writer.write(data.upper())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _resolver(table: dict[str, str]):
    async def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError(f"cannot resolve {host}")
        return [table[host]]
    return resolve


async def _proxy(table: dict[str, str]) -> SandboxProxy:
    proxy = SandboxProxy(resolver=_resolver(table), is_internal=lambda ip: ip == METADATA,
                         unix_socket=False)
    await proxy.start()
    return proxy


def _auth(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:x".encode()).decode()


async def _connect(proxy: SandboxProxy, target: str, token: str | None) -> tuple[bytes, object]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    auth = f"Proxy-Authorization: {_auth(token)}\r\n" if token else ""
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    return head, (reader, writer)


@pytest.mark.asyncio
async def test_connect_to_default_host_tunnels():
    server, port = await _echo_server()
    proxy = await _proxy({"pypi.org": "127.0.0.1"})
    lease = proxy.lease(workspace_hosts=[])
    head, (reader, writer) = await _connect(proxy, f"pypi.org:{port}", lease.token)
    assert head.startswith(b"HTTP/1.1 200")
    writer.write(b"hello")
    await writer.drain()
    assert await reader.read(5) == b"HELLO"
    writer.close()
    await proxy.close()
    server.close()


@pytest.mark.asyncio
async def test_unknown_host_refused_and_recorded():
    proxy = await _proxy({"evil.example": "127.0.0.1"})
    lease = proxy.lease(workspace_hosts=[])
    head, _ = await _connect(proxy, "evil.example:443", lease.token)
    assert head.startswith(b"HTTP/1.1 403")
    await _connect(proxy, "evil.example:443", lease.token)
    assert lease.refused == ["evil.example"]
    await proxy.close()


@pytest.mark.asyncio
async def test_workspace_and_extra_hosts_allowed_for_that_lease_only():
    server, port = await _echo_server()
    proxy = await _proxy({"api.example.com": "127.0.0.1", "db.example.com": "127.0.0.1"})
    with_hosts = proxy.lease(workspace_hosts=["api.example.com"], extra_hosts=["db.example.com"])
    other = proxy.lease(workspace_hosts=[])
    for target in ("api.example.com", "db.example.com"):
        head, _ = await _connect(proxy, f"{target}:{port}", with_hosts.token)
        assert head.startswith(b"HTTP/1.1 200"), target
    head, _ = await _connect(proxy, f"db.example.com:{port}", other.token)
    assert head.startswith(b"HTTP/1.1 403")
    await proxy.close()
    server.close()


@pytest.mark.asyncio
async def test_internal_address_refused_unless_listed():
    # The local test server stands in for an internal address (dialing the real metadata
    # address hangs on networks without it).
    server, port = await _echo_server()
    table = {"pypi.org": "127.0.0.1", "meta.internal": "127.0.0.1"}
    proxy = SandboxProxy(resolver=_resolver(table), is_internal=lambda ip: ip == "127.0.0.1",
                         unix_socket=False)
    await proxy.start()
    lease = proxy.lease(workspace_hosts=[])
    head, _ = await _connect(proxy, f"pypi.org:{port}", lease.token)
    assert head.startswith(b"HTTP/1.1 403")
    assert lease.refused == ["pypi.org"]
    listed = proxy.lease(workspace_hosts=["meta.internal"])
    head, _ = await _connect(proxy, f"meta.internal:{port}", listed.token)
    assert head.startswith(b"HTTP/1.1 200")
    await proxy.close()
    server.close()


def test_metadata_and_loopback_are_internal():
    from agentd.sandbox.proxy import is_internal_address
    assert is_internal_address(METADATA) and is_internal_address("127.0.0.1")
    assert is_internal_address("::1") and not is_internal_address("140.82.112.3")


@pytest.mark.asyncio
async def test_missing_token_407():
    proxy = await _proxy({})
    head, _ = await _connect(proxy, "pypi.org:443", None)
    assert head.startswith(b"HTTP/1.1 407")
    head, _ = await _connect(proxy, "pypi.org:443", "not-a-lease")
    assert head.startswith(b"HTTP/1.1 407")
    await proxy.close()


@pytest.mark.asyncio
async def test_released_lease_rejected():
    proxy = await _proxy({"pypi.org": "127.0.0.1"})
    lease = proxy.lease(workspace_hosts=[])
    proxy.release(lease)
    head, _ = await _connect(proxy, "pypi.org:443", lease.token)
    assert head.startswith(b"HTTP/1.1 407")
    await proxy.close()


@pytest.mark.asyncio
async def test_allow_any_lease():
    server, port = await _echo_server()
    proxy = await _proxy({"anything.example": "127.0.0.1"})
    lease = proxy.lease(workspace_hosts=[], allow_any=True)
    head, _ = await _connect(proxy, f"anything.example:{port}", lease.token)
    assert head.startswith(b"HTTP/1.1 200")
    await proxy.close()
    server.close()


@pytest.mark.asyncio
async def test_plain_http_forward():
    server, port = await _echo_server()
    proxy = await _proxy({"pypi.org": "127.0.0.1"})
    lease = proxy.lease(workspace_hosts=[])
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(f"GET http://pypi.org:{port}/simple HTTP/1.1\r\nHost: pypi.org\r\n"
                 f"Proxy-Authorization: {_auth(lease.token)}\r\n\r\n".encode())
    await writer.drain()
    body = await reader.read(200)
    assert body.startswith(b"HTTP/1.1 200") and body.endswith(b"ok")
    await proxy.close()
    server.close()


@pytest.mark.asyncio
async def test_unresolvable_allowed_host_is_bad_gateway_not_refused():
    proxy = await _proxy({})
    lease = proxy.lease(workspace_hosts=[])
    head, _ = await _connect(proxy, "pypi.org:443", lease.token)
    assert head.startswith(b"HTTP/1.1 502")
    assert lease.refused == []
    await proxy.close()


def test_lease_url_carries_token():
    proxy = SandboxProxy(resolver=_resolver({}), unix_socket=False)
    proxy.port = 4321
    lease = proxy.lease(workspace_hosts=[])
    assert lease.url == f"http://{lease.token}:x@127.0.0.1:4321"
