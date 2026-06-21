"""TurnEditSession — one ACID shadow per chat-controller turn.

Each apply() patches the turn-shadow; accept() instant-promotes the touched files
to the real workspace; reject() restores them in the shadow from real so the
`shadow == real` invariant holds at every patch boundary (real is therefore the
clean "before" for the next patch). The shadow is created lazily on the first edit
and discarded at turn end.
"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from agentd.domain.models import DiffEntry
from agentd.patch.diffing import compute_diff_entries
from agentd.patch.engine import PatchEngine
from agentd.patch.inline_apply import apply_ops
from agentd.workspace.promote import promote_files
from agentd.workspace.shadow import ShadowWorkspaceManager


def _validate_patch_ops(patch_ops: list[dict[str, object]]) -> None:
    """Reject structurally malformed ops BEFORE any file is touched.

    Weak models (qwen3-class) frequently swap the 'file' and 'content' fields —
    putting the file body in 'file'. POSIX allows newlines in filenames, so an
    unvalidated op would create a garbage-named file and (auto-accept) promote it
    to the real workspace under a false "success". Raising here routes the failure
    back through the loop's PATCH FAILED branch with actionable guidance instead.
    """
    if not patch_ops:
        raise ValueError("no patch_ops were emitted — emit at least one op or submit_changes.")
    for op in patch_ops:
        if not isinstance(op, dict):
            raise ValueError(f"each patch op must be a JSON object, got {type(op).__name__}.")
        file = op.get("file")
        if not isinstance(file, str) or not file.strip():
            raise ValueError(
                "each patch op needs a 'file': a workspace-relative path string. "
                "The code/text belongs in 'content' (or 'search'/'replace'), NOT in 'file'."
            )
        if any(c in file for c in ("\n", "\r", "\x00")) or len(file) > 255:
            raise ValueError(
                "'file' must be a single-line workspace-relative PATH, not code — you put the "
                "file body in 'file'. Put the path in 'file' and the code in 'content'."
            )
        path = PurePosixPath(file)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"'file' must be a workspace-relative path inside the workspace (got {file!r})."
            )


class TurnEditSession:
    def __init__(
        self,
        *,
        turn_id: str,
        real_path: Path,
        workspace_manager: ShadowWorkspaceManager,
        patch_engine: PatchEngine,
    ) -> None:
        self._turn_id = turn_id
        self._real = real_path
        self._wm = workspace_manager
        self._patch = patch_engine
        self._shadow: Path | None = None
        self._touched_ever: set[str] = set()  # files the shadow has ever held this turn
        self._pending_touched: list[str] = []

    async def _ensure_shadow(self, touched: list[str]) -> Path:
        if self._shadow is None:
            sw = await self._wm.prepare_lightweight(
                f"chatturn-{self._turn_id}", str(self._real), touched
            )
            self._shadow = Path(sw.shadow_path)
        else:
            # Seed any newly-touched existing file into the lightweight shadow from real,
            # so apply_ops patches current content (real is the clean before-state).
            for rel in touched:
                if rel not in self._touched_ever and (self._real / rel).exists():
                    dst = self._shadow / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self._real / rel, dst)
        self._touched_ever.update(touched)
        return self._shadow

    async def apply(self, patch_ops: list[dict[str, object]]) -> list[DiffEntry]:
        _validate_patch_ops(patch_ops)
        touched = [str(op["file"]) for op in patch_ops if "file" in op]
        shadow = await self._ensure_shadow(touched)
        applied = await apply_ops(self._patch, shadow, patch_ops, allowed_files=set(touched))
        self._pending_touched = applied
        return compute_diff_entries(self._real, shadow, applied, self._turn_id)

    async def accept(self) -> None:
        assert self._shadow is not None
        promote_files(self._shadow, self._real, self._pending_touched)
        self._pending_touched = []

    async def reject(self) -> None:
        assert self._shadow is not None
        for rel in self._pending_touched:
            real_f, shadow_f = self._real / rel, self._shadow / rel
            if real_f.exists():
                shadow_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(real_f, shadow_f)  # modified/deleted → restore from real
            elif shadow_f.exists():
                shadow_f.unlink()  # created → drop
        self._pending_touched = []

    async def close(self) -> None:
        if self._shadow is not None:
            shutil.rmtree(self._shadow, ignore_errors=True)
            self._shadow = None
