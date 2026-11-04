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
from agentd.trust.config import is_injection_defense_enabled
from agentd.trust.project_trust import ProjectTrust, project_trust_for

logger = logging.getLogger(__name__)

UNTRUSTED_SKILLS_TEXT = "This project's skills aren't trusted yet. Ask the user to review them."

_NAME_MAX = 64
_DESC_MAX = 1024


def _disabled_names() -> frozenset[str]:
    # Read per call, AFTER the mtime-cached scan — the cache stays name-agnostic
    # and a toggle (new env at next managed spawn) needs no cache invalidation.
    raw = os.getenv("CRUCIBLE_SKILLS_DISABLED", "")
    return frozenset(n.strip() for n in raw.split(",") if n.strip())


class SkillCatalogLoader:
    SKILLS_SUBDIR = Path(".crucible") / "skills"

    def __init__(self, workspace_path: Path | str, trust: ProjectTrust | None = None) -> None:
        self._root = Path(workspace_path) / self.SKILLS_SUBDIR
        self._trust = trust or project_trust_for(Path(workspace_path))
        self._lock = threading.Lock()
        self._cached_mtime_ns: int | None = None
        self._cached_fingerprint: str | None = None
        self._cached: list[SkillManifest] | None = None

    def is_trusted(self) -> bool:
        return self._trust.is_effective("skills")

    def load_catalog(self) -> list[SkillManifest]:
        """The skills the agent may use: none until the user trusted this project's skills
        (prompt-injection Phase 1, spec §4.1). One fingerprint is both checked and used as
        the cache key, so a catalog parsed from other content is never served."""
        if not is_injection_defense_enabled():
            return self._load(None)
        fingerprint = self._trust.fingerprint("skills")
        if not self._trust.fingerprint_trusted("skills", fingerprint):
            return []
        return self._load(fingerprint)

    def load_untrusted(self) -> list[SkillManifest]:
        """Every parsed skill regardless of trust, for listings that mark them untrusted."""
        if not is_injection_defense_enabled():
            return self._load(None)
        return self._load(self._trust.fingerprint("skills"))

    def _load(self, fingerprint: str | None) -> list[SkillManifest]:
        # The root folder's mtime misses edits to a nested SKILL.md; with the defense on the
        # fingerprint (over every SKILL.md's bytes) is part of the cache key.
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

            if (self._cached_mtime_ns != mtime_ns or self._cached is None
                    or self._cached_fingerprint != fingerprint):
                self._cached = self._scan()
                self._cached_mtime_ns = mtime_ns
                self._cached_fingerprint = fingerprint
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
