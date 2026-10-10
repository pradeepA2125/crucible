import asyncio

import pytest

from agentd.domain.models import ApprovalOutcome
from agentd.tools.sources import AggregatingToolRegistry
from agentd.web.config import WebSearchRuleStore, is_web_search_enabled
from agentd.web.exa import WebSearchError
from agentd.web.tool_source import NOTICE, PREFIX, WebSearchToolSource, bounded_output


class Client:
    def __init__(self, text="Title: Docs\nURL: https://example.com", error=None):
        self.calls = []
        self.text = text
        self.error = error

    async def search(self, query, max_results):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.text


@pytest.mark.asyncio
async def test_contract_and_approval_before_network():
    client = Client()
    approvals = []

    async def approve(server, tool, args):
        assert not client.calls
        approvals.append((server, tool, args))
        return ApprovalOutcome.allow()

    source = WebSearchToolSource(client, approve, enabled=lambda: True)
    definition, = source.definitions()
    assert definition.name == "web_search"
    assert definition.parameters["additionalProperties"] is False
    assert "model training" in definition.description
    out = await source.execute("web_search", {"query": "  public  docs \n"})
    assert not out.is_error and out.output.startswith(PREFIX)
    assert approvals == [("Exa", "web_search", {"query": "public  docs", "max_results": 5})]
    assert client.calls == [("public  docs", 5)]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {}, {"query": None}, {"query": ""}, {"query": " \n"}, {"query": "x" * 2001},
    *[{"query": "docs", "max_results": n} for n in (True, 0, 11, 1.2, "5", None)],
    {"query": "docs", "endpoint": "https://other.example"},
])
async def test_invalid_arguments_never_approve_or_search(args):
    client = Client()

    async def approve(*args):
        pytest.fail("invalid calls must not ask")

    source = WebSearchToolSource(client, approve, enabled=lambda: True)
    out = await source.execute("web_search", args)
    assert out.is_error and not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["user", "policy", "timeout"])
async def test_denial_never_searches(reason):
    client = Client()

    async def deny(*args):
        return ApprovalOutcome.deny(reason)

    source = WebSearchToolSource(client, deny, enabled=lambda: True)
    out = await source.execute("web_search", {"query": "docs"})
    assert out.is_error and not client.calls
    if reason != "user":
        assert "rejected" not in out.output.lower()


@pytest.mark.asyncio
async def test_disabled_unknown_and_registry_filter_never_ask():
    client = Client()

    async def approve(*args):
        pytest.fail("unavailable calls must not ask")

    source = WebSearchToolSource(client, approve, enabled=lambda: False)
    assert source.definitions() == []
    assert (await source.execute("web_search", {"query": "docs"})).is_error
    source = WebSearchToolSource(client, approve, enabled=lambda: True)
    assert (await source.execute("other", {"query": "docs"})).is_error
    registry = AggregatingToolRegistry([source], allowed_tools=frozenset())
    assert registry.definitions() == []
    assert (await registry.execute("web_search", {"query": "docs"})).is_error
    assert not client.calls


@pytest.mark.asyncio
async def test_disable_while_approval_pending():
    client = Client()
    enabled = True
    ready, release = asyncio.Event(), asyncio.Event()

    async def approve(*args):
        ready.set()
        await release.wait()
        return ApprovalOutcome.allow()

    source = WebSearchToolSource(client, approve, enabled=lambda: enabled)
    task = asyncio.create_task(source.execute("web_search", {"query": "docs"}))
    await ready.wait()
    enabled = False
    release.set()
    assert (await task).is_error and not client.calls


@pytest.mark.parametrize("length", [0, 12000 - len(PREFIX), 12001, 100000])
def test_output_limit_includes_wrapper_and_notice(length):
    out = bounded_output("x" * length)
    assert len(out) <= 12000 and out.startswith(PREFIX)
    assert out.endswith(NOTICE) == (length > 12000 - len(PREFIX))


def test_output_prefers_result_boundary():
    text = "first" + "\nTitle:" + "x" * 12000
    assert bounded_output(text) == PREFIX + "first" + NOTICE


@pytest.mark.asyncio
async def test_operational_error_and_cancellation():
    async def approve(*args):
        return ApprovalOutcome.allow()

    client = Client(error=WebSearchError("Search timed out"))
    source = WebSearchToolSource(client, approve, enabled=lambda: True)
    out = await source.execute("web_search", {"query": "docs"})
    assert out.is_error and "timed out" in out.output
    client.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await source.execute("web_search", {"query": "docs"})


@pytest.mark.parametrize("value,expected", [
    (None, False), ("0", False), ("1", True), (" true ", True),
    ("yes", True), ("on", True), ("false", False),
])
def test_flag_defaults_off(monkeypatch, value, expected):
    monkeypatch.delenv("CRUCIBLE_WEB_SEARCH_ENABLED", raising=False)
    if value is not None:
        monkeypatch.setenv("CRUCIBLE_WEB_SEARCH_ENABLED", value)
    assert is_web_search_enabled() is expected


def test_native_rules_use_separate_file(tmp_path):
    from agentd.mcp.rules import McpRuleStore

    McpRuleStore(tmp_path).add("Exa", "web_search")
    native = WebSearchRuleStore(tmp_path)
    assert not native.matches("Exa", "web_search")
    native.add("Exa", "web_search")
    assert native.matches("Exa", "web_search")
    assert (tmp_path / ".crucible/approved-web-search.json").exists()
