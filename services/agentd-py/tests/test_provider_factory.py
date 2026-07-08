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
    assert default_model("turboquant") == "devstral-small-2:24b-q4_k_xl"


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
