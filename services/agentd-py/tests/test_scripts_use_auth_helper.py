"""Every dev script that talks to agentd uses the auth helper (spec §3.5)."""
from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
EXCLUDED = {"_backend_auth.py", "_backend_auth.sh", "smoke_clarify_gate.py"}


def test_scripts_use_the_auth_helper() -> None:
    offenders = []
    for path in sorted(SCRIPTS.rglob("*")):
        if not path.is_file() or "node_modules" in path.parts or path.name in EXCLUDED:
            continue
        if path.suffix not in {".py", ".sh", ".mjs", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ("/v1/" in text or "/health" in text) and "_backend_auth" not in text:
            offenders.append(str(path.relative_to(SCRIPTS)))
    assert offenders == []
