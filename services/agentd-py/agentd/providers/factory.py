"""Provider transport factory — the one place a (backend, credentials) pair
becomes a transport. Used at app startup (main.py), by POST /v1/providers/validate,
and by the PUT /v1/config/provider hot-swap. Request-supplied credentials override
process env and are held in the transport object only (never persisted/logged)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentd.providers.contracts import ModelJsonTransport

_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-3-flash-preview",
    "huggingface": "deepseek-ai/DeepSeek-R1:fastest",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "stepfun/step-3.5-flash:free",
    "watsonx": "ibm/granite-3-8b-instruct",
    "ollama": "glm-4.7-flash:latest",
    "turboquant": "qwen3.6:35b-a3b-q4_K_M",
    "openai": "gpt-5",
}

MODEL_ENV_VAR: dict[str, str] = {
    "anthropic": "CRUCIBLE_ANTHROPIC_MODEL",
    "gemini": "CRUCIBLE_GEMINI_MODEL",
    "huggingface": "CRUCIBLE_HUGGINGFACE_MODEL",
    "groq": "CRUCIBLE_GROQ_MODEL",
    "openrouter": "CRUCIBLE_OPENROUTER_MODEL",
    "watsonx": "CRUCIBLE_WATSONX_MODEL",
    "ollama": "CRUCIBLE_OLLAMA_MODEL",
    "turboquant": "CRUCIBLE_TURBOQUANT_MODEL",
    "openai": "CRUCIBLE_OPENAI_MODEL",
}

PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "watsonx": "WATSONX_API_KEY",
    "huggingface": "HF_TOKEN",
}


def default_model(backend: str) -> str:
    try:
        return _DEFAULT_MODEL[backend]
    except KeyError:
        raise ValueError(f"Unsupported backend: {backend}") from None


def resolve_model(backend: str) -> str:
    env_var = MODEL_ENV_VAR.get(backend)
    return (os.getenv(env_var) if env_var else None) or default_model(backend)


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float_env(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


_OLLAMA_THINK_LEVELS = frozenset({"low", "medium", "high", "max"})


def _ollama_think_env(env: dict[str, str], name: str) -> bool | str | None:
    """Unset (None) omits Ollama's `think` field entirely — model decides, today's
    behavior. A bool disables/re-enables reasoning for models that honor it; a level
    string ("low"/"medium"/"high"/"max") is the finer-grained form some models (e.g.
    GPT-OSS) require instead of a bool. See OllamaJsonTransport.__init__ for why this
    stays opt-in rather than a default."""
    raw = env.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    if normalized in _OLLAMA_THINK_LEVELS:
        return normalized
    return None


def build_transport(
    backend: str, credentials: dict[str, str] | None = None
) -> ModelJsonTransport:
    env: dict[str, str] = dict(os.environ)
    if credentials:
        env.update(credentials)  # request credential wins; process env untouched

    if backend == "anthropic":
        from agentd.providers.anthropic_transport import AnthropicJsonTransport

        return AnthropicJsonTransport(
            api_key=env.get("ANTHROPIC_API_KEY"),
            endpoint=env.get(
                "CRUCIBLE_ANTHROPIC_ENDPOINT", "https://api.anthropic.com/v1/messages"
            ),
            anthropic_version=env.get("CRUCIBLE_ANTHROPIC_VERSION", "2023-06-01"),
            max_tokens=_int_env(env, "CRUCIBLE_ANTHROPIC_MAX_TOKENS", 4096),
            timeout_sec=_float_env(env, "CRUCIBLE_ANTHROPIC_TIMEOUT_SEC", 60.0),
        )
    if backend == "gemini":
        from agentd.providers.gemini_transport import GeminiJsonTransport

        # Thinking knobs mirror main.py's semantics: thinking_level defaults to
        # "high" for Gemini 3.x when enabled and no explicit budget/level is set
        # (thinking_budget=-1 is the 2.5 dynamic-budget API; 3.x uses levels).
        thinking_level = env.get("CRUCIBLE_GEMINI_THINKING_LEVEL")
        raw_budget = env.get("CRUCIBLE_GEMINI_THINKING_BUDGET")
        thinking_budget = (
            int(raw_budget) if raw_budget and raw_budget.lstrip("-").isdigit() else None
        )
        thinking_enabled = env.get(
            "CRUCIBLE_GEMINI_THINKING_ENABLED", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if thinking_enabled and thinking_budget is None and not thinking_level:
            thinking_level = "high"
        return GeminiJsonTransport(
            api_key=env.get("GEMINI_API_KEY"),
            thinking_enabled=thinking_enabled,
            thinking_budget=thinking_budget,
            thinking_level=thinking_level,
            include_thoughts=env.get("CRUCIBLE_GEMINI_INCLUDE_THOUGHTS", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            timeout_sec=_float_env(env, "CRUCIBLE_GEMINI_TIMEOUT_SEC", 120.0),
            max_retries=_int_env(env, "CRUCIBLE_GEMINI_MAX_RETRIES", 4),
        )
    if backend == "huggingface":
        from agentd.providers.huggingface_transport import HuggingFaceJsonTransport

        seed_raw = env.get("CRUCIBLE_HUGGINGFACE_SEED")
        return HuggingFaceJsonTransport(
            api_key=env.get("HF_TOKEN"),
            max_new_tokens=_int_env(env, "CRUCIBLE_HUGGINGFACE_MAX_NEW_TOKENS", 4096),
            seed=int(seed_raw) if seed_raw and seed_raw.isdigit() else None,
            timeout_sec=_float_env(env, "CRUCIBLE_HUGGINGFACE_TIMEOUT_SEC", 60.0),
        )
    if backend == "groq":
        from agentd.providers.groq_transport import GroqJsonTransport

        return GroqJsonTransport(
            api_key=env.get("GROQ_API_KEY"),
            endpoint=env.get("CRUCIBLE_GROQ_ENDPOINT"),
            max_tokens=_int_env(env, "CRUCIBLE_GROQ_MAX_TOKENS", 4096),
            timeout_sec=_float_env(env, "CRUCIBLE_GROQ_TIMEOUT_SEC", 60.0),
            max_retries=_int_env(env, "CRUCIBLE_GROQ_MAX_RETRIES", 4),
        )
    if backend == "openrouter":
        from agentd.providers.openrouter_transport import OpenRouterJsonTransport

        return OpenRouterJsonTransport(
            api_key=env.get("OPENROUTER_API_KEY"),
            max_tokens=_int_env(env, "CRUCIBLE_OPENROUTER_MAX_TOKENS", 4096),
            json_max_tokens=_int_env(env, "CRUCIBLE_OPENROUTER_JSON_MAX_TOKENS", 16384),
            timeout_sec=_float_env(env, "CRUCIBLE_OPENROUTER_TIMEOUT_SEC", 120.0),
            max_retries=_int_env(env, "CRUCIBLE_OPENROUTER_MAX_RETRIES", 4),
            require_parameters=env.get("CRUCIBLE_OPENROUTER_REQUIRE_PARAMETERS", "true")
            .strip()
            .lower()
            not in ("0", "false", "no", "off"),
        )
    if backend == "watsonx":
        from agentd.providers.watsonx_transport import WatsonxJsonTransport

        return WatsonxJsonTransport(
            api_key=env.get("WATSONX_API_KEY"),
            project_id=env.get("WATSONX_PROJECT_ID"),
            url=env.get("WATSONX_URL"),
            space_id=env.get("WATSONX_SPACE_ID"),
        )
    if backend == "ollama":
        from agentd.providers.ollama_transport import OllamaJsonTransport

        return OllamaJsonTransport(
            host=env.get("OLLAMA_HOST"),
            keep_alive=env.get("CRUCIBLE_OLLAMA_KEEP_ALIVE"),
            timeout_sec=_float_env(env, "CRUCIBLE_OLLAMA_TIMEOUT_SEC", 600.0),
            max_retries=_int_env(env, "CRUCIBLE_OLLAMA_MAX_RETRIES", 4),
            num_ctx=_int_env(env, "CRUCIBLE_OLLAMA_NUM_CTX", 32768),
            json_predict_frac=_float_env(env, "CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC", 0.5),
            think=_ollama_think_env(env, "CRUCIBLE_OLLAMA_THINK"),
            temperature=_float_env(env, "CRUCIBLE_OLLAMA_TEMPERATURE", 0.0),
        )
    if backend == "turboquant":
        from agentd.providers.turboquant_transport import TurboQuantTransport

        return TurboQuantTransport.from_env()
    if backend == "openai":
        from agentd.providers.openai_transport import OpenAIJsonTransport

        return OpenAIJsonTransport(api_key=env.get("OPENAI_API_KEY"))
    raise ValueError(f"Unsupported backend: {backend}")
