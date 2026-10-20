import logging

from agentd.chat.controller_factory import (
    is_task_subsystem_enabled,
    warn_if_incoherent_flags,
)


def test_defaults_off(monkeypatch):
    monkeypatch.delenv("CRUCIBLE_TASK_SUBSYSTEM", raising=False)
    assert is_task_subsystem_enabled() is False


def test_truthy_values_enable(monkeypatch):
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", v)
        assert is_task_subsystem_enabled() is True


def test_other_values_off(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    assert is_task_subsystem_enabled() is False


def test_warns_when_task_off_and_controller_off(monkeypatch, caplog):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "0")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("test"))
    assert any("incoherent" in r.message.lower() for r in caplog.records)


def test_no_warn_when_task_off_but_controller_on(monkeypatch, caplog):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "0")
    monkeypatch.setenv("CRUCIBLE_CHAT_CONTROLLER", "1")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("test"))
    assert not caplog.records


def test_capability_loading_without_mcp_warns(monkeypatch, caplog):
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "1")
    monkeypatch.setenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", "1")
    monkeypatch.delenv("CRUCIBLE_MCP_ENABLED", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("test"))
    assert any("CRUCIBLE_CAPABILITY_LOADING_ENABLED" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.setenv("CRUCIBLE_MCP_ENABLED", "1")
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("test"))
    assert not any("CRUCIBLE_CAPABILITY_LOADING_ENABLED" in r.message for r in caplog.records)


def test_capability_loading_defaults_on_with_a_kill_switch(monkeypatch):
    from agentd.chat.controller_factory import is_capability_loading_enabled
    monkeypatch.delenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", raising=False)
    assert is_capability_loading_enabled()
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", off)
        assert not is_capability_loading_enabled()


def test_default_capability_loading_without_mcp_does_not_warn(monkeypatch, caplog):
    # MCP is off by default: warning about the default would fire on every startup.
    monkeypatch.setenv("CRUCIBLE_TASK_SUBSYSTEM", "1")
    monkeypatch.delenv("CRUCIBLE_CAPABILITY_LOADING_ENABLED", raising=False)
    monkeypatch.delenv("CRUCIBLE_MCP_ENABLED", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_if_incoherent_flags(logging.getLogger("test"))
    assert not any("CRUCIBLE_CAPABILITY_LOADING_ENABLED" in r.message for r in caplog.records)
