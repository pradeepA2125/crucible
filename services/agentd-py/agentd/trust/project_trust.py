"""Trust for repository files that change Crucible's behavior (spec §4.1).

A cloned repository can carry AGENTS.md, skills, an MCP config and approval stores. Each counts
only once the user trusted its exact content. Records live in ~/.crucible/trust.json under a
top-level "project" key, outside the repository and unreadable inside the sandbox, so a
repository can't mark itself trusted. Every fingerprint ever trusted is kept, so reverting to a
reviewed version needs no new click.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from agentd.trust.config import is_injection_defense_enabled

TrustKind = Literal[
    "agents_md", "skills", "mcp", "approved_commands", "approved_mcp_tools",
    "approved_web_fetch", "approved_web_search",
]
TrustState = Literal["absent", "trusted", "untrusted"]

KIND_PATHS: dict[TrustKind, str] = {
    "agents_md": "AGENTS.md",
    "skills": ".crucible/skills",
    "mcp": ".crucible/mcp.json",
    "approved_commands": ".crucible/approved-commands.json",
    "approved_mcp_tools": ".crucible/approved-mcp-tools.json",
    "approved_web_fetch": ".crucible/approved-web-fetch.json",
    "approved_web_search": ".crucible/approved-web-search.json",
}
ALL_KINDS: tuple[TrustKind, ...] = get_args(TrustKind)

# Larger files can't be reviewed in the trust dialog, so they are never trusted.
MAX_REVIEW_BYTES = 64 * 1024
_PROJECT_KEY = "project"


class TrustMismatch(Exception):
    """The file changed between the user reviewing it and the trust request."""


class TrustRefused(Exception):
    """The file can't be trusted at all (a symlink, or too large to review)."""


@dataclass(frozen=True)
class TrustView:
    kind: TrustKind
    state: TrustState
    sha256: str | None
    content: str | None
    diff_base: str | None


def kind_for_path(workspace: Path, path: Path) -> TrustKind | None:
    """The kind a file belongs to, or None. Only top-level `SKILL.md` files count for skills;
    a skill's scripts run through the command gate, not the trust gate."""
    root = Path(os.path.realpath(workspace))
    try:
        rel = Path(os.path.realpath(path)).relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) == 4 and parts[:2] == (".crucible", "skills") and parts[3] == "SKILL.md":
        return "skills"
    text = rel.as_posix()
    for kind, rel_path in KIND_PATHS.items():
        if kind != "skills" and text == rel_path:
            return kind
    return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ProjectTrust:
    def __init__(self, workspace: Path, home: Path | None = None) -> None:
        self._workspace = Path(os.path.realpath(workspace))
        self._key = str(self._workspace)
        root = (home or Path.home()) / ".crucible"
        self._store = root / "trust.json"
        self._lock_path = root / "trust.json.lock"
        self._copies = root / "trusted-files" / _sha(self._key.encode())[:16]
        self._thread_lock = threading.Lock()

    # -- reading the workspace -------------------------------------------------------------

    def _skill_files(self) -> list[tuple[str, Path]]:
        root = self._workspace / KIND_PATHS["skills"]
        if not root.is_dir():
            return []
        return sorted(
            (path.relative_to(self._workspace).as_posix(), path)
            for path in root.glob("*/SKILL.md")
            if path.is_file()
        )

    def _read_bytes(self, kind: TrustKind) -> bytes | None:
        """The content the fingerprint covers, or None when absent. A symlinked file reads as
        its target, which is why trust() refuses it."""
        if kind == "skills":
            files = self._skill_files()
            if not files:
                return None
            return b"".join(
                f"--- {rel}\n".encode() + path.read_bytes() + b"\n" for rel, path in files
            )
        path = self._workspace / KIND_PATHS[kind]
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError:
            return None

    def fingerprint(self, kind: TrustKind) -> str | None:
        if kind == "skills":
            files = self._skill_files()
            if not files:
                return None
            pairs = [f"{rel}\0{_sha(path.read_bytes())}" for rel, path in files]
            return _sha("\n".join(pairs).encode())
        data = self._read_bytes(kind)
        return None if data is None else _sha(data)

    def _check_reviewable(self, kind: TrustKind) -> None:
        if kind == "skills":
            paths = [path for _rel, path in self._skill_files()]
        else:
            paths = [self._workspace / KIND_PATHS[kind]]
        for path in paths:
            if path.is_symlink():
                raise TrustRefused(
                    f"{path.relative_to(self._workspace)} is a symlink; replace it with a "
                    "regular file before trusting it.")
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_REVIEW_BYTES:
                raise TrustRefused(
                    f"{path.relative_to(self._workspace)} is too large to review "
                    f"({size} bytes, limit {MAX_REVIEW_BYTES}); shorten it first.")

    # -- the store -------------------------------------------------------------------------

    def _read_all(self) -> dict[str, object]:
        try:
            data = json.loads(self._store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_all(self, data: dict[str, object]) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._store.parent, prefix=".trust-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, self._store)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # The flock covers other backends (one per workspace) sharing trust.json; the thread
        # lock covers threads in this process, since flock is per open file description.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, open(self._lock_path, "a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _trusted_shas(self, kind: TrustKind) -> list[str]:
        project = self._read_all().get(_PROJECT_KEY)
        if not isinstance(project, dict):
            return []
        entry = project.get(self._key)
        shas = entry.get(kind) if isinstance(entry, dict) else None
        return [s for s in shas if isinstance(s, str)] if isinstance(shas, list) else []

    def _add_trusted(self, kind: TrustKind, sha: str, content: bytes) -> None:
        with self._locked():
            data = self._read_all()
            project = data.get(_PROJECT_KEY)
            if not isinstance(project, dict):
                project = {}
                data[_PROJECT_KEY] = project
            entry = project.setdefault(self._key, {})
            shas = entry.setdefault(kind, [])
            if sha not in shas:
                shas.append(sha)
            self._write_all(data)
        self._copies.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._copies, prefix=".copy-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp, self._copies / kind)

    # -- public API ------------------------------------------------------------------------

    def state(self, kind: TrustKind) -> TrustState:
        sha = self.fingerprint(kind)
        if sha is None:
            return "absent"
        return "trusted" if sha in self._trusted_shas(kind) else "untrusted"

    def is_effective(self, kind: TrustKind) -> bool:
        return not is_injection_defense_enabled() or self.state(kind) == "trusted"

    def trust(self, kind: TrustKind, sha256: str) -> None:
        self._check_reviewable(kind)
        current = self.fingerprint(kind)
        if current is None or current != sha256:
            raise TrustMismatch(
                f"{KIND_PATHS[kind]} changed since you reviewed it; review it again.")
        content = self._read_bytes(kind)
        if content is None:
            raise TrustMismatch(
                f"{KIND_PATHS[kind]} changed since you reviewed it; review it again.")
        self._add_trusted(kind, current, content)

    def record_own_write(self, kind: TrustKind, before: str | None) -> bool:
        """Trust a file the user (or a reviewed edit) just wrote, but only when what it
        replaced was absent or already trusted: an edit on top of planted content must not
        launder it. With the defense off nothing is recorded: everything counts anyway, and
        the switch promises today's behavior exactly."""
        if not is_injection_defense_enabled():
            return True
        if before is not None and before not in self._trusted_shas(kind):
            return False
        current = self.fingerprint(kind)
        content = self._read_bytes(kind)
        if current is None or content is None:
            return False
        try:
            self._check_reviewable(kind)
        except TrustRefused:
            return False
        self._add_trusted(kind, current, content)
        return True

    def view(self, kind: TrustKind) -> TrustView:
        sha = self.fingerprint(kind)
        state = self.state(kind)
        content = self._read_bytes(kind)
        try:
            base = (self._copies / kind).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            base = None
        return TrustView(
            kind=kind,
            state=state,
            sha256=sha,
            content=None if content is None else content.decode("utf-8", errors="replace"),
            diff_base=base,
        )


_INSTANCES: dict[str, ProjectTrust] = {}
_INSTANCES_LOCK = threading.Lock()


def project_trust_for(workspace: Path) -> ProjectTrust:
    """One instance per workspace (by realpath) per process, so its thread lock is shared."""
    key = os.path.realpath(workspace)
    with _INSTANCES_LOCK:
        if key not in _INSTANCES:
            _INSTANCES[key] = ProjectTrust(Path(key))
        return _INSTANCES[key]
