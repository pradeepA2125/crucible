"""Exercise the actual chat delivery boundary without model or network calls."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from agentd.chat.controller_prompts import build_controller_step_payload
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


class _RecordingEngine(ScriptedReasoningEngine):
    def __init__(self):
        super().__init__(
            None,
            [],
            controller_step_responses=[
                {"type": "answer", "thought": "Candidate context probe", "answer": "ok",
                 "recap": "Answered the current request."}
                for _ in range(8)
            ],
        )
        self.payloads = []

    async def create_controller_step(
        self, plan_context, history, tool_definitions, *, phase, **kwargs
    ):
        self.payloads.append(
            copy.deepcopy(
                build_controller_step_payload(plan_context, history, tool_definitions, phase=phase)
            )
        )
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, **kwargs
        )


def _workspace(root: Path) -> None:
    nodes = []
    for name in ("auth", "invoice"):
        (root / f"{name}.py").write_text(f"def {name}():\n    return '{name} original'\n")
        nodes.extend(
            [
                {
                    "id": f"file:{name}.py",
                    "path": f"{name}.py",
                    "name": f"{name}.py",
                    "kind": "File",
                    "line": 1,
                },
                {
                    "id": f"function:{name}",
                    "path": f"{name}.py",
                    "name": name,
                    "kind": "Function",
                    "line": 1,
                },
            ]
        )
    snapshot = root / ".crucible/index-snapshot.json"
    snapshot.parent.mkdir()
    snapshot.write_text(
        json.dumps(
            {
                "workspace_root": str(root),
                "generated_at_ms": int(time.time() * 1000),
                "graph": {"nodes": nodes, "edges": []},
                "diagnostics": [],
            }
        )
    )
