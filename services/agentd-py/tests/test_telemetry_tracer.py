"""Tracer lifecycle: off means no import, missing packages warn once, a dead endpoint never
blocks shutdown (observability spec §4, §6.1)."""
from __future__ import annotations

import builtins
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentd.telemetry import tracer
from agentd.telemetry.config import TracingConfig


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    tracer.reset_for_tests()
    yield
    tracer.reset_for_tests()


def test_tracing_off_imports_no_opentelemetry(tmp_path: Path) -> None:
    # FastAPI imports the OpenTelemetry *API* on its own whenever it is installed; what
    # tracing-off must never load is our SDK, exporter and conventions.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRUCIBLE_TRACING")}
    env.update({"CRUCIBLE_LISTEN_PORT": "8999", "CRUCIBLE_SERVE_PID": str(os.getpid()),
                "CRUCIBLE_AUTH_DISABLED": "1"})
    code = ("import sys, agentd.main; "
            "bad = sorted(m for m in sys.modules "
            "if m.startswith(('opentelemetry.sdk', 'opentelemetry.exporter', 'openinference'))); "
            "print('IMPORTED', bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "IMPORTED []" in out.stdout, out.stdout + out.stderr


def test_missing_packages_warns_once_and_stays_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith(("opentelemetry", "openinference")):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    config = TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "1"})
    with caplog.at_level(logging.WARNING, logger="agentd.telemetry.tracer"):
        assert tracer.init_tracing(config, version="0", backend="openai") is False
        assert tracer.init_tracing(config, version="0", backend="openai") is False
    assert tracer.is_active() is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "pip install 'crucible-agentd[telemetry]'" in warnings[0].getMessage()


def test_disabled_config_is_not_active() -> None:
    assert tracer.init_tracing(TracingConfig.from_env({}), version="0", backend="x") is False
    assert tracer.is_active() is False and tracer.get_tracer() is None


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.asyncio
async def test_unreachable_endpoint_never_blocks_shutdown() -> None:
    config = TracingConfig.from_env({
        "CRUCIBLE_TRACING_ENABLED": "1",
        "CRUCIBLE_TRACING_ENDPOINT": f"http://127.0.0.1:{_closed_port()}/v1/traces"})
    assert tracer.init_tracing(config, version="0", backend="openai") is True
    t = tracer.get_tracer()
    assert t is not None
    with t.start_as_current_span("probe"):
        pass
    started = time.monotonic()
    await tracer.shutdown_tracing(timeout_sec=2.0)
    assert time.monotonic() - started < 2.5
    assert tracer.is_active() is False


def test_test_exporter_records_spans() -> None:
    exporter = tracer.install_test_exporter()
    t = tracer.get_tracer()
    assert t is not None and tracer.capture_content() is True
    with t.start_as_current_span("one"):
        pass
    assert [s.name for s in exporter.get_finished_spans()] == ["one"]


def test_process_exit_is_bounded_when_phoenix_is_down(tmp_path: Path) -> None:
    # The await alone returning at 2 s is not enough: asyncio.run and uvicorn join the
    # default executor on teardown, and an exporter retrying a full queue held it ~10 s.
    code = f"""
import asyncio, time
from agentd.telemetry import tracer
from agentd.telemetry.config import TracingConfig

async def main():
    tracer.init_tracing(TracingConfig(True, "http://127.0.0.1:{_closed_port()}/v1/traces",
                                      "crucible", False), version="0", backend="openai")
    t = tracer.get_tracer()
    for i in range(2000):
        with t.start_as_current_span(f"s{{i}}"):
            pass
    await tracer.shutdown_tracing(timeout_sec=2.0)

started = time.monotonic()
asyncio.run(main())
print("ELAPSED", round(time.monotonic() - started, 2))
"""
    started = time.monotonic()
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                         text=True, timeout=60)
    total = time.monotonic() - started
    assert "ELAPSED" in out.stdout, out.stdout + out.stderr
    assert float(out.stdout.split("ELAPSED")[1]) < 3.0, out.stdout
    assert total < 6.0, f"interpreter exit took {total:.1f}s"
