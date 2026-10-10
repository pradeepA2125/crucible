from __future__ import annotations

import asyncio
import json

import httpx

from agentd.performance import profile_async

ENDPOINT = "https://mcp.exa.ai/mcp"
MAX_BYTES = 1024 * 1024
DEADLINE = 25


class WebSearchError(RuntimeError):
    pass


def decode_response(body: bytes, content_type: str) -> str:
    try:
        text = body.decode("utf-8")
        if content_type.split(";", 1)[0].strip() == "text/event-stream":
            values = []
            data = []
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") + [""]:
                if not line:
                    if data:
                        values.append(json.loads("\n".join(data)))
                        data = []
                elif line.startswith("data:"):
                    value = line[5:]
                    data.append(value[1:] if value.startswith(" ") else value)
            matches = [v for v in values if isinstance(v, dict)
                       and not isinstance(v.get("id"), bool) and v.get("id") == 1]
            if len(matches) != 1:
                raise WebSearchError("Malformed search response")
            value = matches[0]
        else:
            value = json.loads(text)
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
                or isinstance(value.get("id"), bool) or value.get("id") != 1):
            raise WebSearchError("Malformed search response")
        if "error" in value:
            raise WebSearchError("Search provider returned a JSON-RPC error")
        result = value.get("result")
        if not isinstance(result, dict) or result.get("isError", False) is not False:
            raise WebSearchError("Search provider returned a tool error")
        content = result.get("content")
        if not isinstance(content, list):
            raise WebSearchError("Malformed search response")
        parts = []
        for block in content:
            if not isinstance(block, dict):
                raise WebSearchError("Malformed search content")
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise WebSearchError("Malformed search text")
                parts.append(block["text"])
        output = "\n".join(parts)
        if not output.strip():
            raise WebSearchError("Search provider returned empty content")
        return output
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WebSearchError("Malformed search response") from exc


class ExaSearchClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @profile_async("web.search_network")
    async def search(self, query: str, max_results: int) -> str:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "web_search_exa", "arguments": {
                "query": query, "numResults": max_results, "type": "auto"}}}
        try:
            async with asyncio.timeout(DEADLINE):
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=DEADLINE,
                    verify=True, follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream("POST", ENDPOINT, json=payload, headers={
                        "Accept": "application/json, text/event-stream",
                        "Accept-Encoding": "identity",
                    }) as response:
                        if response.status_code == 429:
                            raise WebSearchError("Exa free search is rate limited; try later")
                        if 500 <= response.status_code <= 599:
                            raise WebSearchError("Exa search upstream outage; try later")
                        if response.status_code != 200:
                            raise WebSearchError(f"Search HTTP error {response.status_code}")
                        encoding = response.headers.get("content-encoding", "").strip().lower()
                        if encoding not in ("", "identity"):
                            raise WebSearchError("Unsupported search content encoding")
                        body = bytearray()
                        async for chunk in response.aiter_raw(chunk_size=16384):
                            if len(body) + len(chunk) > MAX_BYTES:
                                raise WebSearchError("Search response exceeds 1 MiB")
                            body.extend(chunk)
                        return decode_response(
                            bytes(body), response.headers.get("content-type", ""),
                        )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise WebSearchError("Search timed out") from exc
        except httpx.HTTPError as exc:
            raise WebSearchError("Search transport failed") from exc
