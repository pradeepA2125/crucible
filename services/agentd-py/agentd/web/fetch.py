"""Bounded direct HTTP fetching and HTML conversion, with no MCP subprocess."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from agentd.performance import profile_async

MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0
MAX_REDIRECTS = 5
FORMATS = frozenset({"markdown", "text", "html"})
SKIP_ELEMENTS = ("script", "style", "noscript", "iframe", "object", "embed", "meta", "link")
Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(RuntimeError):
    pass


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id or address.sixtofour or address.teredo:
            return False
        if address.ipv4_mapped:
            address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def validate_url(value: str) -> httpx.URL:
    if not value.strip() or len(value) > 2000 or any(ord(c) < 32 for c in value.strip()):
        raise FetchError("Invalid fetch URL")
    try:
        url = httpx.URL(value.strip())
        if url.scheme not in {"http", "https"} or not url.host:
            raise FetchError("Fetch URL must be an absolute HTTP or HTTPS URL")
        if url.userinfo or "@" in url.netloc.decode("ascii"):
            raise FetchError("Fetch URLs must not contain credentials")
        host = url.host.lower().rstrip(".")
        if (
            host == "localhost"
            or host.endswith((".localhost", ".local", ".internal"))
            or "%" in host
        ):
            raise FetchError("Fetching private or internal addresses is not allowed")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not _public_address(host):
                raise FetchError("Fetching private or internal addresses is not allowed")
        return url.copy_with(fragment=None)
    except (httpx.InvalidURL, ValueError) as exc:
        raise FetchError("Invalid fetch URL") from exc


async def resolve_public_host(host: str, port: int) -> list[str]:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError("Fetch hostname could not be resolved") from exc
    return list(dict.fromkeys(str(record[4][0]) for record in records))


def convert_content(content: str, content_type: str, format: str, url: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    is_html = mime in {"text/html", "application/xhtml+xml"}
    if not mime:
        is_html = "<html" in content[:100].lower() or "<!doctype html" in content[:100].lower()
    if format == "html" or not is_html:
        return content
    try:
        soup = BeautifulSoup(content, "html.parser")
        for element in soup.find_all(SKIP_ELEMENTS):
            element.decompose()
        if format == "text":
            return soup.get_text(separator="\n", strip=True)
        for element in soup.find_all(["a", "img"]):
            attr = "href" if element.name == "a" else "src"
            value = element.get(attr)
            if isinstance(value, str):
                element[attr] = urljoin(url, value)
        return markdownify(str(soup), heading_style="ATX", bullets="-", code_language="").strip()
    except (ValueError, RecursionError) as exc:
        raise FetchError("Fetched HTML could not be converted") from exc


@dataclass(frozen=True)
class FetchResult:
    url: str
    content_type: str
    content: str


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return url.scheme, url.host, url.port or (443 if url.scheme == "https" else 80)


def _is_text(mime: str) -> bool:
    return (
        not mime
        or mime.startswith("text/")
        or mime.endswith(("+json", "+xml"))
        or mime in {"application/json", "application/xml"}
    )


class WebFetchClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve_public_host,
    ) -> None:
        self._transport = transport
        self._resolve = resolver

    @profile_async("web.fetch_network_and_conversion")
    async def fetch(
        self, url: str, format: str = "markdown", timeout: float = DEFAULT_TIMEOUT
    ) -> FetchResult:
        current = validate_url(url)
        if format not in FORMATS:
            raise FetchError("Invalid fetch format")
        try:
            async with asyncio.timeout(min(timeout, MAX_TIMEOUT)):
                for hop in range(MAX_REDIRECTS + 1):
                    host = current.raw_host.decode("ascii")
                    addresses = await self._resolve(host, _origin(current)[2])
                    if not addresses or not all(_public_address(ip) for ip in addresses):
                        raise FetchError("Fetching private or internal addresses is not allowed")
                    # Pin the validated IP; original Host and TLS SNI remain intact.
                    # No second hostname lookup can rebind this request to a private IP.
                    target = current.copy_with(host=addresses[0])
                    async with httpx.AsyncClient(
                        transport=self._transport,
                        timeout=timeout,
                        verify=True,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        async with client.stream(
                            "GET",
                            target,
                            headers={
                                "Host": current.netloc.decode("ascii"),
                                "User-Agent": "Crucible-WebFetch/1.0",
                                "Accept": (
                                    "text/markdown, text/plain, text/html, "
                                    "application/xhtml+xml, application/json"
                                ),
                                "Accept-Encoding": "identity",
                            },
                            extensions={"sni_hostname": host},
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location or hop == MAX_REDIRECTS:
                                    raise FetchError(
                                        "Fetch redirect missing a URL or exceeds limit"
                                    )
                                next_url = validate_url(urljoin(str(current), location))
                                if _origin(next_url) != _origin(current):
                                    raise FetchError(
                                        "Fetch redirect crosses origin; fetch the destination "
                                        f"as a separate approved call: {next_url}"
                                    )
                                current = next_url
                                continue
                            if not 200 <= response.status_code < 300:
                                raise FetchError(f"Fetch HTTP error {response.status_code}")
                            encoding = response.headers.get("content-encoding", "").lower().strip()
                            if encoding not in {"", "identity"}:
                                raise FetchError("Unsupported fetch content encoding")
                            content_type = response.headers.get("content-type", "")
                            mime = content_type.split(";", 1)[0].lower().strip()
                            if not _is_text(mime):
                                raise FetchError(f"Unsupported content type: {mime}")
                            length = response.headers.get("content-length", "")
                            if length.isdigit() and int(length) > MAX_BYTES:
                                raise FetchError("Fetch response exceeds 5 MiB")
                            body = bytearray()
                            async for chunk in response.aiter_raw(chunk_size=16384):
                                if len(body) + len(chunk) > MAX_BYTES:
                                    raise FetchError("Fetch response exceeds 5 MiB")
                                body.extend(chunk)
                            try:
                                text = bytes(body).decode(response.encoding or "utf-8")
                            except (LookupError, UnicodeError) as exc:
                                raise FetchError("Fetched page has invalid text encoding") from exc
                        content = await asyncio.to_thread(
                            convert_content, text, content_type, format, str(current)
                        )
                        if not content.strip():
                            raise FetchError("Fetched page returned empty content")
                        return FetchResult(str(current), content_type, content)
                raise FetchError("Fetch redirect exceeds limit")
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise FetchError("Fetch timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchError("Fetch transport failed") from exc
