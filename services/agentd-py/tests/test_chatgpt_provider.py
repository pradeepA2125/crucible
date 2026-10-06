"""The `chatgpt` backend in the provider factory."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentd.chatgpt_auth import service as service_module
from agentd.chatgpt_auth.service import ChatGPTAuthService
from agentd.chatgpt_auth.store import CredentialRecord, CredentialStore
from agentd.providers.factory import build_transport, default_model
from agentd.providers.unconfigured import build_transport_or_placeholder


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatGPTAuthService:
    svc = ChatGPTAuthService(store=CredentialStore(tmp_path / "auth"))
    monkeypatch.setattr(service_module, "_SERVICE", svc)
    return svc


def _record(store: CredentialStore) -> str:
    reg = store.new_registration_id()
    store.save(CredentialRecord(
        registration_id=reg, label="a@example.com", issuer="https://auth.openai.com",
        subject="s", client_id="oaiapp_1", ext_agent_host_id=store.host_id(),
        access_token="at", refresh_token="rt", expires_at=time.time() + 3600,
        scopes=["chatgpt.tokens.use.direct"], saved_at=time.time()))
    return reg


def test_chatgpt_has_no_default_model() -> None:
    with pytest.raises(ValueError, match="catalog"):
        default_model("chatgpt")


def test_without_a_sign_in_the_backend_starts_with_a_placeholder(
    service: ChatGPTAuthService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRUCIBLE_CHATGPT_REGISTRATION", raising=False)
    _, error = build_transport_or_placeholder("chatgpt", build_transport)
    assert error is not None and "Continue with ChatGPT" in error
    gone = {"CRUCIBLE_CHATGPT_REGISTRATION": "reg_aaaaaaaaaaaa"}
    _, error = build_transport_or_placeholder("chatgpt", lambda b: build_transport(b, gone))
    assert error is not None and "sign in again" in error


def test_a_registration_builds_a_plan_route_transport_sharing_one_bearer(
    service: ChatGPTAuthService,
) -> None:
    reg = _record(service.store)
    first = build_transport("chatgpt", {"CRUCIBLE_CHATGPT_REGISTRATION": reg})
    second = build_transport("chatgpt", {"CRUCIBLE_CHATGPT_REGISTRATION": reg})
    assert first._plan_route is True  # noqa: SLF001
    assert first._bearer is second._bearer is service.bearer(reg)  # noqa: SLF001
