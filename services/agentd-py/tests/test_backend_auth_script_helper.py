"""scripts/_backend_auth.py against a real agentd.serve."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tests.test_serve import _handshake, _start, _stop

HELPER = Path(__file__).resolve().parents[3] / "scripts" / "_backend_auth.py"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX")


def _load():
    spec = importlib.util.spec_from_file_location("_backend_auth", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_url_rewrites_localhost() -> None:
    assert _load().backend_url("http://localhost:8000/") == "http://127.0.0.1:8000"


def test_auth_headers_verify_then_return_the_token(tmp_path: Path, monkeypatch) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    try:
        port = _handshake(proc)["port"]
        monkeypatch.setenv("HOME", str(home))
        helper = _load()
        headers = helper.auth_headers(f"http://localhost:{port}")
        token = (home / ".crucible/run" / f"agentd-{port}.token").read_text()
        assert headers == {"Authorization": f"Bearer {token}"}
    finally:
        _stop(proc)


def test_auth_headers_refuse_a_wrong_proof(tmp_path: Path, monkeypatch) -> None:
    proc, home, _ws = _start(tmp_path, "--port", "0")
    try:
        port = _handshake(proc)["port"]
        fake_home = tmp_path / "fake"
        (fake_home / ".crucible/run").mkdir(parents=True, mode=0o700)
        os.chmod(fake_home / ".crucible", 0o700)
        bogus = fake_home / ".crucible/run" / f"agentd-{port}.token"
        bogus.write_text("z" * 43)
        os.chmod(bogus, 0o600)
        monkeypatch.setenv("HOME", str(fake_home))
        helper = _load()
        with pytest.raises(helper.BackendAuthError):
            helper.auth_headers(f"http://127.0.0.1:{port}")
    finally:
        _stop(proc)
