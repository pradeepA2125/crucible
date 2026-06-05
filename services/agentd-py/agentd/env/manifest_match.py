"""Resolve a (just-touched manifest file → ecosystem scope_key) pure function.

Called from ToolLoop after a successful emit_patch: if any file in
PatchResult.touched_files matches an EnvEcosystemEntry.manifest_path, the
loop sets pending_install_for_scope to that entry's scope_key so the next
run_command is preceded by an install.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from agentd.domain.models import EnvProfile

# Manifest basenames we care about for auto-sync.
_MANIFEST_BASENAMES = frozenset({"pyproject.toml", "package.json", "Cargo.toml", "go.mod"})


def _normalize(path: str) -> str:
    """Strip a leading './' so 'pyproject.toml' and './pyproject.toml' match."""
    return str(PurePosixPath(path))


def resolve_manifest_scope_key(
    touched_files: Iterable[str],
    profile: EnvProfile,
) -> str | None:
    """Return the scope_key of the first ecosystem-scope whose manifest_path is in
    touched_files. None if no touched file matches any scope's manifest.

    Conservative on multi-manifest patches: returns the first match in the order
    touched_files is iterated. Only one install fires per (next) run_command;
    a later run_command will trigger the second scope's install on its own
    next emit_patch.
    """
    by_manifest = {entry.manifest_path: entry.scope_key for entry in profile.ecosystems}
    for raw in touched_files:
        norm = _normalize(raw)
        if PurePosixPath(norm).name not in _MANIFEST_BASENAMES:
            continue
        if norm in by_manifest:
            return by_manifest[norm]
    return None
