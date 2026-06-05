"""read_env_profile tool — exposes the workspace EnvProfile to the agent."""
from __future__ import annotations

from pathlib import Path

from agentd.env.profile_store import EnvProfileStore
from agentd.tools.registry import ToolOutput


async def read_env_profile(*, real_workspace: Path) -> ToolOutput:
    profile = EnvProfileStore().read(real_workspace)
    if profile is None:
        return ToolOutput(
            output="profile not yet built; proceed without it",
            is_error=False,
        )
    return ToolOutput(output=profile.model_dump_json(indent=2), is_error=False)
