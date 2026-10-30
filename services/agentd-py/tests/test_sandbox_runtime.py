"""Sandbox runtime: availability, the on/off switch on the approval-mode API, the
workspace's hosts and folders, and the Settings routes."""

from __future__ import annotations

import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentd.api.sandbox_routes import build_sandbox_router
from agentd.sandbox.hosts import DEFAULT_HOSTS
from agentd.sandbox.runtime import SandboxRuntime
from agentd.sandbox.store import WorkspaceSandboxStore
from tests.test_approval_mode import write
from tests.test_background_dispatch import _setup


@pytest.mark.asyncio
async def test_probe_unavailable_without_bwrap(tmp_path):
    runtime = SandboxRuntime(tmp_path, platform="linux", which=lambda _name: None)
    await runtime.probe()
    status = runtime.status()
    assert (status.enabled, status.available, status.reason) == (
        True, False, "bubblewrap is not installed")
    assert not runtime.active()


@pytest.mark.asyncio
async def test_probe_native_windows_unsupported(tmp_path):
    runtime = SandboxRuntime(tmp_path, platform="win32")
    await runtime.probe()
    assert runtime.status().reason == "native Windows is not supported"


def test_status_before_probe_is_unavailable(tmp_path):
    status = SandboxRuntime(tmp_path).status()
    assert not status.available and status.reason == "not checked yet"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt probe")
@pytest.mark.asyncio
async def test_probe_macos_available(tmp_path):
    runtime = SandboxRuntime(tmp_path)
    await runtime.probe()
    assert runtime.status().available and runtime.active()
    runtime.enabled = False
    assert not runtime.active()


def test_approval_mode_sets_sandbox_and_bumps_version(tmp_path, monkeypatch):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    ctrl._sandbox = SandboxRuntime(tmp_path)
    first = write(ctrl)
    assert first.sandbox is not None and first.sandbox.enabled
    state = ctrl.approval_state()
    result = ctrl.set_approval_mode(
        auto_accept=False, shell_policy=ctrl._shell_policy,
        resolve_waiting=False, expected_process_id=state.process_id,
        expected_version=state.version, sandbox=False)
    assert result.sandbox is not None and not result.sandbox.enabled
    assert result.version == state.version + 1
    assert not ctrl._sandbox.enabled


def test_sandbox_omitted_keeps_value(tmp_path, monkeypatch):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    ctrl._sandbox = SandboxRuntime(tmp_path)
    ctrl._sandbox.enabled = False
    assert not write(ctrl, auto=True).sandbox.enabled


def test_no_runtime_reports_no_sandbox(tmp_path, monkeypatch):
    ctrl, _, _ = _setup(tmp_path, monkeypatch, [], {})
    assert ctrl.approval_state().sandbox is None


def test_store_round_trip_and_malformed_file(tmp_path):
    store = WorkspaceSandboxStore(tmp_path)
    assert store.hosts() == [] and store.folders() == []
    store.add_host("Example.COM")
    store.add_host("example.com")
    assert store.hosts() == ["example.com"]
    store.remove_host("example.com")
    assert store.hosts() == []
    extra = tmp_path / "builds"
    extra.mkdir()
    store.set_folders([extra])
    assert store.folders() == [extra.resolve()]
    assert json.loads((tmp_path / ".crucible" / "sandbox.json").read_text()) == {
        "writable": [str(extra.resolve())]}
    (tmp_path / ".crucible" / "approved-hosts.json").write_text("{not json")
    assert store.hosts() == []


@pytest.mark.parametrize("bad", ["http://x.com", "x.com:443", "x.com/path", "", "a b"])
def test_store_rejects_bad_hosts(tmp_path, bad):
    with pytest.raises(ValueError):
        WorkspaceSandboxStore(tmp_path).set_hosts([bad])


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(build_sandbox_router(SandboxRuntime(tmp_path),
                                            WorkspaceSandboxStore(tmp_path)))
    return TestClient(app)


def test_get_sandbox_route_shape(tmp_path):
    body = _client(tmp_path).get("/v1/sandbox").json()
    assert set(body) == {"status", "default_hosts", "hosts", "folders"}
    assert body["default_hosts"] == list(DEFAULT_HOSTS)
    assert set(body["status"]) == {"enabled", "available", "reason"}


def test_put_hosts_and_folders(tmp_path):
    client = _client(tmp_path)
    assert client.put("/v1/sandbox/hosts", json={"hosts": ["api.example.com"]}).status_code == 200
    assert client.get("/v1/sandbox").json()["hosts"] == ["api.example.com"]
    assert client.put("/v1/sandbox/hosts", json={"hosts": ["http://bad"]}).status_code == 422
    extra = tmp_path / "builds"
    extra.mkdir()
    assert client.put("/v1/sandbox/folders", json={"folders": [str(extra)]}).status_code == 200


def test_put_folders_rejects_missing_path(tmp_path):
    response = _client(tmp_path).put("/v1/sandbox/folders",
                                     json={"folders": [str(tmp_path / "nope")]})
    assert response.status_code == 422
