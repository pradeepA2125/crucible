"""CommandRuleStore — per-workspace approved-command persistence + token matching (T3)."""
from pathlib import Path

from agentd.domain.models import CommandRule
from agentd.tools.command_rules import CommandRuleStore


def test_exact_prefix_binary_matching(tmp_path: Path) -> None:
    store = CommandRuleStore(tmp_path)
    store.add(CommandRule(type="exact", value="ruff check .", added_at="t"))
    store.add(CommandRule(type="prefix", value="python -c", added_at="t"))
    store.add(CommandRule(type="binary", value="pytest", added_at="t"))
    store.add(CommandRule(type="prefix", value="cat /etc/passwd", added_at="t"))

    # exact: full token-list equality
    assert store.matches("ruff check .")
    assert not store.matches("ruff check src")            # different token
    assert not store.matches("ruff check . --fix")        # extra token

    # prefix: leading token sublist
    assert store.matches('python -c "print(1)"')          # ["python","-c","print(1)"]
    assert not store.matches("python script.py")          # ["python","script.py"]

    # prefix is token-aware, NOT char startswith — no substring bleed
    assert store.matches("cat /etc/passwd -n")
    assert not store.matches("cat /etc/password-store/secret")

    # binary: basename of first token
    assert store.matches("pytest tests/test_x.py::t")
    assert store.matches("/usr/bin/pytest -q")
    assert not store.matches("pytestx -q")                # basename differs


def test_persist_and_reload(tmp_path: Path) -> None:
    store = CommandRuleStore(tmp_path)
    store.add(CommandRule(type="binary", value="pytest", added_at="t"))
    # A new instance reads the same on-disk file
    reloaded = CommandRuleStore(tmp_path)
    assert reloaded.matches("pytest -q")


def test_add_is_deduped(tmp_path: Path) -> None:
    store = CommandRuleStore(tmp_path)
    store.add(CommandRule(type="binary", value="pytest", added_at="t1"))
    store.add(CommandRule(type="binary", value="pytest", added_at="t2"))
    assert len(store.load()) == 1


def test_empty_command_does_not_match(tmp_path: Path) -> None:
    store = CommandRuleStore(tmp_path)
    store.add(CommandRule(type="prefix", value="anything", added_at="t"))
    assert not store.matches("")


def test_args_form_uses_tokens_directly(tmp_path: Path) -> None:
    """When args is provided, the store treats [command, *args] as tokens
    directly (no re-shlex'ing) — useful for callers that already have split args."""
    store = CommandRuleStore(tmp_path)
    store.add(CommandRule(type="prefix", value="python -c", added_at="t"))
    assert store.matches("python", ["-c", "print(1)"])
    assert not store.matches("python", ["script.py"])
