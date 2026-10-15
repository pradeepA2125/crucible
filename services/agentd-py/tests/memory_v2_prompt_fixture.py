"""A separate v2 golden; the legacy main-agent snapshot stays unchanged."""

from agentd.chat.controller_prompts import (
    build_controller_step_payload,
    controller_response_schema,
    format_controller_system_prompt,
)
from agentd.memory.scope import ScopeContext
from agentd.memory.tool_source_v2 import DurableMemoryToolSource


def collect():
    tools = [
        tool.model_dump()
        for tool in DurableMemoryToolSource(
            None,
            ScopeContext("git:example/repository", "thread"),
        ).definitions()
    ]
    return {
        "system": format_controller_system_prompt(
            tools,
            memory_enabled=True,
            memory_v2=True,
            task_subsystem_enabled=False,
        ),
        "schema": controller_response_schema(phase="ACTIVE", memory_v2=True, anyof=True),
        "payload": build_controller_step_payload(
            {
                "goal": "Review transaction behavior",
                "memory_v2": True,
                "memory_recap": "Reviewing concurrent transaction behavior",
            },
            [
                {
                    "role": "user",
                    "content": (
                        "Memory candidate catalog. Selected using keyword. May contain false "
                        "positives. [m:example@1] when: preview: Use fictional fixtures."
                    ),
                    "mseq": 12,
                    "_evidence": {"kind": "injected_memory", "memory_ids": ["example"]},
                }
            ],
            tools,
            phase="ACTIVE",
        ),
    }
