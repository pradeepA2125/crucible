"""Native fetch downloads public pages and preserves bounds before conversion."""

import asyncio

import httpx
import pytest

from agentd.web.fetch import FetchError, WebFetchClient, convert_content, validate_url
from tests.test_web_exa import RecordingStream


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "https://user:pass@example.com/",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://example.local/",
        "http://example.internal/",
        "https:///missing",
    ],
)
def test_invalid_or_internal_url(url):
    with pytest.raises(FetchError):
        validate_url(url)


def test_normalize_public_url():
    assert (
        str(validate_url(" https://example.com/path?q=a#section "))
        == "https://example.com/path?q=a"
    )


@pytest.mark.parametrize("format", ["markdown", "text", "html"])
def test_html_conversion_preserves_content_and_removes_active_elements(format):
    html = (
        '<html><head><title>Docs</title><style>hidden-css</style></head>'
        '<body><h1>Heading</h1><p>Hello <strong>world</strong>.</p>'
        '<a href="/guide">Guide</a><pre><code>x = 1</code></pre>'
        '<script>hidden-js</script></body></html>'
    )
    out = convert_content(html, "text/html", format, "https://example.com/docs")
    assert "Heading" in out and "world" in out
    if format == "html":
        assert "<h1>" in out and "hidden-js" in out
    else:
        assert "hidden-js" not in out and "hidden-css" not in out
    if format == "markdown":
        assert "# Heading" in out and "**world**" in out
        assert "https://example.com/guide" in out and "```" in out


def test_plain_and_json_content_are_preserved():
    for mime in ("text/plain", "application/json", "text/markdown"):
        assert (
            convert_content('{"ok":true}', mime, "markdown", "https://example.com") == '{"ok":true}'
        )


async def public_dns(host, port):
    return ["93.184.216.34"]


def client_for(stream, *, status=200, headers=None, resolver=public_dns, handler=None):
    return WebFetchClient(
        transport=httpx.MockTransport(
            handler
            or (
                lambda req: httpx.Response(
                    status, stream=stream, headers={"content-type": "text/html", **(headers or {})}
                )
            )
        ),
        resolver=resolver,
    )


@pytest.mark.asyncio
async def test_public_download_pins_dns_and_preserves_host_and_tls_name(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://secret:password@proxy.invalid:8080")
    stream = RecordingStream([b"<h1>Docs</h1>"])
    requests = []

    def respond(req):
        requests.append(req)
        assert str(req.url) == "https://93.184.216.34/docs"
        assert req.headers["host"] == "example.com"
        assert req.extensions["sni_hostname"] == "example.com"
        assert req.headers["accept-encoding"] == "identity"
        assert not {"authorization", "cookie", "proxy-authorization"} & set(req.headers)
        assert "secret" not in str(req.headers)
        return httpx.Response(200, stream=stream, headers={"content-type": "text/html"})

    result = await client_for(stream, handler=respond).fetch("https://example.com/docs")
    assert result.url == "https://example.com/docs" and "# Docs" in result.content
    assert len(requests) == 1 and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], [], ["::ffff:127.0.0.1"]]
)
async def test_dns_private_or_mixed_answers_never_connect(addresses):
    async def resolve(host, port):
        return addresses

    def forbidden(req):
        pytest.fail("nonpublic destinations must not connect")

    with pytest.raises(FetchError):
        await client_for(None, resolver=resolve, handler=forbidden).fetch("https://example.com")


@pytest.mark.asyncio
async def test_same_origin_redirect_revalidates_dns_and_does_not_carry_cookies():
    streams = [RecordingStream([]), RecordingStream([b"Done"])]
    calls, resolutions = [], []

    async def resolve(host, port):
        resolutions.append((host, port))
        return ["93.184.216.34"]

    def respond(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(
                302, stream=streams[0], headers={"location": "/next", "set-cookie": "secret=abc"}
            )
        assert "cookie" not in req.headers
        return httpx.Response(200, stream=streams[1], headers={"content-type": "text/plain"})

    result = await client_for(None, handler=respond, resolver=resolve).fetch(
        "https://example.com/start"
    )
    assert result.content == "Done" and result.url == "https://example.com/next"
    assert len(resolutions) == 2 and all(s.closed for s in streams)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "https://other.example/page",
        "http://example.com/page",
        "http://127.0.0.1/",
        "https://user:pass@example.com/",
    ],
)
async def test_cross_origin_or_unsafe_redirect_never_followed(location):
    stream = RecordingStream([])
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(302, stream=stream, headers={"location": location})

    with pytest.raises(FetchError):
        await client_for(stream, handler=respond).fetch("https://example.com")
    assert len(requests) == 1 and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,message",
    [
        ({"content-length": str(5 * 1024 * 1024 + 1)}, "5 MiB"),
        ({"content-encoding": "gzip"}, "encoding"),
        ({"content-type": "image/png"}, "Unsupported content type"),
    ],
)
async def test_unsupported_or_oversized_headers_abort_without_read(headers, message):
    stream = RecordingStream([b"body must not be read"])
    with pytest.raises(FetchError, match=message):
        await client_for(stream, headers=headers).fetch("https://example.com")
    assert stream.closed and stream.yielded == 0


@pytest.mark.asyncio
async def test_stream_limit_and_boundary():
    size = 5 * 1024 * 1024
    for extra in (0, 1):
        stream = RecordingStream([b"x" * size, b"x" * extra])
        client = client_for(stream, headers={"content-type": "text/plain"})
        if extra:
            with pytest.raises(FetchError, match="5 MiB"):
                await client.fetch("https://example.com")
        else:
            assert len((await client.fetch("https://example.com")).content) == size
        assert stream.closed


@pytest.mark.asyncio
async def test_deadline_includes_body_and_cancellation_closes():
    stream = RecordingStream([], block=True)
    with pytest.raises(FetchError, match="timed out"):
        await client_for(stream).fetch("https://example.com", timeout=0.01)
    assert stream.closed
    stream = RecordingStream([], block=True)
    task = asyncio.create_task(client_for(stream).fetch("https://example.com"))
    await stream.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_http_errors_close(status):
    stream = RecordingStream([])
    with pytest.raises(FetchError, match=str(status)):
        await client_for(stream, status=status).fetch("https://example.com")
    assert stream.closed


@pytest.mark.asyncio
async def test_redirect_dns_rebinding_is_blocked_before_second_connection():
    stream = RecordingStream([])
    resolutions = 0
    requests = []

    async def resolve(host, port):
        nonlocal resolutions
        resolutions += 1
        return ["93.184.216.34"] if resolutions == 1 else ["127.0.0.1"]

    def respond(req):
        requests.append(req)
        return httpx.Response(302, stream=stream, headers={"location": "/next"})

    with pytest.raises(FetchError, match="private"):
        await client_for(None, handler=respond, resolver=resolve).fetch("https://example.com")
    assert resolutions == 2 and len(requests) == 1 and stream.closed


@pytest.mark.asyncio
async def test_redirect_loop_has_bounded_requests_and_closes_every_response():
    streams = []

    def respond(req):
        stream = RecordingStream([])
        streams.append(stream)
        return httpx.Response(302, stream=stream, headers={"location": "/again"})

    with pytest.raises(FetchError, match="limit"):
        await client_for(None, handler=respond).fetch("https://example.com")
    assert len(streams) == 6 and all(stream.closed for stream in streams)


@pytest.mark.asyncio
async def test_total_deadline_includes_dns_resolution():
    async def resolve(host, port):
        await asyncio.Event().wait()

    def forbidden(req):
        pytest.fail("DNS timeout must precede connection")

    with pytest.raises(FetchError, match="timed out"):
        await client_for(None, handler=forbidden, resolver=resolve).fetch(
            "https://example.com", timeout=0.01
        )
