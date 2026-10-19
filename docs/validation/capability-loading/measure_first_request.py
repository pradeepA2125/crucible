"""Measure the first-request system prompt with on-demand MCP loading off vs on."""
import json
import os
import sys
from pathlib import Path

import tiktoken

# Set before importing agentd: the recorded numbers were produced with both on.
os.environ.setdefault("CRUCIBLE_COMPACT_CONTEXT_ENABLED", "1")
os.environ.setdefault("CRUCIBLE_TOOL_BATCHING_ENABLED", "1")

from agentd.capabilities.bundle import render_bundle  # noqa: E402
from agentd.capabilities.catalog import render_directory, split_definitions  # noqa: E402
from agentd.capabilities.tool_source import LOAD_CAPABILITIES  # noqa: E402
from agentd.chat.controller_prompts import format_controller_system_prompt, mcp_bundle_guidance  # noqa: E402
from agentd.prompting.tagged import RenderContext  # noqa: E402

artifact, out = Path(sys.argv[1]), Path(sys.argv[2])
system = json.loads(artifact.read_text())["system_instructions"]
dec = json.JSONDecoder()
start = next(i for i in range(len(system)) if system.startswith('[{"description"', i)
             or system.startswith('[\n  {', i))
tools, _ = dec.raw_decode(system, start)
enc = tiktoken.get_encoding("o200k_base")
n = lambda text: len(enc.encode(text))  # noqa: E731

split = split_definitions(tools)
directory = render_directory(split.namespaces)
loader = LOAD_CAPABILITIES.model_dump()
kw = dict(task_subsystem_enabled=False, memory_enabled=False)
off = format_controller_system_prompt(tools, **kw)
on = format_controller_system_prompt(split.core + [loader], capability_directory=directory, **kw)
compact = lambda d: json.dumps(d, sort_keys=True, separators=(",", ":"))  # noqa: E731
mcp_defs = [d for defs in split.namespaces.values() for d in defs]
result = {
    "note": ("One workspace's estimate (this repo, the MCP servers connected on 2026-10-19), "
             "o200k_base tokens of the controller system prompt built with the same inputs "
             "(no AGENTS.md, skills or memory blocks). Not a billing figure."),
    "source_artifact": str(artifact.name),
    "tools_total": len(tools),
    "mcp_tools": len(mcp_defs),
    "namespaces": {ns: len(defs) for ns, defs in split.namespaces.items()},
    "flag_off": {
        "system_prompt_tokens": n(off),
        "mcp_schema_tokens": n(compact(mcp_defs)),
        "mcp_guidance_tokens": n(mcp_bundle_guidance(RenderContext.main())),
    },
    "flag_on": {
        "system_prompt_tokens": n(on),
        "loader_schema_tokens": n(compact([loader])),
        "capability_block_and_directory_tokens": n(on) - n(format_controller_system_prompt(
            split.core + [loader], **kw)),
        "directory": directory,
    },
}
result["first_request_saving_tokens"] = n(off) - n(on)
result["loading_whole_namespace_bundle_tokens"] = {
    ns: n(render_bundle(ns, defs, mcp_bundle_guidance(RenderContext.main())))
    for ns, defs in split.namespaces.items()}
result["settings"] = {k: os.environ[k] for k in (
    "CRUCIBLE_COMPACT_CONTEXT_ENABLED", "CRUCIBLE_TOOL_BATCHING_ENABLED")}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
