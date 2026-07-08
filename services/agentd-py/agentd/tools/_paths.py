"""Workspace-local binary/import resolution shared by env, shell, and validator."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_WORKSPACE_BIN_DIRS: tuple[tuple[str, ...], ...] = (
    (".venv", "bin"),
    (".venv", "Scripts"),
    ("node_modules", ".bin"),
    ("target", "release"),
    ("target", "debug"),
)

_BIN_SUFFIXES: tuple[str, ...] = ("", ".exe", ".cmd")


def resolve_workspace_bin(root: Path, name: str) -> Path | None:
    """Return the absolute path to an executable inside known workspace-local dirs.

    Probes <root>/.venv/bin, <root>/.venv/Scripts, <root>/node_modules/.bin,
    <root>/target/release, <root>/target/debug for `name` (with .exe/.cmd suffixes
    on Windows). Returns the first hit ranked in dir-order, or None.
    """
    if not name or "/" in name or "\\" in name:
        return None
    for parts in _WORKSPACE_BIN_DIRS:
        for suffix in _BIN_SUFFIXES:
            candidate = root.joinpath(*parts, name + suffix)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


# Dirs never worth walking when locating a package inside the shadow.
_WALK_SKIP_DIRS = {
    ".venv", "venv", "node_modules", "target", "dist", "build",
    ".tox", ".mypy_cache", ".pytest_cache", "__pycache__", ".crucible/state", ".git",
}


def editable_package_names() -> set[str]:
    """Top-level package names installed as *editable* in the current interpreter.

    These are the packages a user is actively developing — the ones whose shadow copy
    should take import precedence over the installed copy. Detected via PEP 610
    ``direct_url.json`` (``dir_info.editable``) and named via ``top_level.txt``, which is
    installer-agnostic (setuptools/hatch/pdm/flit) and avoids depending on any one
    backend's finder internals. Keyed on identity, so it generalizes to any editable
    package (``agentd`` here, or e.g. an editable ``cv2`` clone), not a hardcoded name.
    """
    import importlib.metadata as md
    import json

    names: set[str] = set()
    for dist in md.distributions():
        try:
            raw = dist.read_text("direct_url.json")
            if not raw or not json.loads(raw).get("dir_info", {}).get("editable"):
                continue
            top = dist.read_text("top_level.txt") or ""
        except Exception:
            continue
        names.update(line.strip() for line in top.splitlines() if line.strip())
    return names


def shadow_import_roots(shadow_root: Path, package_names: set[str]) -> list[str]:
    """Locate each named package inside the shadow; return its import root.

    The import root is the directory to place on PYTHONPATH so ``import <pkg>`` resolves
    to the shadow's edited copy. Found by package name rather than a fixed sub-path, so
    it generalizes across layouts (flat, ``src/``, monorepo subdir) — the same idea as
    ``PYTHONPATH=/path/to/local/clone`` to import your edits over an installed package.
    """
    if not package_names:
        return []
    shallowest: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(shadow_root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS and not d.startswith(".")]
        name = os.path.basename(dirpath)
        if name in package_names and "__init__.py" in filenames:
            root = os.path.dirname(dirpath)
            if name not in shallowest or len(root) < len(shallowest[name]):
                shallowest[name] = root
    return list(shallowest.values())


def shadow_pythonpath_extras(
    shadow_root: Path,
    real_workspace_path: Path | None = None,
    *,
    include_editable: bool = True,
) -> list[str]:
    """PYTHONPATH entries that make subprocesses import the shadow's copy of edited code.

    Two redirects, both meant to override an installed/real-workspace copy:
      * real-workspace ``sys.path`` entries rewritten to their shadow equivalents (only
        when ``real_workspace_path`` is known and differs from the shadow);
      * editable-installed packages located inside the shadow by name — covers installs
        whose source is OUTSIDE the workspace (e.g. agentd run via ``--agentd-dir``).
    """
    shadow_root = Path(shadow_root)
    extras: list[str] = []
    if real_workspace_path is not None and Path(real_workspace_path) != shadow_root:
        real_ws = str(real_workspace_path)
        extras += [p.replace(real_ws, str(shadow_root), 1) for p in sys.path if p.startswith(real_ws)]
    if include_editable:
        extras += shadow_import_roots(shadow_root, editable_package_names())
    return extras


def prepend_pythonpath(env: dict[str, str], extras: list[str]) -> dict[str, str]:
    """Return a copy of ``env`` with ``extras`` prepended (de-duped) onto PYTHONPATH."""
    if not extras:
        return env
    env = dict(env)
    existing = env.get("PYTHONPATH", "")
    seen: set[str] = set()
    ordered: list[str] = []
    for p in [*extras, *([existing] if existing else [])]:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    env["PYTHONPATH"] = os.pathsep.join(ordered)
    return env
