from agentd.chat.controller_factory import is_skills_enabled
from agentd.skills.config import skills_body_max_chars, skills_catalog_max_chars


def test_flag_default_off(monkeypatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SKILLS_ENABLED", raising=False)
    assert is_skills_enabled() is False


def test_flag_truthy(monkeypatch) -> None:
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", v)
        assert is_skills_enabled() is True


def test_flag_explicit_off(monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_SKILLS_ENABLED", "0")
    assert is_skills_enabled() is False


def test_budget_defaults(monkeypatch) -> None:
    monkeypatch.delenv("CRUCIBLE_SKILLS_CATALOG_MAX_CHARS", raising=False)
    monkeypatch.delenv("CRUCIBLE_SKILLS_BODY_MAX_CHARS", raising=False)
    assert skills_catalog_max_chars() == 16000
    assert skills_body_max_chars() == 20000
