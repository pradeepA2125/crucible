"""Scoped shadow->real file promotion (free-function form of _partial_promote).

Shared by the orchestrator's step partial-promote and the chat controller's
TurnEditSession instant-promote so there is one copy implementation.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def promote_files(shadow_path: Path, real_path: Path, touched: list[str]) -> None:
    """Copy each touched file from the shadow into the real workspace."""
    for rel in touched:
        src = shadow_path / rel
        dst = real_path / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
