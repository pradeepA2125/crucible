"""Repository identity and the three scopes visible to one conversation."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from agentd.child_env import child_env


class ScopeKind(StrEnum):
    USER = "user"
    REPO = "repo"
    THREAD = "thread"


@dataclass(frozen=True)
class MemoryScope:
    kind: ScopeKind
    id: str

    def __post_init__(self) -> None:
        if not self.id.strip() or "\x00" in self.id:
            raise ValueError("A memory scope needs a nonempty identifier")
        if self.kind == ScopeKind.USER and self.id != "user":
            raise ValueError("The user scope identifier is 'user'")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


@dataclass(frozen=True)
class ScopeContext:
    repo_id: str
    thread_id: str

    @property
    def scopes(self) -> tuple[MemoryScope, MemoryScope, MemoryScope]:
        return (
            MemoryScope(ScopeKind.USER, "user"),
            MemoryScope(ScopeKind.REPO, self.repo_id),
            MemoryScope(ScopeKind.THREAD, self.thread_id),
        )

    @property
    def keys(self) -> tuple[str, str, str]:
        user, repo, thread = self.scopes
        return user.key, repo.key, thread.key

    def permits(self, kind: ScopeKind, scope_id: str) -> bool:
        return MemoryScope(kind, scope_id) in self.scopes


def _origin_identity(origin: str) -> str | None:
    """Normalize network origins without retaining userinfo or URL credentials."""
    origin = origin.strip()
    if not origin or any(c.isspace() for c in origin):
        return None
    if "://" not in origin:
        if re.match(r"^[A-Za-z]:[/\\]", origin) or origin.split(":", 1)[0].lower() in {
            "http",
            "https",
            "ssh",
            "git",
            "file",
        }:
            return None
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", origin)
        if not match:
            return None
        origin = f"ssh://{match[1]}/{match[2]}"
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"https", "http", "ssh", "git"} or not parsed.hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    defaults = {"https": 443, "http": 80, "ssh": 22, "git": 9418}
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != defaults[parsed.scheme]:
        host += f":{port}"
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return f"git:{host}/{path}"


def resolve_repo_identity(origin: str | None, root_commits: tuple[str, ...], checkout: Path) -> str:
    """Pure normalization; callers collect git state separately, once at startup."""
    remote = _origin_identity(origin) if origin else None
    if remote:
        return remote
    roots = sorted({r.lower() for r in root_commits if re.fullmatch(r"[\da-fA-F]{40,64}", r)})
    if roots:
        return "root:" + ",".join(roots)
    return f"path:{checkout.absolute()}"


def discover_repo_identity(checkout: Path) -> str:
    """Bounded, read-only git inspection for backend construction."""

    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(checkout), *args],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                env=child_env(),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    origin = git("config", "--get", "remote.origin.url")
    roots = (
        ()
        if _origin_identity(origin)
        else tuple(git("rev-list", "--max-parents=0", "HEAD").split())
    )
    return resolve_repo_identity(origin, roots, checkout)
