"""Cached input tokens are counted per agent, thread and team alongside the rest."""
from pathlib import Path

from agentd.chat.storage import ChatThreadStore
from agentd.providers.usage import Usage, UsageMeter


def test_the_meter_counts_cached_tokens() -> None:
    meter = UsageMeter()
    meter.record("a", prompt=100, cached=80)
    meter.record("a", prompt=50, cached=40)
    assert meter.peek("a").cached_tokens == 120
    assert meter.take("a") == Usage(prompt_tokens=150, cached_tokens=120)


def test_threads_store_cached_tokens(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread("/ws", title="t")
    store.add_thread_usage(thread.thread_id, Usage(requests=2, prompt_tokens=20000,
                                                   cached_tokens=17000))
    usage = store.thread_usage(thread.thread_id)
    assert (usage.requests, usage.prompt_tokens, usage.cached_tokens) == (2, 20000, 17000)


def test_an_existing_database_gains_the_column(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "chat.sqlite3"
    ChatThreadStore(db)
    conn = sqlite3.connect(db)
    for table in ("chat_threads", "chat_agents", "teams"):
        cols = [r[1] for r in conn.execute(f"pragma table_info({table})")]  # noqa: S608
        assert "cached_tokens" in cols, table
