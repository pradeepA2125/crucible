"""Apply raw patch ops to a base dir via the candidate path (shared, DRY).

Wraps ops into a PatchDocumentV2 and applies the first candidate. Used by both the
execution ToolLoop (_apply_patch_inline) and the chat controller's TurnEditSession.
"""
from __future__ import annotations

from pathlib import Path

from agentd.domain.models import PatchDocumentV2
from agentd.patch.engine import PatchEngine


async def apply_ops(
    patch_engine: PatchEngine,
    base_dir: Path,
    patch_ops: list[dict[str, object]],
    allowed_files: set[str],
) -> list[str]:
    """Apply patch_ops to base_dir; return the touched relative paths."""
    doc = PatchDocumentV2.model_validate(
        {"candidates": [{"candidate_id": "inline-c1", "patch_ops": patch_ops}]}
    )
    candidate = doc.candidates[0]
    result = await patch_engine.apply_patch_candidate(
        base_dir, candidate, allowed_files=allowed_files
    )
    return list(result.touched_files)
