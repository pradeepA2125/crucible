import asyncio
import json

import httpx
import pytest

from agentd.web import exa
from agentd.web.exa import ExaSearchClient, WebSearchError, decode_response


def envelope(text):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": text}]}}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


@pytest.mark.parametrize("sse", [False, True])
def test_decode(sse):
    raw = json.dumps(envelope("Title: Docs\nURL: https://docs.python.org"))
    body = ("event: message\ndata: " + raw + "\n\n" if sse else raw).encode()
    assert "https://docs.python.org" in decode_response(
        body, "text/event-stream" if sse else "application/json")


def test_http_success_can_be_tool_error():
    value = envelope("invalid arguments")
    value["result"]["isError"] = True
    with pytest.raises(WebSearchError, match="tool error"):
        decode_response(encoded(value), "application/json")


@pytest.mark.parametrize("value", [
    [], None, {},
    {**envelope("ok"), "id": 2},
    {**envelope("ok"), "id": "1"},
    {**envelope("ok"), "jsonrpc": "1.0"},
    {**envelope("ok"), "error": {"code": -1, "message": "bad"}},
    {**envelope("ok"), "result": None},
    {**envelope("ok"), "result": {"isError": 0, "content": []}},
    {**envelope("ok"), "result": {}},
    {**envelope("ok"), "result": {"content": "bad"}},
    {**envelope("ok"), "result": {"content": []}},
    {**envelope("ok"), "result": {"content": [None]}},
    {**envelope("ok"), "result": {"content": [{"type": "text"}]}},
    {**envelope("ok"), "result": {"content": [{"type": "text", "text": 5}]}},
    envelope(" \n\t"),
    {**envelope("ok"), "result": {"content": [{"type": "image", "data": "x"}]}},
])
def test_reject_bad_envelopes(value):
    with pytest.raises(WebSearchError):
        decode_response(encoded(value), "application/json")


@pytest.mark.parametrize("body", [b"\xff", b"{", b"", b"not json"])
def test_reject_invalid_encoding_and_json(body):
    with pytest.raises(WebSearchError, match="Malformed search response"):
        decode_response(body, "application/json")


def test_multiple_text_blocks():
    value = envelope("first")
    value["result"]["content"] += [
        {"type": "image", "data": "ignored"}, {"type": "text", "text": "second"}]
    assert decode_response(encoded(value), "application/json") == "first\nsecond"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_sse_multiline_comments_notifications(newline):
    raw = json.dumps(envelope("résult"), ensure_ascii=False, indent=2)
    notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"})
    body = newline.join([
        ": keepalive", "event: message", "data: " + notification, "", "",
        "event: message", *["data: " + line for line in raw.splitlines()], "", ""])
    assert decode_response(body.encode(), "text/event-stream; charset=utf-8") == "résult"


@pytest.mark.parametrize("body", [
    b": comment\n\n", b"data: {}\n\n", b"data: invalid\n\n",
    ("data: " + json.dumps(envelope("ok")) + "\n\n") .encode() * 2,
])
def test_sse_requires_one_matching_reply(body):
    with pytest.raises(WebSearchError, match="Malformed search response"):
        decode_response(body, "text/event-stream")


@pytest.mark.asyncio
async def test_outbound_contains_only_search_controls(monkeypatch):
    for name, value in {
        "HTTP_PROXY": "http://proxy-user:proxy-secret@proxy.invalid:8080",
        "HTTPS_PROXY": "http://proxy-user:proxy-secret@proxy.invalid:8080",
        "WORKSPACE": "private-workspace", "MODEL": "private-model",
        "SESSION": "private-session", "SSL_CERT_FILE": "private-workspace/cert.pem",
    }.items():
        monkeypatch.setenv(name, value)
    constructions = []
    original = httpx.AsyncClient

    def spy(**kwargs):
        constructions.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(exa.httpx, "AsyncClient", spy)

    def respond(request):
        assert request.method == "POST"
        assert str(request.url) == "https://mcp.exa.ai/mcp"
        assert not {"authorization", "cookie", "proxy-authorization"} & set(request.headers)
        for marker in ("private-workspace", "private-model", "private-session",
                       "proxy-user", "proxy-secret"):
            assert marker not in str(request.headers) + str(request.url) + request.content.decode()
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "web_search_exa", "arguments": {
                "query": "asyncio docs", "numResults": 5, "type": "auto"}}}
        return httpx.Response(200, stream=RecordingStream([
            encoded(envelope("No results found."))]))

    client = ExaSearchClient(transport=httpx.MockTransport(respond))
    assert await client.search("asyncio docs", 5) == "No results found."
    assert len(constructions) == 1
    assert constructions[0]["verify"] is True
    assert constructions[0]["follow_redirects"] is False
    assert constructions[0]["trust_env"] is False
    assert constructions[0]["timeout"] == 25


class RecordingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, block=False):
        self.chunks = chunks
        self.closed = False
        self.yielded = 0
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        if self.block:
            await self.release.wait()

    async def aclose(self):
        self.closed = True


def stream_client(stream, *, content_type="application/json", status=200, headers=None):
    return ExaSearchClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, stream=stream, headers={
            "content-type": content_type, **(headers or {})})))


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("chunk_size", [1, 7, 16384])
async def test_arbitrary_chunk_boundaries_and_success_close(sse, chunk_size):
    body = encoded(envelope("résult https://example.com"))
    if sse:
        body = b"event: message\r\ndata: " + body + b"\r\n\r\n"
    stream = RecordingStream([body[i:i + chunk_size] for i in range(0, len(body), chunk_size)])
    assert await stream_client(stream, content_type=(
        "text/event-stream" if sse else "application/json")).search("docs", 5) == (
            "résult https://example.com")
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "unknown", "identity, gzip"])
async def test_compressed_response_rejected_before_reading(encoding, monkeypatch):
    decoded_reads = []
    original = httpx.Response.aiter_bytes

    async def spy(response, *args, **kwargs):
        decoded_reads.append(True)
        async for chunk in original(response, *args, **kwargs):
            yield chunk

    monkeypatch.setattr(httpx.Response, "aiter_bytes", spy)
    stream = RecordingStream([b"compressed body must not be read"])
    try:
        with pytest.raises(WebSearchError, match="Unsupported search content encoding"):
            await stream_client(stream, headers={"content-encoding": encoding}).search("docs", 5)
    finally:
        assert stream.closed
        assert stream.yielded == 0
        assert not decoded_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
async def test_deeply_nested_response_is_malformed_and_closes(sse):
    body = b"[" * 10000 + b"0" + b"]" * 10000
    if sse:
        body = b"data: " + body + b"\n\n"
    stream = RecordingStream([body])
    try:
        with pytest.raises(WebSearchError, match="Malformed search response") as exc:
            await stream_client(stream, content_type=(
                "text/event-stream" if sse else "application/json")).search("docs", 5)
        assert isinstance(exc.value.__cause__, RecursionError)
    finally:
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("reply_id", [True, False])
async def test_boolean_reply_id_is_malformed_and_closes(sse, reply_id):
    body = encoded({**envelope("ok"), "id": reply_id})
    if sse:
        body = b"data: " + body + b"\n\n"
    stream = RecordingStream([body])
    try:
        with pytest.raises(WebSearchError, match="Malformed search response"):
            await stream_client(stream, content_type=(
                "text/event-stream" if sse else "application/json")).search("docs", 5)
    finally:
        assert stream.closed


@pytest.mark.parametrize("sse", [False, True])
def test_numeric_float_reply_id_is_valid(sse):
    body = encoded({**envelope("ok"), "id": 1.0})
    if sse:
        body = b"data: " + body + b"\n\n"
    assert decode_response(body, "text/event-stream" if sse else "application/json") == "ok"


def test_sse_boolean_id_is_not_a_matching_reply():
    body = b"data: " + encoded({**envelope("ignored"), "id": True}) + b"\n\n"
    body += b"data: " + encoded(envelope("ok")) + b"\n\n"
    assert decode_response(body, "text/event-stream") == "ok"


@pytest.mark.asyncio
async def test_identity_response_uses_raw_stream(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("decoded iterator must not be used")
        yield b""  # Make this an async generator like aiter_bytes.

    monkeypatch.setattr(httpx.Response, "aiter_bytes", forbidden)
    stream = RecordingStream([encoded(envelope("ok"))])
    assert await stream_client(stream, headers={"content-encoding": "identity"}).search(
        "docs", 5) == "ok"
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1])
async def test_byte_limit_boundary_and_overflow_close(extra):
    assert exa.MAX_BYTES == 1024 * 1024
    body = encoded(envelope("ok"))
    body += b" " * (1024 * 1024 + extra - len(body))
    stream = RecordingStream([body[i:i + 8192] for i in range(0, len(body), 8192)])
    client = stream_client(stream)
    if extra:
        with pytest.raises(WebSearchError, match="exceeds 1 MiB"):
            await client.search("docs", 5)
    else:
        assert await client.search("docs", 5) == "ok"
    assert stream.closed


@pytest.mark.asyncio
async def test_overflow_aborts_before_consuming_remaining_body():
    stream = RecordingStream([b"x" * 16384] * 100)
    with pytest.raises(WebSearchError, match="exceeds 1 MiB"):
        await stream_client(stream).search("docs", 5)
    assert stream.yielded == 65
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message", [
    (302, "HTTP error 302"), (429, "rate limited; try later"),
    (500, "upstream outage; try later"), (599, "upstream outage; try later"),
    (403, "HTTP error 403"),
])
async def test_http_errors_do_not_redirect_or_retry(status, message):
    requests = []
    stream = RecordingStream([b"do not read"])

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(status, stream=stream, headers={
            "Location": "https://second-host.example/search"})

    with pytest.raises(WebSearchError, match=message):
        await ExaSearchClient(transport=httpx.MockTransport(respond)).search("docs", 5)
    assert requests == ["https://mcp.exa.ai/mcp"]
    assert stream.yielded == 0
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("error,message", [
    (httpx.ConnectError("secret details"), "transport failed"),
    (httpx.ReadTimeout("secret details"), "timed out"),
])
async def test_transport_errors(error, message):
    def respond(request):
        raise error

    with pytest.raises(WebSearchError, match=message) as exc:
        await ExaSearchClient(transport=httpx.MockTransport(respond)).search("docs", 5)
    assert "secret details" not in str(exc.value)


@pytest.mark.asyncio
async def test_total_deadline_includes_body_and_closes(monkeypatch):
    assert exa.DEADLINE == 25
    monkeypatch.setattr(exa, "DEADLINE", 0.01)
    stream = RecordingStream([encoded(envelope("ok"))], block=True)
    with pytest.raises(WebSearchError, match="Search timed out"):
        await asyncio.wait_for(stream_client(stream).search("docs", 5), timeout=1)
    assert stream.started.is_set()
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes():
    stream = RecordingStream([], block=True)
    task = asyncio.create_task(stream_client(stream).search("docs", 5))
    try:
        await asyncio.wait_for(stream.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
