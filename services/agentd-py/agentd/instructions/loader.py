"""mtime-cached reader for a workspace AGENTS.md (project instructions).

Mirrors the GraphWalker mtime-cache discipline: a cheap NOOP when the file
has not moved, a single re-read when it has. Best-effort — any IO error
degrades to None so a controller turn is never broken by instructions.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from agentd.trust.config import is_injection_defense_enabled
from agentd.trust.project_trust import ProjectTrust, project_trust_for

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 16000


def _max_chars() -> int:
    raw = os.getenv("CRUCIBLE_INSTRUCTIONS_MAX_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_CHARS


class ProjectInstructionsLoader:
    """Reads `<workspace>/AGENTS.md`, size-capped, returns text or None.

    Thread-safe. The text is re-read only when the file's mtime changes, so
    repeated `load()` calls across turns are cheap and the returned bytes are
    identical until the user edits the file (KV-cache-friendly upstream)."""

    FILENAME = "AGENTS.md"

    def __init__(self, workspace_path: Path | str, trust: ProjectTrust | None = None) -> None:
        self._path = Path(workspace_path) / self.FILENAME
        self._trust = trust or project_trust_for(Path(workspace_path))
        self._lock = threading.Lock()
        self._cached_mtime_ns: int | None = None
        self._cached_text: str | None = None  # capped; "" means present-but-blank

    def load(self) -> str | None:
        """The text as project instructions: only once the user trusted this exact content
        (prompt-injection Phase 1, spec §4.1). A cloned repository's AGENTS.md is otherwise
        notes the agent may read, never instructions."""
        if not is_injection_defense_enabled():
            return self._read()
        data, trusted = self._trust.read_checked("agents_md")
        return self._decode(data) if trusted else None

    def load_untrusted(self) -> str | None:
        """The text while it is present but not trusted, for the payload's notes block."""
        if not is_injection_defense_enabled():
            return None
        data, trusted = self._trust.read_checked("agents_md")
        return None if trusted else self._decode(data)

    def _decode(self, data: bytes | None) -> str | None:
        # The same bytes the trust check hashed, never a cached copy of another version.
        if data is None:
            return None
        return self._nonblank(self._cap(data.decode("utf-8", errors="replace")))

    def _read(self) -> str | None:
        with self._lock:
            try:
                mtime_ns = self._path.stat().st_mtime_ns
            except (FileNotFoundError, NotADirectoryError):
                self._cached_mtime_ns = None
                self._cached_text = None
                return None
            except OSError as exc:  # permission, etc. — keep any prior text
                logger.warning("[instructions] cannot stat %s: %s", self._path, exc)
                return self._nonblank(self._cached_text)

            if self._cached_mtime_ns == mtime_ns and self._cached_text is not None:
                return self._nonblank(self._cached_text)

            try:
                text = self._path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[instructions] cannot read %s: %s", self._path, exc)
                return self._nonblank(self._cached_text)

            self._cached_text = self._cap(text)
            self._cached_mtime_ns = mtime_ns
            return self._nonblank(self._cached_text)

    @staticmethod
    def _nonblank(text: str | None) -> str | None:
        return text if (text and text.strip()) else None

    @staticmethod
    def _cap(text: str) -> str:
        limit = _max_chars()
        if len(text) <= limit:
            return text
        logger.warning(
            "[instructions] AGENTS.md exceeds %d chars; truncating (was %d)",
            limit,
            len(text),
        )
        return text[:limit] + f"\n\n[... AGENTS.md truncated at {limit} chars ...]"
