"""main.py wiring: the auth middleware is the outermost user middleware (spec §3.3)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CHECK = """
from agentd.auth import AuthMiddleware
from agentd.main import app
assert app.user_middleware, "no user middleware"
assert app.user_middleware[0].cls is AuthMiddleware, app.user_middleware
assert app.router.on_startup[0].__name__ == "_load", app.router.on_startup
print("ok")
"""


def test_auth_is_outermost_and_loads_first(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRUCIBLE_")}
    env.update({"CRUCIBLE_REASONING_BACKEND": "scripted", "CRUCIBLE_MEMORY_ENABLED": "0",
                "CRUCIBLE_WORKSPACE_PATH": str(tmp_path), "HOME": str(tmp_path)})
    out = subprocess.run([sys.executable, "-c", CHECK], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")
