import pytest

from agentd.providers.factory import (
    PROVIDER_KEY_ENV,
    build_transport,
    default_model,
    resolve_model,
)


def test_default_model_known_backends() -> None:
    assert default_model("gemini") == "gemini-3-flash-preview"
    assert default_model("openai") == "gpt-5"
    assert default_model("turboquant") == "qwen3.6:35b-a3b-q4_K_M"


def test_default_model_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported backend"):
        default_model("nope")


def test_resolve_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_GEMINI_MODEL", "gemini-flash-latest")
    assert resolve_model("gemini") == "gemini-flash-latest"
    monkeypatch.delenv("CRUCIBLE_GEMINI_MODEL")
    assert resolve_model("gemini") == "gemini-3-flash-preview"


def test_build_transport_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported backend"):
        build_transport("nope")


def test_build_transport_credentials_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Behavioral: OpenAIJsonTransport raises RuntimeError without a key, so
    # construction succeeding proves the request credential reached it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_transport("openai")
    transport = build_transport("openai", credentials={"OPENAI_API_KEY": "sk-req"})
    assert transport is not None


def test_provider_key_env_covers_cloud_backends() -> None:
    for backend in ("openai", "anthropic", "gemini", "groq", "openrouter",
                    "watsonx", "huggingface"):
        assert backend in PROVIDER_KEY_ENV
    for local in ("ollama", "turboquant"):
        assert local not in PROVIDER_KEY_ENV


def test_build_transport_ollama_default_num_ctx() -> None:
    transport = build_transport("ollama")
    assert transport._num_ctx == 32768
    assert transport._json_num_predict == 16384


def test_build_transport_ollama_default_temperature_zero() -> None:
    assert build_transport("ollama")._temperature == 0.0


def test_build_transport_ollama_honors_temperature_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_OLLAMA_TEMPERATURE", "0.7")
    assert build_transport("ollama")._temperature == 0.7


def test_build_transport_ollama_honors_num_ctx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cloud model with a bigger context window is a pure config change, no code
    edit — this pins CRUCIBLE_OLLAMA_NUM_CTX/CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC reach
    the transport."""
    monkeypatch.setenv("CRUCIBLE_OLLAMA_NUM_CTX", "131072")
    monkeypatch.setenv("CRUCIBLE_OLLAMA_JSON_PREDICT_FRAC", "0.75")
    transport = build_transport("ollama")
    assert transport._num_ctx == 131072
    assert transport._json_num_predict == 98304


def test_build_transport_ollama_think_unset_by_default() -> None:
    transport = build_transport("ollama")
    assert transport._think is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("false", False),
        ("0", False),
        ("true", True),
        ("1", True),
        ("low", "low"),
        ("high", "high"),
        ("garbage", None),
    ],
)
def test_build_transport_ollama_think_env_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool | str | None
) -> None:
    monkeypatch.setenv("CRUCIBLE_OLLAMA_THINK", raw)
    transport = build_transport("ollama")
    assert transport._think == expected


def test_build_transport_openrouter_default_json_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    transport = build_transport("openrouter")
    assert transport._json_max_tokens == 16384
    assert transport._max_tokens == 4096


def test_build_transport_openrouter_honors_json_max_tokens_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CRUCIBLE_OPENROUTER_JSON_MAX_TOKENS", "32000")
    transport = build_transport("openrouter")
    assert transport._json_max_tokens == 32000
