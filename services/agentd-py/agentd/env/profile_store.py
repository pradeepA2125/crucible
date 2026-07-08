"""Persist EnvProfile as JSON at <workspace>/.crucible/state/env_profile.json."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentd.domain.models import EnvProfile

_PROFILE_REL_PATH = Path(".crucible/state") / "env_profile.json"


class EnvProfileStore:
    """Read/write EnvProfile + age-based staleness check."""

    def __init__(self, *, max_age_days: int = 30) -> None:
        self._max_age = timedelta(days=max_age_days)

    @staticmethod
    def path_for(workspace: Path) -> Path:
        return workspace / _PROFILE_REL_PATH

    def read(self, workspace: Path) -> EnvProfile | None:
        p = self.path_for(workspace)
        if not p.is_file():
            return None
        try:
            return EnvProfile.model_validate_json(p.read_text())
        except (json.JSONDecodeError, ValueError):
            return None

    def write(self, workspace: Path, profile: EnvProfile) -> None:
        p = self.path_for(workspace)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(profile.model_dump_json(indent=2))

    def is_stale(self, workspace: Path) -> bool:
        p = self.path_for(workspace)
        if not p.is_file():
            return True
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime) > self._max_age
