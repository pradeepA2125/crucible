"""Tracing switches and the content rule (observability spec §6.1)."""
from __future__ import annotations

import pytest

from agentd.telemetry.config import TracingConfig, is_loopback_endpoint


def test_defaults_are_off_with_phoenix_endpoint() -> None:
    c = TracingConfig.from_env({})
    assert (c.enabled, c.endpoint, c.project) == (
        False, "http://localhost:6006/v1/traces", "crucible")


def test_enabled_by_truthy_value() -> None:
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "yes"}).enabled is True
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "off"}).enabled is False


def test_content_defaults_on_for_loopback_and_off_for_remote() -> None:
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "1"}).capture_content is True
    remote = TracingConfig.from_env({
        "CRUCIBLE_TRACING_ENABLED": "1",
        "CRUCIBLE_TRACING_ENDPOINT": "https://phoenix.example.com/v1/traces"})
    assert remote.capture_content is False


def test_explicit_content_value_wins() -> None:
    assert TracingConfig.from_env({
        "CRUCIBLE_TRACING_ENDPOINT": "https://x.example/v1/traces",
        "CRUCIBLE_TRACING_CONTENT": "1"}).capture_content is True
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_CONTENT": "off"}).capture_content is False


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:6006/v1/traces", True),
    ("http://127.9.9.9:6006/v1/traces", True),
    ("http://[::1]:6006/v1/traces", True),
    ("http://localhost:6006/v1/traces", True),
    ("http://localhost.evil.com/v1/traces", False),
    ("https://phoenix.example.com/v1/traces", False),
    ("not a url", False),
])
def test_loopback(url: str, expected: bool) -> None:
    assert is_loopback_endpoint(url) is expected
