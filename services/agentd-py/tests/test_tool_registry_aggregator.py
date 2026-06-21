import pytest

from agentd.tools.registry import ToolDefinition, ToolOutput
from agentd.tools.sources import AggregatingToolRegistry


class _FakeSource:
    name = "fake"

    def definitions(self):
        return [ToolDefinition(name="fake__ping", description="p",
                               parameters={"type": "object", "properties": {}})]

    def owns(self, tool):
        return tool == "fake__ping"

    async def execute(self, tool, args):
        return ToolOutput(output="pong")


@pytest.mark.asyncio
async def test_aggregator_concats_routes_and_rejects_collision():
    reg = AggregatingToolRegistry([_FakeSource()])
    assert [d.name for d in reg.definitions()] == ["fake__ping"]
    out = await reg.execute("fake__ping", {})
    assert out.output == "pong"
    out2 = await reg.execute("unknown", {})
    assert out2.is_error is True
    with pytest.raises(ValueError):
        AggregatingToolRegistry([_FakeSource(), _FakeSource()])  # duplicate name
