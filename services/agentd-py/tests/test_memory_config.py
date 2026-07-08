from agentd.memory.config import MemoryConfig


def test_from_env_defaults_enabled():
    cfg = MemoryConfig.from_env({})
    assert cfg.enabled is True
    assert cfg.db_path.endswith("memory.sqlite3")
    assert cfg.trigger_frac == 0.65
    assert cfg.hot_token_frac == 0.4
    assert cfg.hot_turns == 10
    assert cfg.window_tokens == 128000


def test_from_env_explicit_disable_still_works():
    cfg = MemoryConfig.from_env({"CRUCIBLE_MEMORY_ENABLED": "false"})
    assert cfg.enabled is False


def test_from_env_overrides():
    cfg = MemoryConfig.from_env(
        {
            "CRUCIBLE_MEMORY_ENABLED": "1",
            "CRUCIBLE_MEMORY_DB_PATH": "/tmp/m.sqlite3",
            "CRUCIBLE_MEMORY_COMPACT_TRIGGER_FRAC": "0.5",
            "CRUCIBLE_MEMORY_HOT_TOKEN_FRAC": "0.25",
            "CRUCIBLE_MEMORY_HOT_TURNS": "4",
            "CRUCIBLE_MEMORY_WINDOW_TOKENS": "8000",
        }
    )
    assert cfg.enabled is True
    assert cfg.db_path == "/tmp/m.sqlite3"
    assert cfg.trigger_frac == 0.5
    assert cfg.hot_token_frac == 0.25
    assert cfg.hot_turns == 4
    assert cfg.window_tokens == 8000
