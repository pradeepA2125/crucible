"""TurnEditSession — one ACID shadow per chat-controller turn.

Each apply() patches the turn-shadow; accept() instant-promotes the touched files
to the real workspace; reject() restores them in the shadow from real so the
`shadow == real` invariant holds at every patch boundary (real is therefore the
clean "before" for the next patch). The shadow is created lazily on the first edit
and discarded at turn end.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from agentd.chat.protected_paths import AgentProtection, MainProtection, ProtectedPathError
from agentd.domain.models import DiffEntry, PatchFailureCode, PatchPreflightIssue
from agentd.patch.diffing import compute_diff_entries
from agentd.patch.engine import PatchEngine, PatchPreflightFailed
from agentd.patch.inline_apply import apply_ops
from agentd.subagents.write_log import WriteGuard, canonical_path, file_digest
from agentd.workspace.promote import promote_files
from agentd.workspace.shadow import ShadowWorkspaceManager

logger = logging.getLogger(__name__)


_CONTENT_FIELDS = ("content", "search", "replace", "diff")


class StaleWriteError(PatchPreflightFailed):
    """An agent tried to edit a file another agent changed after its last read
    (spec §7.4). Carries a STALE_READ issue so the loop's PATCH FAILED branch gives the
    re-read guidance, and `writer_label` so the durable record can name the writer."""

    def __init__(self, path: str, message: str, *, writer_label: str = "") -> None:
        super().__init__(message, [PatchPreflightIssue(
            code=PatchFailureCode.STALE_READ, file=path, message=message)])
        self.path = path
        self.writer_label = writer_label


def _same_bytes(real_file: Path, shadow_file: Path) -> bool:
    """True when the edit left this file exactly as it is in real (both absent counts)."""
    if real_file.exists() != shadow_file.exists():
        return False
    return not real_file.exists() or real_file.read_bytes() == shadow_file.read_bytes()


def _looks_double_escaped(text: str) -> bool:
    """Detects a model double-escaping its own JSON string content: technically
    valid JSON (parses fine, triggers no retry) where the value holds literal
    backslash-n / backslash-t two-character sequences instead of real newline/tab
    characters — e.g. the model wrote `"content": "line1\\\\nline2"` (an escaped
    backslash + 'n') instead of `"content": "line1\\nline2"` (an escaped newline).
    Confirmed live 2026-07-13: a Nemotron-via-Ollama response's `patch_ops[].content`
    already had this shape in the debug artifact BEFORE any Crucible code touched
    it — not a parsing bug on our side, the model over-escaped under its own steam.
    Silently writing this produces a real file with zero actual newlines (one giant
    line of escaped text) with no exception anywhere and a false "Applied" success.

    Heuristic: no real newline anywhere, but several literal escape-sequence
    markers — a legitimate multi-line file essentially always has real newlines;
    one that has none but "contains" repeated textual \\n/\\t markers is almost
    certainly this failure mode, not intentional single-line content."""
    if "\n" in text:
        return False
    literal_escapes = text.count("\\n") + text.count("\\t")
    return literal_escapes >= 3


def _validate_patch_ops(patch_ops: list[dict[str, object]]) -> None:
    """Reject structurally malformed ops BEFORE any file is touched.

    Weak models (qwen3-class) frequently swap the 'file' and 'content' fields —
    putting the file body in 'file'. POSIX allows newlines in filenames, so an
    unvalidated op would create a garbage-named file and (auto-accept) promote it
    to the real workspace under a false "success". Raising here routes the failure
    back through the loop's PATCH FAILED branch with actionable guidance instead.
    """
    if not patch_ops:
        raise ValueError("no patch_ops were emitted — emit at least one op.")
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
        for field in _CONTENT_FIELDS:
            value = op.get(field)
            if isinstance(value, str) and _looks_double_escaped(value):
                raise ValueError(
                    f"'{field}' looks double-escaped: it contains literal \\n/\\t sequences "
                    "(a backslash character followed by 'n' or 't') instead of real newline/tab "
                    "characters, with no real newline anywhere. Emit the actual characters your "
                    "JSON string value should decode to — do NOT escape newlines/tabs a second "
                    "time inside the string."
                )


class TurnEditSession:
    def __init__(
        self,
        *,
        turn_id: str,
        real_path: Path,
        workspace_manager: ShadowWorkspaceManager,
        patch_engine: PatchEngine,
        checkpoint_cb: Callable[[list[str]], None] | None = None,
        write_guard: WriteGuard | None = None,
        protection: MainProtection | AgentProtection | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._real = real_path
        self._wm = workspace_manager
        self._patch = patch_engine
        self._shadow: Path | None = None
        self._pending_touched: list[str] = []
        # Rewind capture. Called BEFORE _ensure_shadow so the copy is taken while the
        # real workspace is still the clean before-state (the `shadow == real` invariant
        # in this module's docstring). Sync, like the shutil copies in _ensure_shadow.
        self._checkpoint_cb = checkpoint_cb
        self._write_guard = write_guard
        # Control-plane files (spec §3.9): an agent is refused, the main agent must review.
        self._protection = protection or MainProtection()

    def _raise_if_stale(self, paths: list[str]) -> None:
        """Refuse the first path another agent promoted after this agent last saw it.
        Synchronous on purpose: callers pair it with the promote/patch that follows
        with no await in between (spec §7.4)."""
        guard = self._write_guard
        if guard is None:
            return
        for raw in paths:
            key = canonical_path(self._real, raw)
            if key is None:
                continue
            writer = guard.log.stale_writer(guard.agent_id, key)
            if writer is None:
                continue
            guard.log.note_stale_refusal(guard.agent_id)
            logger.info("[subagent] stale-refusal path=%s by=%s agent=%s",
                        key, writer.label, guard.agent_id)
            raise StaleWriteError(
                key,
                f"`{key}` was modified by agent `{writer.label}` ({writer.name}) after "
                "your last read — read it again before editing.",
                writer_label=writer.label)
        for raw in paths:
            key = canonical_path(self._real, raw)
            if key is None:
                continue
            if guard.log.changed_outside(guard.agent_id, key, file_digest(self._real / key)):
                guard.log.note_stale_refusal(guard.agent_id)
                raise StaleWriteError(
                    key,
                    f"STALE_READ: `{key}` was changed outside the agents (by you or a tool) "
                    "since you last read it. Re-read it before editing.",
                    writer_label="outside the agents")

    async def _ensure_shadow(self, touched: list[str]) -> Path:
        if self._shadow is None:
            sw = await self._wm.prepare_lightweight(
                f"chatturn-{self._turn_id}", str(self._real), touched
            )
            self._shadow = Path(sw.shadow_path)
            return self._shadow
        # Re-seed EVERY touched file from real on every apply, not only on first touch:
        # real may have changed since this shadow last held the file (a formatter run via
        # run_command, or another agent's promote), and patching a stale copy would
        # promote over the newer content — a silent lost update (spec §7.5). A file that
        # no longer exists in real loses its stale shadow copy, so create_file works.
        for rel in touched:
            src, dst = self._real / rel, self._shadow / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        return self._shadow

    async def apply(self, patch_ops: list[dict[str, object]]) -> list[DiffEntry]:
        _validate_patch_ops(patch_ops)
        touched = [str(op["file"]) for op in patch_ops if "file" in op]
        self._protection.check_apply(self._keys(touched))
        self._raise_if_stale(touched)
        if self._checkpoint_cb is not None:
            self._checkpoint_cb(touched)
        shadow = await self._ensure_shadow(touched)
        applied = await apply_ops(self._patch, shadow, patch_ops, allowed_files=set(touched))
        # Only files whose bytes actually changed go forward. An unchanged file must not be
        # promoted, shown as a diff, or recorded as written: a recorded no-op promote
        # reports false work in files_changed and makes siblings' edits of that file stale.
        # Its shadow copy already equals real, so dropping it keeps shadow == real.
        changed = [rel for rel in applied if not _same_bytes(self._real / rel, shadow / rel)]
        if not changed:
            self._pending_touched = []
            files = ", ".join(applied) or "(no files)"
            message = f"Edit changes nothing: {files} already has exactly this content."
            raise PatchPreflightFailed(message, [PatchPreflightIssue(
                code=PatchFailureCode.NO_OP, file=applied[0] if applied else None,
                message=message)])
        self._pending_touched = changed
        return compute_diff_entries(self._real, shadow, changed, self._turn_id)

    @property
    def requires_review(self) -> bool:
        """The last apply touched a protected path: it must be shown for review (§3.9)."""
        return self._protection.requires_review

    def _keys(self, paths: list[str]) -> list[str]:
        return [k for k in (canonical_path(self._real, p) for p in paths) if k is not None]

    async def accept(self) -> None:
        assert self._shadow is not None
        try:
            # Re-checked here: a symlink created after apply could redirect the promote.
            self._protection.check_accept(self._keys(self._pending_touched))
        except ProtectedPathError:
            await self.reject()
            raise
        if self._write_guard is not None:
            try:
                # Check 2: a sibling may have promoted one of these files while this edit
                # was held at a review gate. Check + promote below are await-free.
                self._raise_if_stale(self._pending_touched)
            except StaleWriteError:
                await self.reject()  # restore the shadow from real (shadow == real)
                raise
        promote_files(self._shadow, self._real, self._pending_touched)
        if self._write_guard is not None:
            guard = self._write_guard
            keys = [k for k in (canonical_path(self._real, p) for p in self._pending_touched)
                    if k is not None]
            guard.log.note_promote(guard.agent_id, guard.label, guard.name, keys,
                                   {k: file_digest(self._real / k) for k in keys})
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
