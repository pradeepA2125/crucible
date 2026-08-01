import pytest

from agentd.providers.factory import (
    MODEL_ENV_VAR,
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


def test_openai_compatible_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="CRUCIBLE_OPENAI_COMPAT_BASE_URL"):
        build_transport("openai_compatible")


def test_openai_compatible_has_no_default_model() -> None:
    """A guessed default would fail confusingly at the endpoint instead of clearly here."""
    with pytest.raises(ValueError, match="CRUCIBLE_OPENAI_COMPAT_MODEL"):
        default_model("openai_compatible")


def test_openai_compatible_resolve_model_uses_the_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_model must reach the table entry, not the raising default."""
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    assert resolve_model("openai_compatible") == "nvidia/nemotron-3-ultra-550b-a55b"


def test_default_model_still_returns_for_other_backends() -> None:
    """The openai_compatible branch must not turn every lookup into a raise."""
    assert default_model("openrouter") == "stepfun/step-3.5-flash:free"
    assert default_model("ollama") == "glm-4.7-flash:latest"


def test_openai_compatible_builds_with_base_url_and_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local vLLM / LM Studio have no API key — construction must still succeed."""
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.delenv("CRUCIBLE_OPENAI_COMPAT_API_KEY", raising=False)
    transport = build_transport("openai_compatible")
    assert transport.supports_oneof_grammar is True


def test_openai_compatible_normalizes_a_pasted_endpoint_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CRUCIBLE_OPENAI_COMPAT_BASE_URL", "https://x.test/v1/chat/completions"
    )
    transport = build_transport("openai_compatible")
    assert str(transport._completions._client.base_url).rstrip("/") == "https://x.test/v1"


def test_openai_compatible_honors_token_and_retry_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_MAX_TOKENS", "512")
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_JSON_MAX_TOKENS", "32000")
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_TIMEOUT_SEC", "45")
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_MAX_RETRIES", "1")
    transport = build_transport("openai_compatible")
    assert transport._max_tokens == 512
    assert transport._json_max_tokens == 32000
    assert transport._timeout_sec == 45.0
    assert transport._max_retries == 1


def test_openai_compatible_credentials_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRUCIBLE_OPENAI_COMPAT_BASE_URL", "http://from-env/v1")
    transport = build_transport(
        "openai_compatible",
        credentials={"CRUCIBLE_OPENAI_COMPAT_BASE_URL": "http://from-request/v1"},
    )
    assert transport is not None
    assert "from-request" in str(transport._completions._client.base_url)


def test_openai_compatible_is_registered_in_the_env_tables() -> None:
    assert MODEL_ENV_VAR["openai_compatible"] == "CRUCIBLE_OPENAI_COMPAT_MODEL"
    assert PROVIDER_KEY_ENV["openai_compatible"] == "CRUCIBLE_OPENAI_COMPAT_API_KEY"
