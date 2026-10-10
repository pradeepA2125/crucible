#!/usr/bin/env python3
"""Matched controller benchmark: real commands, simulated model/web delays; no API calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/agentd-py"))
from agentd.chat.controller_loop import ControllerLoop  # noqa: E402
from agentd.chat.controller_phase import ControllerPhaseSM  # noqa: E402
from agentd.orchestrator.broadcaster import EventBroadcaster  # noqa: E402
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine  # noqa: E402
from agentd.tools.registry import ToolDefinition, ToolOutput  # noqa: E402
from agentd.tools.sources import (  # noqa: E402
    AggregatingToolRegistry,
    BuiltinToolSource,
)


class DelayedEngine(ScriptedReasoningEngine):
    requests = 0

    async def create_controller_step(self, *args, **kwargs):
        self.requests += 1
        await asyncio.sleep(0.05)
        return await super().create_controller_step(*args, **kwargs)


class SimulatedWeb:
    def definitions(self):
        return [
            ToolDefinition(
                name="web_fetch",
                description="Benchmark simulated fetch",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            )
        ]

    def owns(self, tool):
        return tool == "web_fetch"

    async def execute(self, tool, args):
        await asyncio.sleep(0.1)
        return ToolOutput(
            "Authoritative benchmark fixture; simulated network response."
        )


async def scenario(root, batched, dependent):
    os.environ["CRUCIBLE_TOOL_BATCHING_ENABLED"] = str(int(batched))
    nodes = [
        {
            "id": name,
            "tool": "run_command",
            "execution": "parallel",
            "args": {
                "command": sys.executable,
                "args": ["-c", "import time;time.sleep(.1);print('ok')"],
                "cwd": name,
            },
        }
        for name in ("one", "two")
    ]
    nodes.append(
        {"id": "web", "tool": "web_fetch", "args": {"url": "https://example.invalid"}}
    )
    if dependent:
        nodes[1]["depends_on"] = ["one"]
        nodes[2]["depends_on"] = ["two"]
    responses = (
        [{"type": "tool_calls", "calls": nodes}]
        if batched
        else [
            {"type": "tool_call", "tool": n["tool"], "args": n["args"]} for n in nodes
        ]
    )
    engine = DelayedEngine(
        None,
        [],
        controller_step_responses=[*responses, {"type": "answer", "answer": "done"}],
    )
    registry = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=root, real_workspace_path=root), SimulatedWeb()]
    )
    loop = ControllerLoop(
        engine,
        registry,
        EventBroadcaster(),
        channel_id="benchmark",
        phase_sm=ControllerPhaseSM(),
    )
    start = time.perf_counter()
    outcome = await loop.run(
        {"goal": "Run fixtures", "workspace_path": str(root)}, max_iters=8
    )
    elapsed = time.perf_counter() - start
    results = [h for h in outcome.history if h["role"] == "tool_result"]
    if len(results) != 3 or any(h.get("is_error") for h in results):
        raise RuntimeError("Benchmark did not execute all three tools successfully")
    return {
        "wall_seconds": elapsed,
        "model_requests": engine.requests,
        "tool_results": len(results),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = os.environ.get("CRUCIBLE_TOOL_BATCHING_ENABLED")
    rows = {}
    try:
        with tempfile.TemporaryDirectory(prefix="crucible-latency-") as tmp:
            root = Path(tmp)
            for name in ("one", "two"):
                (root / name).mkdir()
            for dependent in (False, True):
                for batched in (False, True):
                    name = ("dependent" if dependent else "independent") + (
                        "_batch" if batched else "_serial"
                    )
                    runs = [
                        await scenario(root, batched, dependent)
                        for _ in range(args.repeats)
                    ]
                    walls = [r["wall_seconds"] for r in runs]
                    rows[name] = {
                        "median_seconds": statistics.median(walls),
                        "min_seconds": min(walls),
                        "max_seconds": max(walls),
                        "runs": runs,
                    }
    finally:
        if original is None:
            os.environ.pop("CRUCIBLE_TOOL_BATCHING_ENABLED", None)
        else:
            os.environ["CRUCIBLE_TOOL_BATCHING_ENABLED"] = original
    report = {
        "method": "Real command processes; fixed 50ms model and 100ms web delays. No live provider/cache claim.",
        "scenarios": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: {p: v for p, v in row.items() if p != "runs"}
                for k, row in rows.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
