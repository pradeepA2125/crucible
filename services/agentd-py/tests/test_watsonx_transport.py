from __future__ import annotations

import pytest

import agentd.providers.watsonx_transport as watsonx_transport_module
from agentd.providers.watsonx_transport import WatsonxJsonTransport


class FakeGenParams:
    DECODING_METHOD = "decoding_method"
    MAX_NEW_TOKENS = "max_new_tokens"
    MIN_NEW_TOKENS = "min_new_tokens"


class FakeCredentials:
    def __init__(self, *, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key


class FakeModelInference:
    response_text = "# Plan\n\n- Add route"
    calls: list[dict[str, object]] = []

    def __init__(
        self,
        *,
        model_id: str,
        params: dict[str, object],
        credentials: object,
        project_id: str | None,
        space_id: str | None,
    ) -> None:
        self.model_id = model_id
        self.params = params
        self.credentials = credentials
        self.project_id = project_id
        self.space_id = space_id

    def generate_text(self, *, prompt: str) -> str:
        FakeModelInference.calls.append(
            {
                "model_id": self.model_id,
                "params": self.params,
                "project_id": self.project_id,
                "space_id": self.space_id,
                "prompt": prompt,
            }
        )
        return self.response_text


@pytest.mark.asyncio
async def test_watsonx_transport_generate_text_returns_output(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeModelInference.calls.clear()
    monkeypatch.setattr(watsonx_transport_module, "Credentials", FakeCredentials)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", FakeModelInference)
    monkeypatch.setattr(watsonx_transport_module, "GenParams", FakeGenParams)

    transport = WatsonxJsonTransport(
        api_key="test-key",
        project_id="project-1",
        url="https://watsonx.example",
    )

    output = await transport.generate_text(
        model="ibm/granite",
        system_instructions="plan",
        user_payload={"task_id": "task-1"},
    )

    assert output == "# Plan\n\n- Add route"
    assert FakeModelInference.calls
    call = FakeModelInference.calls[0]
    assert call["model_id"] == "ibm/granite"
    assert "plan" in str(call["prompt"])


def test_watsonx_transport_requires_project_or_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watsonx_transport_module, "Credentials", FakeCredentials)
    monkeypatch.setattr(watsonx_transport_module, "ModelInference", FakeModelInference)
    monkeypatch.setattr(watsonx_transport_module, "GenParams", FakeGenParams)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    monkeypatch.delenv("WATSONX_SPACE_ID", raising=False)

    with pytest.raises(RuntimeError, match="WATSONX_PROJECT_ID or WATSONX_SPACE_ID"):
        WatsonxJsonTransport(api_key="test-key", project_id=None, space_id=None)
