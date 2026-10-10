import asyncio

import pytest

from agentd.domain.models import ApprovalOutcome
from agentd.tools.sources import AggregatingToolRegistry
from agentd.web.config import WebFetchRuleStore, is_web_fetch_enabled
from agentd.web.fetch import FetchError, FetchResult
from agentd.web.fetch_source import NOTICE, WebFetchToolSource


class Client:
    def __init__(self, content="Page content", error=None):
        self.calls = []
        self.content = content
        self.error = error

    async def fetch(self, url, format="markdown", timeout=30):
        self.calls.append((url, format, timeout))
        if self.error:
            raise self.error
        return FetchResult(url, "text/html", self.content)


@pytest.mark.asyncio
async def test_defaults_and_permission_before_network():
    client = Client()
    approvals = []

    async def approve(*args):
        assert not client.calls
        approvals.append(args)
        return ApprovalOutcome.allow()

    source = WebFetchToolSource(client, approve, enabled=lambda: True)
    assert [d.name for d in source.definitions()] == ["web_fetch"]
    out = await source.execute("web_fetch", {"url": " https://example.com/#a "})
    assert not out.is_error and "Untrusted external page content" in out.output
    assert approvals == [
        ("Web", "web_fetch", {"url": "https://example.com/", "format": "markdown", "timeout": 30})
    ]
    assert client.calls == [("https://example.com/", "markdown", 30)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {},
        {"url": None},
        {"url": ""},
        {"url": "http://localhost/"},
        {"url": "file:///etc/passwd"},
        {"url": "https://user:pass@example.com"},
        {"url": "https://example.com", "format": "pdf"},
        {"url": "https://example.com", "format": []},
        {"url": "https://example.com", "headers": {"Authorization": "secret"}},
        *[
            {"url": "https://example.com", "timeout": t}
            for t in [True, "30", 0, -1, 121, float("nan"), float("inf")]
        ],
    ],
)
async def test_invalid_args_never_ask_or_fetch(args):
    client = Client()

    async def forbidden(*args):
        pytest.fail("invalid call must not ask")

    source = WebFetchToolSource(client, forbidden, enabled=lambda: True)
    assert (await source.execute("web_fetch", args)).is_error
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["user", "policy", "timeout"])
async def test_denied_call_never_fetches(reason):
    client = Client()

    async def deny(*args):
        return ApprovalOutcome.deny(reason)

    source = WebFetchToolSource(client, deny, enabled=lambda: True)
    assert (await source.execute("web_fetch", {"url": "https://example.com"})).is_error
    assert not client.calls


@pytest.mark.asyncio
async def test_unavailable_and_registry_allow_list():
    client = Client()

    async def forbidden(*args):
        pytest.fail("unavailable call must not ask")

    source = WebFetchToolSource(client, forbidden, enabled=lambda: False)
    assert source.definitions() == []
    assert (await source.execute("web_fetch", {"url": "https://example.com"})).is_error
    source = WebFetchToolSource(client, forbidden, enabled=lambda: True)
    assert (await source.execute("other", {})).is_error
    reg = AggregatingToolRegistry([source], allowed_tools=frozenset())
    assert reg.definitions() == []
    assert (await reg.execute("web_fetch", {"url": "https://example.com"})).is_error
    assert not client.calls


@pytest.mark.asyncio
async def test_disable_pending_approval_and_output_limit():
    client = Client(content="x" * 50000)
    enabled = True

    async def approve(*args):
        nonlocal enabled
        enabled = False
        return ApprovalOutcome.allow()

    source = WebFetchToolSource(client, approve, enabled=lambda: enabled)
    assert (await source.execute("web_fetch", {"url": "https://example.com"})).is_error
    assert not client.calls
    enabled = True

    async def allow(*args):
        return ApprovalOutcome.allow()

    source = WebFetchToolSource(client, allow, enabled=lambda: enabled)
    out = await source.execute(
        "web_fetch", {"url": "https://example.com", "format": "html", "timeout": 120}
    )
    assert not out.is_error and len(out.output) == 12000 and out.output.endswith(NOTICE)
    assert client.calls == [("https://example.com", "html", 120)]


@pytest.mark.asyncio
async def test_errors_and_cancellation():
    async def allow(*args):
        return ApprovalOutcome.allow()

    client = Client(error=FetchError("Fetch timed out"))
    source = WebFetchToolSource(client, allow, enabled=lambda: True)
    assert (await source.execute("web_fetch", {"url": "https://example.com"})).is_error
    client.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await source.execute("web_fetch", {"url": "https://example.com"})


def test_default_on_and_rule_file_isolation(tmp_path, monkeypatch):
    from agentd.mcp.rules import McpRuleStore
    from agentd.web.config import WebSearchRuleStore

    monkeypatch.delenv("CRUCIBLE_WEB_FETCH_ENABLED", raising=False)
    assert is_web_fetch_enabled()
    monkeypatch.setenv("CRUCIBLE_WEB_FETCH_ENABLED", "0")
    assert not is_web_fetch_enabled()
    WebFetchRuleStore(tmp_path).add("Web", "web_fetch")
    assert WebFetchRuleStore(tmp_path).matches("Web", "web_fetch")
    assert not McpRuleStore(tmp_path).matches("Web", "web_fetch")
    assert not WebSearchRuleStore(tmp_path).matches("Web", "web_fetch")
