"""A trial's isolated process environment (injection spec §3, observability spec §6.3).

Nothing a trial does may reach the user's real home, databases or trust records: only
provider settings are carried over from the real environment, and every state path points
into the trial."""
from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

# Provider settings a trial needs from the real environment. Every other CRUCIBLE_* variable
# is dropped: an inherited CRUCIBLE_DB_PATH or CRUCIBLE_SHELL_POLICY would point a trial at the
# user's real state or change what it measures.
PROVIDER_ENV = re.compile(
    r"CRUCIBLE_(REASONING_BACKEND|REASONING_EFFORT|CHATGPT_REGISTRATION"
    r"|(OPENAI|OPENAI_COMPAT|ANTHROPIC|CLAUDE|CHATGPT|GEMINI|GROQ|OLLAMA|OPENROUTER|WATSONX"
    r"|HF|HUGGINGFACE|TURBOQUANT)_\w+|MEMORY_WINDOW_TOKENS)"
)
# Kept only by a harness that traces its trials and judges them.
TRACING_ENV = re.compile(r"CRUCIBLE_(TRACING|EVAL_JUDGE)_\w+")


def isolated_environment(
    real_env: Mapping[str, str], home: Path, state_dir: Path, flags: Mapping[str, str], *,
    keep_extra: re.Pattern[str] | None = None,
) -> dict[str, str]:
    """The whole process environment for one trial; `flags` are applied last."""
    real_home = Path(real_env.get("HOME") or Path.home())

    def kept(key: str) -> bool:
        return (not key.startswith("CRUCIBLE_") or PROVIDER_ENV.fullmatch(key) is not None
                or (keep_extra is not None and keep_extra.fullmatch(key) is not None))

    env = {k: v for k, v in real_env.items() if kept(k)}
    env.update({
        "HOME": str(home),
        # Sign-in and model caches stay the user's real ones, by absolute path.
        "CRUCIBLE_CHATGPT_AUTH_DIR": real_env.get(
            "CRUCIBLE_CHATGPT_AUTH_DIR", str(real_home / ".crucible" / "auth" / "chatgpt")),
        "CRUCIBLE_CLAUDE_AUTH_DIR": real_env.get(
            "CRUCIBLE_CLAUDE_AUTH_DIR", str(real_home / ".crucible" / "auth" / "claude")),
        "HF_HOME": real_env.get("HF_HOME", str(real_home / ".cache" / "huggingface")),
        "CRUCIBLE_DB_PATH": str(state_dir / "agentd.sqlite3"),
        "CRUCIBLE_CHAT_DB_PATH": str(state_dir / "chat.sqlite3"),
        "CRUCIBLE_MEMORY_DB_PATH": str(state_dir / "memory.sqlite3"),
        "CRUCIBLE_SHADOW_ROOT": str(state_dir / "shadows"),
        "CRUCIBLE_LOG_FILE": str(state_dir / "agentd.log"),
    })
    env.update(flags)
    return env


@contextmanager
def applied_environment(env: dict[str, str]) -> Iterator[None]:
    """Make `os.environ` exactly `env` for the duration, then restore it."""
    saved = dict(os.environ)
    _swap_environment(env)
    try:
        yield
    finally:
        _swap_environment(saved)


def _swap_environment(env: Mapping[str, str]) -> None:
    # Key by key, never clear(): a background thread (a model warmup reading HOME) must not
    # catch the environment empty and fall back to the real home folder.
    os.environ.update(env)
    for key in [k for k in os.environ if k not in env]:
        del os.environ[key]
