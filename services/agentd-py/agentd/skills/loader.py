"""mtime-cached discovery + frontmatter parse for `.crucible/skills/*/SKILL.md`.

Mirrors instructions/loader.py: a cheap NOOP when the skills dir has not moved,
a single re-scan when it has. Best-effort — a malformed skill is skipped with a
warning, never raising into a turn.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import yaml

from agentd.skills.models import SkillManifest

logger = logging.getLogger(__name__)

_NAME_MAX = 64
_DESC_MAX = 1024


def _disabled_names() -> frozenset[str]:
    # Read per call, AFTER the mtime-cached scan — the cache stays name-agnostic
    # and a toggle (new env at next managed spawn) needs no cache invalidation.
    raw = os.getenv("CRUCIBLE_SKILLS_DISABLED", "")
    return frozenset(n.strip() for n in raw.split(",") if n.strip())


class SkillCatalogLoader:
    SKILLS_SUBDIR = Path(".crucible") / "skills"

    def __init__(self, workspace_path: Path | str) -> None:
        self._root = Path(workspace_path) / self.SKILLS_SUBDIR
        self._lock = threading.Lock()
        self._cached_mtime_ns: int | None = None
        self._cached: list[SkillManifest] | None = None

    def load_catalog(self) -> list[SkillManifest]:
        disabled = _disabled_names()
        with self._lock:
            try:
                mtime_ns = self._root.stat().st_mtime_ns
            except (FileNotFoundError, NotADirectoryError):
                self._cached_mtime_ns = None
                self._cached = []
                return self._cached
            except OSError as exc:
                logger.warning("[skills] cannot stat %s: %s", self._root, exc)
                catalog = self._cached if self._cached is not None else []
                return self._filter(catalog, disabled)

            if self._cached_mtime_ns != mtime_ns or self._cached is None:
                self._cached = self._scan()
                self._cached_mtime_ns = mtime_ns
            return self._filter(self._cached, disabled)

    @staticmethod
    def _filter(
        catalog: list[SkillManifest], disabled: frozenset[str]
    ) -> list[SkillManifest]:
        # No filter active → hand back the cached list itself (identity is the
        # documented no-re-scan signal for callers and tests).
        if not disabled:
            return catalog
        return [m for m in catalog if m.name not in disabled]

    def _scan(self) -> list[SkillManifest]:
        out: list[SkillManifest] = []
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            manifest = self._parse(child)
            if manifest is not None:
                out.append(manifest)
        out.sort(key=lambda m: m.name)
        return out

    def _parse(self, skill_dir: Path) -> SkillManifest | None:
        body_path = skill_dir / "SKILL.md"
        try:
            text = body_path.read_text(encoding="utf-8")
        except OSError:
            return None
        front = self._frontmatter(text)
        if front is None:
            logger.warning("[skills] %s: missing/invalid YAML frontmatter", body_path)
            return None
        name = front.get("name")
        description = front.get("description")
        if not isinstance(name, str) or not name.strip():
            logger.warning("[skills] %s: missing 'name'", body_path)
            return None
        if not isinstance(description, str) or not description.strip():
            logger.warning("[skills] %s: missing 'description'", body_path)
            return None
        name = name.strip()[:_NAME_MAX]
        description = description.strip()[:_DESC_MAX]
        if name != skill_dir.name:
            logger.warning(
                "[skills] %s: name %r does not match folder %r",
                body_path,
                name,
                skill_dir.name,
            )
        return SkillManifest(
            name=name, description=description, body_path=body_path, dir=skill_dir
        )

    @staticmethod
    def _frontmatter(text: str) -> dict[str, object] | None:
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        try:
            data = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            return None
        return data if isinstance(data, dict) else None
